import datetime as dt

from app.models import Event

SITE = "blue-mug.example"


def add_event(db, **overrides):
    # Timestamped against the real clock, because the endpoints resolve their
    # own window rather than taking one from the caller.
    defaults = {
        "site_id": SITE,
        "visitor_id": "visitor-1",
        "pathname": "/",
        "timestamp": dt.datetime.now(dt.UTC),
        "name": "pageview",
        "source": "Google",
        "browser": "Chrome",
        "os": "Windows",
        "device": "desktop",
        "country": "DE",
        "screen_width": 1920,
    }
    db.add(Event(**(defaults | overrides)))
    db.commit()


def test_summary_endpoint(client, db_session):
    add_event(db_session, visitor_id="a")
    add_event(db_session, visitor_id="a", pathname="/about")
    add_event(db_session, visitor_id="b")

    response = client.get(f"/api/stats/{SITE}/summary")

    assert response.status_code == 200
    assert response.json() == {"visitors": 2, "pageviews": 3, "views_per_visitor": 1.5}


def test_timeseries_endpoint_returns_a_full_series(client, db_session):
    add_event(db_session, visitor_id="a")

    response = client.get(f"/api/stats/{SITE}/timeseries", params={"period": "30d"})

    assert response.status_code == 200
    points = response.json()
    assert len(points) == 30
    assert sum(point["visitors"] for point in points) == 1
    assert set(points[0]) == {"bucket", "visitors", "pageviews"}


def test_breakdown_endpoint(client, db_session):
    add_event(db_session, visitor_id="a", pathname="/popular")
    add_event(db_session, visitor_id="b", pathname="/popular")
    add_event(db_session, visitor_id="c", pathname="/quiet")

    response = client.get(f"/api/stats/{SITE}/breakdown/page")

    assert response.status_code == 200
    assert response.json() == [
        {"value": "/popular", "visitors": 2, "pageviews": 2},
        {"value": "/quiet", "visitors": 1, "pageviews": 1},
    ]


def test_live_endpoint(client, db_session):
    add_event(db_session, visitor_id="a")

    response = client.get(f"/api/stats/{SITE}/live")

    assert response.status_code == 200
    assert response.json() == {"visitors": 1, "window_minutes": 5}


def test_an_unknown_site_reports_zeroes_rather_than_failing(client):
    response = client.get("/api/stats/never-heard-of-it.example/summary")

    assert response.status_code == 200
    assert response.json()["visitors"] == 0


def test_unknown_breakdown_property_is_rejected(client):
    # The enum is the whitelist: a request parameter never reaches a column name.
    response = client.get(f"/api/stats/{SITE}/breakdown/passwords")

    assert response.status_code == 422


def test_unknown_period_is_rejected(client):
    response = client.get(f"/api/stats/{SITE}/summary", params={"period": "since-forever"})

    assert response.status_code == 422


def test_breakdown_limit_is_bounded(client):
    assert client.get(f"/api/stats/{SITE}/breakdown/page", params={"limit": 0}).status_code == 422
    assert client.get(f"/api/stats/{SITE}/breakdown/page", params={"limit": 500}).status_code == 422
