from fastapi import APIRouter, Request, status

from app.dependencies import DbSession
from app.models import Event
from app.schemas import EventIn
from app.services import accounts
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

    event = Event(
        site_id=domain,
        name=payload.name,
        pathname=pathname_of(payload.url),
        visitor_id=visitor_id(
            salt=current_salt(db),
            site_id=domain,
            ip=address,
            user_agent=user_agent,
        ),
        referrer_host=referrer_host,
        source=source,
        browser=client.browser,
        os=client.os,
        device=client.device,
        country=get_country_resolver().country_code(address),
        screen=screen_bucket(payload.screen_width),
    )
    db.add(event)
    db.commit()

    # `address`, `user_agent` and the exact viewport width all go out of scope
    # here, and none of them were persisted.
    return ACCEPTED
