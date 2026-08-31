import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import Event
from app.services import accounts
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


@pytest.mark.parametrize("name", ["", "   ", "\t\n"])
def test_a_blank_event_name_is_rejected(client, db_session, site, name):
    """It would otherwise show up in the goals report as an empty row."""
    response = client.post("/api/event", json=_payload(name=name))

    assert response.status_code == 422
    assert db_session.scalars(select(Event)).all() == []


def test_an_event_name_is_trimmed(client, db_session, site):
    client.post("/api/event", json=_payload(name="  signup  "))

    assert db_session.scalars(select(Event)).one().name == "signup"


def test_a_long_domain_can_still_send_events(client, db_session, account):
    """The payload cap used to be shorter than the column, so it could not."""
    long_domain = ("a" * 60 + ".") * 3 + "example.com"
    accounts.add_site(db_session, owner=account, domain=long_domain)

    response = client.post(
        "/api/event",
        json=_payload(site_id=long_domain, url=f"https://{long_domain}/welcome"),
    )

    assert response.status_code == 202
    assert db_session.scalars(select(Event)).one().site_id == long_domain


def test_campaign_tags_are_recorded(client, db_session, site):
    client.post(
        "/api/event",
        json=_payload(
            url="https://blue-mug.example/sale?utm_source=newsletter"
            "&utm_medium=email&utm_campaign=spring",
            referrer=None,
        ),
    )

    event = db_session.scalars(select(Event)).one()
    assert (event.source, event.medium, event.campaign) == ("newsletter", "email", "spring")


def test_a_campaign_tag_beats_the_referrer(client, db_session, site):
    """The tag is a deliberate statement about the visit; the referrer is not."""
    client.post(
        "/api/event",
        json=_payload(
            url="https://blue-mug.example/sale?utm_source=newsletter",
            referrer="https://www.google.com/search?q=mugs",
        ),
    )

    assert db_session.scalars(select(Event)).one().source == "newsletter"


def test_the_referrer_still_wins_when_there_is_no_tag(client, db_session, site):
    client.post("/api/event", json=_payload(referrer="https://www.google.com/"))

    assert db_session.scalars(select(Event)).one().source == "Google"


def test_the_rest_of_the_query_is_still_thrown_away(client, db_session, site):
    client.post(
        "/api/event",
        json=_payload(
            url="https://blue-mug.example/sale?utm_source=hn&email=a@b.com&token=s3cr3t"
        ),
    )

    event = db_session.scalars(select(Event)).one()
    stored = str(event.__dict__)
    assert event.pathname == "/sale"
    assert "a@b.com" not in stored
    assert "s3cr3t" not in stored


def test_ordinary_traffic_carries_no_campaign(client, db_session, site):
    client.post("/api/event", json=_payload())

    event = db_session.scalars(select(Event)).one()
    assert event.medium is None
    assert event.campaign is None


@pytest.mark.parametrize(
    "content_type",
    [
        # What sendBeacon must use: anything outside the CORS safelist makes the
        # request preflighted and credentialed, and a browser then refuses it
        # against a wildcard origin -- so no event ever arrives from a customer
        # site. This is the whole reason the collector parses the body itself.
        "text/plain",
        "text/plain;charset=UTF-8",
        # Still accepted, for anything posting to the API directly.
        "application/json",
    ],
)
def test_a_json_body_is_read_whatever_it_is_labelled(client, db_session, site, content_type):
    response = client.post(
        "/api/event",
        content=json.dumps(_payload()),
        headers={"content-type": content_type},
    )

    assert response.status_code == 202
    assert db_session.scalars(select(Event)).one().pathname == "/products/blue-mug"


def test_a_body_that_is_not_json_is_still_a_422(client, site):
    response = client.post(
        "/api/event", content="not json at all", headers={"content-type": "text/plain"}
    )

    assert response.status_code == 422


def test_a_json_body_missing_required_fields_is_still_a_422(client, db_session, site):
    response = client.post(
        "/api/event", content='{"url": "https://blue-mug.example/"}',
        headers={"content-type": "text/plain"},
    )

    assert response.status_code == 422
    assert db_session.scalars(select(Event)).all() == []


def test_the_tracking_script_labels_its_body_safelisted():
    """A guard on the other half of the fix.

    The server accepting text/plain is useless if the script goes back to
    announcing application/json, and that failure is invisible unless the page
    is served from a different origin than the collector.
    """
    script = (Path(__file__).parent.parent / "static" / "beacon.js").read_text(encoding="utf-8")

    assert 'type: "text/plain"' in script
    assert '"Content-Type": "text/plain"' in script
    # Quoted, so the comment explaining why it must not be used does not match.
    assert '"application/json"' not in script


