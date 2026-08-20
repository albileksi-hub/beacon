import datetime as dt

from fastapi import APIRouter, Request, status
from sqlalchemy import insert

from app.dependencies import DbSession
from app.models import Event
from app.schemas import EventIn
from app.services import accounts, zones
from app.services.client import client_ip
from app.services.geo import get_country_resolver
from app.services.referrers import classify
from app.services.screens import bucket as screen_bucket
from app.services.urls import pathname_of
from app.services.user_agent import profile
from app.services.visitors import current_salt, visitor_id

router = APIRouter(tags=["ingest"])

ACCEPTED = {"status": "accepted"}


@router.post("/api/event", status_code=status.HTTP_202_ACCEPTED)
def collect_event(payload: EventIn, request: Request, db: DbSession) -> dict[str, str]:
    """Record one interaction.

    Answers 202 rather than 201: the visitor's browser gets an immediate
    acknowledgement and never waits on our storage layer.
    """
    user_agent = request.headers.get("user-agent", "")
    client = profile(user_agent)

    if client.is_bot:
        # Dropped silently, and with the same response a real browser gets, so
        # that a crawler learns nothing about being filtered.
        return ACCEPTED

    domain = accounts.normalise_domain(payload.site_id)
    if not accounts.site_is_registered(db, domain):
        # Unregistered domain: nothing stored, same answer as everything else.
        # Replying differently would let anyone probe which sites are tracked
        # here, and would turn the collector into an open write endpoint.
        return ACCEPTED

    address = client_ip(request)
    referrer_host, source = classify(payload.referrer, payload.url)

    # Which of this site's days the event belongs to is decided here, once, in
    # the site's own zone -- so nothing downstream has to truncate a timestamp.
    occurred = dt.datetime.now(dt.UTC)
    day, hour = zones.local_parts(occurred, accounts.timezone_for(db, domain))

    values = {
        "site_id": domain,
        "name": payload.name,
        "pathname": pathname_of(payload.url),
        "day": day,
        "hour": hour,
        "visitor_id": visitor_id(
            # Rotates at this site's midnight, not at UTC midnight.
            salt=current_salt(db, site_id=domain, day=day),
            site_id=domain,
            ip=address,
            user_agent=user_agent,
        ),
        "referrer_host": referrer_host,
        "source": source,
        "browser": client.browser,
        "os": client.os,
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
