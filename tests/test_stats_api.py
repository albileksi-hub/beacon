import datetime as dt

from app.models import Event
from app.services import accounts
from tests.conftest import OWNER_PASSWORD, SITE_DOMAIN, with_local_bucket


def add_event(db, **overrides):
    # Timestamped against the real clock, because the endpoints resolve their
    # own window rather than taking one from the caller.
    defaults = {
        "site_id": SITE_DOMAIN,
        "visitor_id": "visitor-1",
        "pathname": "/",
        "timestamp": dt.datetime.now(dt.UTC),
        "name": "pageview",
        "source": "Google",
        "browser": "Chrome",
        "os": "Windows",
        "device": "desktop",
        "country": "DE",
        "screen": "Desktop",
    }
    db.add(Event(**with_local_bucket(defaults | overrides)))
    db.commit()


def test_summary_endpoint(signed_in, db_session, rebuild_rollups, site):
    add_event(db_session, visitor_id="a")
    add_event(db_session, visitor_id="a", pathname="/about")
    add_event(db_session, visitor_id="b")

    rebuild_rollups()

    response = signed_in.get(f"/api/stats/{SITE_DOMAIN}/summary")

    assert response.status_code == 200
    # "b" read one page and left; "a" read two. One bounce out of two visitors.
    assert response.json() == {
        "visitors": 2,
        "pageviews": 3,
        "views_per_visitor": 1.5,
        "bounce_rate": 50.0,
    }


def test_timeseries_endpoint_returns_a_full_series(signed_in, db_session, rebuild_rollups, site):
    add_event(db_session, visitor_id="a")

    rebuild_rollups()

    response = signed_in.get(f"/api/stats/{SITE_DOMAIN}/timeseries", params={"period": "30d"})

    assert response.status_code == 200
    points = response.json()
    assert len(points) == 30
    assert sum(point["visitors"] for point in points) == 1
    assert set(points[0]) == {"bucket", "visitors", "pageviews"}


def test_breakdown_endpoint(signed_in, db_session, rebuild_rollups, site):
    add_event(db_session, visitor_id="a", pathname="/popular")
    add_event(db_session, visitor_id="b", pathname="/popular")
    add_event(db_session, visitor_id="c", pathname="/quiet")

    rebuild_rollups()

    response = signed_in.get(f"/api/stats/{SITE_DOMAIN}/breakdown/page")

    assert response.status_code == 200
    assert response.json() == [
        {"value": "/popular", "visitors": 2, "pageviews": 2},
        {"value": "/quiet", "visitors": 1, "pageviews": 1},
    ]


def test_live_endpoint(signed_in, db_session, rebuild_rollups, site):
    add_event(db_session, visitor_id="a")

    rebuild_rollups()

    response = signed_in.get(f"/api/stats/{SITE_DOMAIN}/live")

    assert response.status_code == 200
    assert response.json() == {"visitors": 1, "window_minutes": 5}


def test_a_private_site_is_invisible_to_a_stranger(client, site):
    """404 rather than 401: the answer must not reveal that the site exists."""
    assert client.get(f"/api/stats/{SITE_DOMAIN}/summary").status_code == 404


def test_a_site_you_do_not_own_is_a_404_not_a_403(signed_in, db_session, rebuild_rollups):
    """A 403 would confirm the domain exists here, which enumerates customers."""
    stranger = accounts.register(db_session, email="stranger@example.com", password=OWNER_PASSWORD)
    accounts.add_site(db_session, owner=stranger, domain="not-yours.example")
    add_event(db_session, site_id="not-yours.example", visitor_id="theirs")

    rebuild_rollups()

    response = signed_in.get("/api/stats/not-yours.example/summary")

    assert response.status_code == 404
    assert "theirs" not in response.text


def test_an_unregistered_domain_is_also_a_404(signed_in):
    assert signed_in.get("/api/stats/never-heard-of-it.example/summary").status_code == 404


def test_unknown_breakdown_property_is_rejected(signed_in, site):
    # The enum is the whitelist: a request parameter never reaches a column name.
    assert signed_in.get(f"/api/stats/{SITE_DOMAIN}/breakdown/passwords").status_code == 422


def test_unknown_period_is_rejected(signed_in, site):
    response = signed_in.get(
        f"/api/stats/{SITE_DOMAIN}/summary", params={"period": "since-forever"}
    )

    assert response.status_code == 422


def test_breakdown_limit_is_bounded(signed_in, site):
    url = f"/api/stats/{SITE_DOMAIN}/breakdown/page"

    assert signed_in.get(url, params={"limit": 0}).status_code == 422
    assert signed_in.get(url, params={"limit": 500}).status_code == 422


def test_a_published_site_is_readable_without_an_account(
    client, db_session, site, rebuild_rollups
):
    add_event(db_session, visitor_id="a")
    accounts.set_visibility(db_session, site=site, public=True)
    rebuild_rollups()

    response = client.get(f"/api/stats/{SITE_DOMAIN}/summary")

    assert response.status_code == 200
    assert response.json()["visitors"] == 1


def test_unpublishing_closes_it_again(client, db_session, site):
    accounts.set_visibility(db_session, site=site, public=True)
    assert client.get(f"/api/stats/{SITE_DOMAIN}/summary").status_code == 200

    accounts.set_visibility(db_session, site=site, public=False)

    assert client.get(f"/api/stats/{SITE_DOMAIN}/summary").status_code == 404


def test_entry_and_exit_page_endpoints(signed_in, db_session, rebuild_rollups, site):
    """The two dimensions that are not columns, served like any other."""
    add_event(db_session, visitor_id="a", pathname="/landing")
    add_event(db_session, visitor_id="a", pathname="/checkout")
    add_event(db_session, visitor_id="b", pathname="/landing")

    rebuild_rollups()

    entries = signed_in.get(f"/api/stats/{SITE_DOMAIN}/breakdown/entry_page")
    exits = signed_in.get(f"/api/stats/{SITE_DOMAIN}/breakdown/exit_page")

    # Both visits landed on /landing; between them they read three pages.
    assert entries.json() == [{"value": "/landing", "visitors": 2, "pageviews": 3}]
    assert exits.json() == [
        {"value": "/checkout", "visitors": 1, "pageviews": 2},
        {"value": "/landing", "visitors": 1, "pageviews": 1},
    ]


def test_a_breakdown_the_whitelist_does_not_know_is_refused(signed_in, site):
    """The property is an enum, not a column name."""
    assert signed_in.get(f"/api/stats/{SITE_DOMAIN}/breakdown/passwords").status_code == 422