def test_the_tracking_script_offers_an_opt_out():
    """Exempt from consent still means the visitor must be able to refuse.

    The script only reads the flag; writing it belongs to the site, which keeps
    the script itself free of any device storage.
    """
    script = (Path(__file__).parent.parent / "static" / "beacon.js").read_text(encoding="utf-8")

    assert 'localStorage.getItem("beacon_ignore")' in script
    assert "doNotTrack" in script
    # Reading only: writing would be storage, and storage is what would drag
    # this back under the consent rule it is exempt from.
    assert "localStorage.setItem" not in script


def test_the_public_api_survives_every_reason_the_script_gives_up():
    """beacon("signup") must not throw on a page that is not being counted.

    The API was installed at the bottom of the script, after the checks for Do
    Not Track, the opt-out flag and a missing site id had already returned. A
    site following the documented API therefore threw a ReferenceError on the
    host page -- for precisely the visitors who had asked not to be counted,
    and on a project whose stated rule is that analytics must never break the
    site it measures.

    Asserted structurally rather than by running the script, because there is
    no JavaScript toolchain here and this is the property that matters: the
    assignment comes first.
    """
    script = (Path(__file__).parent.parent / "static" / "beacon.js").read_text(encoding="utf-8")

    installed = script.index("window.beacon = function")
    for bail_out in (
        "document.currentScript",
        "data-site-id",
        "doNotTrack",
        'localStorage.getItem("beacon_ignore")',
    ):
        assert installed < script.index(bail_out), f"{bail_out} can return before the API exists"


def _widest_possible_payload():
    """Every free-text field pushed to the largest the schema will accept."""
    return _payload(
        url="https://blue-mug.example/" + "a" * 1500,
        referrer="https://" + "b" * 400 + "/somewhere",
    )


def test_every_stored_string_fits_the_column_that_holds_it(client, site, db_session):
    """SQLite ignores VARCHAR lengths and Postgres enforces them.

    So a value that is too long is accepted in development and rejected in
    production -- the failure mode this project has already been bitten by
    once, when site_id was capped at 64 against a column of 253. Two more had
    gone the same way: a URL may be 2,048 characters and its path went into a
    VARCHAR(1024), and a referring host went in uncapped against a VARCHAR(255).

    Written against the table rather than against a list of field names, so a
    column added or narrowed later is covered without anyone remembering to
    come back here.
    """
    from sqlalchemy import String

    response = client.post("/api/event", json=_widest_possible_payload())
    assert response.status_code == 202

    event = db_session.scalars(select(Event)).one()
    oversized = [
        (column.name, len(value), column.type.length)
        for column in Event.__table__.columns
        if isinstance(column.type, String)
        and (value := getattr(event, column.name)) is not None
        and len(value) > column.type.length
    ]

    assert oversized == [], f"values too long for their columns: {oversized}"


def test_an_over_long_path_is_trimmed_rather_than_dropped(client, site, db_session):
    """A truncated path still answers "which page"; a dropped event answers nothing.

    Which is why this trims instead of refusing: the visitor cannot resend, and
    with the ingest buffer on, one rejected row fails the whole batch it is
    travelling in.
    """
    client.post("/api/event", json=_payload(url="https://blue-mug.example/" + "a" * 1500))

    event = db_session.scalars(select(Event)).one()
    assert len(event.pathname) == 1024
    assert event.pathname.startswith("/aaa")


def test_an_over_long_referring_host_is_trimmed(client, site, db_session):
    """It reaches two columns, and reached both of them uncapped."""
    client.post(
        "/api/event",
        json=_payload(referrer="https://" + "b" * 400 + "/somewhere"),
    )

    event = db_session.scalars(select(Event)).one()
    assert len(event.referrer_host) == 255
    assert len(event.source) == 255


