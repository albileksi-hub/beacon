import datetime as dt

from app.models import Event

SITE = "blue-mug.example"


def add_event(db, **overrides):
    defaults = {
        "site_id": SITE,
        "visitor_id": "visitor-1",
        "pathname": "/products/blue-mug",
        "timestamp": dt.datetime.now(dt.UTC),
        "name": "pageview",
        "source": "Hacker News",
        "browser": "Firefox",
        "os": "Linux",
        "device": "desktop",
        "country": "DE",
        "screen_width": 1920,
    }
    db.add(Event(**(defaults | overrides)))
    db.commit()


def test_dashboard_renders_the_headline_numbers(client, db_session):
    add_event(db_session, visitor_id="a")
    add_event(db_session, visitor_id="b")

    response = client.get(f"/sites/{SITE}")
    body = response.text

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert SITE in body
    assert "Visitors" in body
    assert "Pageviews" in body


def test_dashboard_renders_each_breakdown_panel(client, db_session):
    add_event(db_session, visitor_id="a")

    body = client.get(f"/sites/{SITE}").text

    assert "Top pages" in body
    assert "/products/blue-mug" in body
    assert "Hacker News" in body
    assert "desktop" in body


def test_dashboard_draws_a_chart(client, db_session):
    add_event(db_session, visitor_id="a")

    body = client.get(f"/sites/{SITE}").text

    assert "<svg" in body
    assert "<polyline" in body


def test_the_selected_period_is_marked_current(client, db_session):
    add_event(db_session, visitor_id="a")

    body = client.get(f"/sites/{SITE}", params={"period": "7d"}).text

    assert 'href="/sites/blue-mug.example?period=7d"\n       class="current"' in body


def test_an_unknown_site_renders_rather_than_erroring(client):
    response = client.get("/sites/never-heard-of-it.example")

    assert response.status_code == 200
    assert "No data for this period." in response.text


def test_an_invalid_period_is_rejected(client):
    assert client.get(f"/sites/{SITE}", params={"period": "forever"}).status_code == 422


def test_index_lists_sites_that_have_sent_traffic(client, db_session):
    add_event(db_session, site_id="one.example")
    add_event(db_session, site_id="two.example")

    body = client.get("/").text

    assert '/sites/one.example' in body
    assert '/sites/two.example' in body


def test_index_explains_setup_when_there_is_no_traffic(client):
    body = client.get("/").text

    assert "No traffic recorded yet." in body
    assert "data-site-id" in body
