from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import Event
from tests.conftest import SAFARI_IPHONE


def _payload(**overrides):
    payload = {
        "site_id": "blue-mug.example",
        "url": "https://blue-mug.example/products/blue-mug",
        "referrer": "https://www.google.com/search?q=blue+mugs",
        "screen_width": 1280,
    }
    payload.update(overrides)
    return payload


def test_health_reports_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_records_an_enriched_pageview(client, db_session, site):
    response = client.post("/api/event", json=_payload())

    assert response.status_code == 202

    event = db_session.scalars(select(Event)).one()
    assert event.site_id == "blue-mug.example"
    assert event.name == "pageview"
    assert event.pathname == "/products/blue-mug"
    assert event.source == "Google"
    assert event.referrer_host == "google.com"
    assert event.browser == "Chrome"
    assert event.os == "Mac OS X"
    assert event.device == "desktop"
    assert event.screen == "Laptop"
    assert len(event.visitor_id) == 32


def test_records_device_class_from_the_user_agent(client, db_session, site):
    client.post("/api/event", json=_payload(), headers={"user-agent": SAFARI_IPHONE})

    event = db_session.scalars(select(Event)).one()
    assert event.device == "mobile"
    assert event.browser == "Mobile Safari"


def test_repeat_visits_share_one_visitor_id(client, db_session, site):
    client.post("/api/event", json=_payload())
    client.post("/api/event", json=_payload(url="https://blue-mug.example/about"))

    first, second = db_session.scalars(select(Event).order_by(Event.id)).all()
    assert first.visitor_id == second.visitor_id


def test_a_different_browser_is_a_different_visitor(client, db_session, site):
    client.post("/api/event", json=_payload())
    client.post("/api/event", json=_payload(), headers={"user-agent": SAFARI_IPHONE})

    first, second = db_session.scalars(select(Event).order_by(Event.id)).all()
    assert first.visitor_id != second.visitor_id


def test_discards_query_strings(client, db_session, site):
    """Query strings routinely carry personal data and must never be stored."""
    response = client.post(
        "/api/event",
        json=_payload(url="https://blue-mug.example/welcome?email=a@b.com&token=abc123"),
    )

    assert response.status_code == 202
    assert db_session.scalars(select(Event)).one().pathname == "/welcome"


def test_root_path_is_normalised(client, db_session, site):
    client.post("/api/event", json=_payload(url="https://blue-mug.example"))

    assert db_session.scalars(select(Event)).one().pathname == "/"


def test_internal_navigation_is_not_credited_to_a_source(client, db_session, site):
    client.post("/api/event", json=_payload(referrer="https://blue-mug.example/"))

    event = db_session.scalars(select(Event)).one()
    assert event.source == "Direct"
    assert event.referrer_host is None


@pytest.mark.parametrize(
    "user_agent",
    ["Googlebot/2.1 (+http://www.google.com/bot.html)", "curl/8.4.0"],
)
def test_automated_traffic_is_not_recorded(client, db_session, site, user_agent):
    response = client.post("/api/event", json=_payload(), headers={"user-agent": user_agent})

    # Answered exactly like a real browser, so crawlers learn nothing.
    assert response.status_code == 202
    assert db_session.scalars(select(Event)).all() == []


def test_rejects_relative_url(client, db_session):
    response = client.post("/api/event", json=_payload(url="/products/blue-mug"))

    assert response.status_code == 422
    assert db_session.scalars(select(Event)).all() == []


def test_rejects_missing_site_id(client, db_session):
    payload = _payload()
    del payload["site_id"]

    response = client.post("/api/event", json=payload)

    assert response.status_code == 422
    assert db_session.scalars(select(Event)).all() == []


def test_events_for_an_unregistered_domain_are_dropped(client, db_session):
    """Otherwise the collector is an open write endpoint for anybody."""
    response = client.post("/api/event", json=_payload(site_id="not-mine.example"))

    # Same answer a registered site gets, so nobody can probe which domains
    # are tracked here.
    assert response.status_code == 202
    assert db_session.scalars(select(Event)).all() == []


def test_the_domain_is_normalised_before_storage(client, db_session, site):
    client.post("/api/event", json=_payload(site_id="https://www.blue-mug.example"))

    assert db_session.scalars(select(Event)).one().site_id == "blue-mug.example"


def test_the_exact_viewport_width_is_bucketed_not_stored(client, db_session, site):
    """A precise width is strong fingerprinting material; the bucket is not."""
    client.post("/api/event", json=_payload(screen_width=1437))

    event = db_session.scalars(select(Event)).one()
    assert event.screen == "Laptop"
    assert "1437" not in str(event.__dict__)


def test_a_missing_width_still_records_the_event(client, db_session, site):
    client.post("/api/event", json=_payload(screen_width=None))

    assert db_session.scalars(select(Event)).one().screen == "Unknown"


def test_a_custom_event_is_recorded_under_its_own_name(client, db_session, site):
    client.post("/api/event", json=_payload(name="signup"))

    event = db_session.scalars(select(Event)).one()
    assert event.name == "signup"
    assert event.pathname == "/products/blue-mug"


def test_an_overlong_event_name_is_rejected(client, db_session, site):
    response = client.post("/api/event", json=_payload(name="x" * 65))

    assert response.status_code == 422
    assert db_session.scalars(select(Event)).all() == []


def test_a_buffered_collector_hands_the_event_to_the_writer(client, db_session, site):
    """With buffering on, the request thread must not touch the database."""
    submitted = []
    client.app.state.event_writer = SimpleNamespace(submit=submitted.append)

    response = client.post("/api/event", json=_payload())

    assert response.status_code == 202
    assert len(submitted) == 1
    assert submitted[0]["pathname"] == "/products/blue-mug"
    # Nothing was written synchronously; the writer thread owns that now.
    assert db_session.scalars(select(Event)).all() == []