@pytest.mark.parametrize(
    ("url", "why"),
    [
        ("https://spam.example.net/buy-cheap-things", "an unrelated host"),
        ("https://notblue-mug.example/x", "a host that merely ends with the domain"),
        ("https://blue-mug.example.evil.test/x", "the domain used as a prefix"),
        ("https://evil.test/?r=blue-mug.example", "the domain hidden in a query string"),
    ],
)
def test_a_stranger_cannot_write_into_someone_elses_dashboard(
    client, site, db_session, url, why
):
    """The site ID is public -- it is in the snippet on every page.

    So the ID alone cannot be the authorisation to write, and before this the
    only other gate was "is the domain registered", which is equally public. A
    single curl put a page called /buy-cheap-things into a real dashboard,
    attributed to whatever referrer it fancied.

    Requiring the reported URL to be on the domain it claims does not stop
    someone who fills the URL in correctly, and nothing here can: a public
    collector cannot hold a secret that the browser does not also hand out.
    What it stops is the two things that actually happen -- a snippet copied
    onto another site quietly filing its traffic here, and spam that scrapes
    IDs without troubling to match the host.
    """
    response = client.post("/api/event", json=_payload(url=url))

    assert response.status_code == 202, f"the answer changed for {why}"
    assert db_session.scalars(select(Event)).all() == [], f"{why} was stored"


@pytest.mark.parametrize(
    ("url", "why"),
    [
        ("https://blue-mug.example/kitchen", "the domain itself"),
        ("https://www.blue-mug.example/kitchen", "the www. form"),
        ("https://BLUE-MUG.EXAMPLE/kitchen", "shouted"),
        ("https://blue-mug.example:8443/kitchen", "a non-default port"),
        ("http://blue-mug.example/kitchen", "plain http"),
        ("https://blog.blue-mug.example/kitchen", "a subdomain"),
        ("https://shop.eu.blue-mug.example/kitchen", "a deeper subdomain"),
    ],
)
def test_the_site_and_its_subdomains_still_report_normally(client, site, db_session, url, why):
    """The check is worthless if it also drops the traffic it exists to protect.

    Subdomains count: someone who registers example.com and tracks
    blog.example.com is tracking their own site, and anyone who can serve a
    page from a subdomain is already inside the domain.
    """
    response = client.post("/api/event", json=_payload(url=url))

    assert response.status_code == 202
    stored = db_session.scalars(select(Event)).all()
    assert len(stored) == 1, f"{why} was dropped"
    assert stored[0].pathname == "/kitchen"


def test_a_refused_url_is_answered_exactly_like_an_accepted_one(client, site):
    """Otherwise the response maps the rule, and spam just tunes itself to it.

    Same status, same body -- the collector already answers unregistered
    domains and crawlers this way for the same reason.
    """
    accepted = client.post("/api/event", json=_payload(url="https://blue-mug.example/a"))
    refused = client.post("/api/event", json=_payload(url="https://evil.test/a"))

    assert (accepted.status_code, accepted.json()) == (refused.status_code, refused.json())


@pytest.mark.parametrize(
    ("url", "belongs", "why"),
    [
        ("https://blue-mug.example/a", True, "the domain itself"),
        ("https://www.blue-mug.example/a", True, "the www. form"),
        ("https://blue-mug.example.:443/a", True, "a fully-qualified name with its trailing dot"),
        ("https://user:pw@blue-mug.example/a", True, "credentials in the URL are not the host"),
        ("https://shop.blue-mug.example/a", True, "a subdomain"),
        ("https://notblue-mug.example/a", False, "the suffix trap the leading dot exists for"),
        ("https://blue-mug.example.evil.test/a", False, "the domain used as a prefix"),
        ("/a", False, "a relative path names no host"),
        ("", False, "nothing at all"),
        ("https:///a", False, "a URL with an empty host"),
    ],
)
def test_whether_a_url_belongs_to_a_site(url, belongs, why):
    """Tested directly as well as through the endpoint.

    The schema already refuses anything that is not an absolute http(s) URL, so
    the hostless cases below cannot arrive over HTTP today. They are checked
    here because `belongs_to` is what decides, and it should not depend on
    another layer having gone first to be safe.
    """
    from app.services.urls import belongs_to

    assert belongs_to(url, "blue-mug.example") is belongs, why


def test_a_flood_of_spoofed_events_writes_one_log_line(client, site, caplog):
    """The sender picks the rate, so the log volume cannot be per-event.

    A warning an attacker can print a million times is a way to fill a disk.
    One line carries everything a million would for the case it exists to serve
    -- an owner working out why their dashboard is empty.
    """
    import logging

    with caplog.at_level(logging.WARNING, logger="app.routers.ingest"):
        for i in range(25):
            client.post("/api/event", json=_payload(url=f"https://evil.test/{i}"))

    warnings = [r for r in caplog.records if "not on that domain" in r.getMessage()]
    assert len(warnings) == 1, f"25 spoofed events wrote {len(warnings)} lines"
    assert "blue-mug.example" in warnings[0].getMessage()
