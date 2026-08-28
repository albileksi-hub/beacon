import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import String, insert
from starlette.requests import ClientDisconnect

from app.dependencies import DbSession
from app.models import Event
from app.schemas import EventIn
from app.services import accounts, campaigns, zones
from app.services.client import client_ip
from app.services.geo import get_country_resolver
from app.services.referrers import classify
from app.services.screens import bucket as screen_bucket
from app.services.urls import pathname_of
from app.services.user_agent import profile
from app.services.visitors import current_salt, visitor_id

router = APIRouter(tags=["ingest"])

ACCEPTED = {"status": "accepted"}

# The width of every string column on events, read off the table rather than
# copied next to it. Two of these were wrong: a URL may be 2048 characters and
# its path went into a VARCHAR(1024), and a referring host went into a
# VARCHAR(255) uncapped. SQLite ignores those lengths and Postgres enforces
# them, so both were accepted in development and would have been rejected in
# production -- and with the ingest buffer on, one oversized value fails the
# whole batch it travels in.
_WIDTHS = {
    name: column.type.length
    for name, column in Event.__table__.columns.items()
    if isinstance(column.type, String)
}


def _fit(value: str, column: str) -> str:
    """Trim a derived value to the width of the column that will hold it.

    Trimmed rather than refused, which is what telemetry pipelines do with an
    over-long attribute: a truncated path still answers "which page", while a
    rejected event answers nothing and is unrecoverable. It is also what this
    codebase already does one layer up, where the rollup builder trims a
    dimension value to VALUE_LIMIT.
    """
    return value[: _WIDTHS[column]]


async def event_payload(request: Request) -> EventIn:
    """Read the body as JSON whatever the browser labelled it.

    navigator.sendBeacon can only send a CORS-safelisted content type without
    turning the request into a preflighted, credentialed one -- and a browser
    refuses a credentialed request against a wildcard origin, so the event is
    blocked outright. Every real customer site is cross-origin, so the collector
    accepts text/plain and parses it itself.

    An async dependency rather than an async endpoint: the body is read on the
    event loop, while the handler stays synchronous and keeps running in the
    threadpool, where its blocking database work belongs.
    """
    try:
        raw = await request.body()
    except ClientDisconnect as error:
        # LimitRequestSize hung up on a body that never declared its length and
        # then exceeded the cap. There is no request left to validate, and the
        # honest answer is the one the limiter would have given.
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Request body too large"
        ) from error

    try:
        return EventIn.model_validate_json(raw)
    except ValidationError as error:
        # Re-raised as FastAPI's own, so a malformed payload still answers 422
        # in exactly the shape it always did.
        raise RequestValidationError(error.errors()) from error


EventPayload = Annotated[EventIn, Depends(event_payload)]


@router.post("/api/event", status_code=status.HTTP_202_ACCEPTED)
def collect_event(payload: EventPayload, request: Request, db: DbSession) -> dict[str, str]:
    """Record one interaction.

    Answers 202 rather than 201: the visitor's browser gets an immediate
    acknowledgement and never waits on our storage layer.
    """
    # The cheapest rejection first. Recognising a crawler means matching against
    # 1,500 patterns, and there is no reason to spend that on traffic aimed at a
    # domain nobody registered.
    domain = accounts.normalise_domain(payload.site_id)
    if not accounts.site_is_registered(db, domain):
        # Unregistered domain: nothing stored, same answer as everything else.
        # Replying differently would let anyone probe which sites are tracked
        # here, and would turn the collector into an open write endpoint.
        return ACCEPTED

    user_agent = request.headers.get("user-agent", "")
    client = profile(user_agent)

    if client.is_bot:
        # Dropped silently, and with the same response a real browser gets, so
        # that a crawler learns nothing about being filtered.
        return ACCEPTED

    address = client_ip(request)
    referrer_host, source = classify(payload.referrer, payload.url)

    # A campaign tag is a deliberate statement about where a visit came from,
    # so it wins over whatever the referrer happened to be.
    tags = campaigns.from_url(payload.url)
    if tags.source:
        source = tags.source

    # Which of this site's days the event belongs to is decided here, once, in
    # the site's own zone -- so nothing downstream has to truncate a timestamp.
    occurred = dt.datetime.now(dt.UTC)
    day, hour = zones.local_parts(occurred, accounts.timezone_for(db, domain))

    values = {
        "site_id": domain,
        "name": payload.name,
        "pathname": _fit(pathname_of(payload.url), "pathname"),
        "day": day,
        "hour": hour,
        "visitor_id": visitor_id(
            # Rotates at this site's midnight, not at UTC midnight.
            salt=current_salt(db, site_id=domain, day=day),
            site_id=domain,
            ip=address,
            user_agent=user_agent,
        ),
        "referrer_host": _fit(referrer_host, "referrer_host") if referrer_host else None,
        "source": _fit(source, "source"),
        "medium": tags.medium,
        "campaign": tags.campaign,
        # Bounded in practice by the user-agent dataset, but derived from a
        # header a caller controls, so they go through the same gate.
        "browser": _fit(client.browser, "browser"),
        "os": _fit(client.os, "os"),
        "device": client.device,
        "country": get_country_resolver().country_code(address),
        "screen": screen_bucket(payload.screen_width),
        "timestamp": occurred,
    }

    writer = getattr(request.app.state, "event_writer", None)
    if writer is not None:
        # Buffered: the write happens on the writer thread, off this request.
        writer.submit(values)
    else:
        db.execute(insert(Event), [values])
        db.commit()

    # `address`, `user_agent` and the exact viewport width all go out of scope
    # here, and none of them were persisted.
    return ACCEPTED
