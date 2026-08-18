import datetime as dt

from app.models import Event
from app.services import accounts
from tests.conftest import OWNER_PASSWORD, SITE_DOMAIN


def add_event(db, **overrides):
    defaults = {
        "site_id": SITE_DOMAIN,
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


def test_dashboard_renders_the_headline_numbers(signed_in, db_session, site):
    add_event(db_session, visitor_id="a")
    add_event(db_session, visitor_id="b")

    response = signed_in.get(f"/sites/{SITE_DOMAIN}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert SITE_DOMAIN in response.text
    assert "Visitors" in response.text
    assert "Pageviews" in response.text


def test_dashboard_renders_each_breakdown_panel(signed_in, db_session, site):
    add_event(db_session, visitor_id="a")

    body = signed_in.get(f"/sites/{SITE_DOMAIN}").text

    assert "Top pages" in body
    assert "/products/blue-mug" in body
    assert "Hacker News" in body
    assert "desktop" in body


def test_dashboard_draws_a_chart(signed_in, db_session, site):
    add_event(db_session, visitor_id="a")

    body = signed_in.get(f"/sites/{SITE_DOMAIN}").text

    assert "<svg" in body
    assert "<polyline" in body


def test_the_selected_period_is_marked_current(signed_in, db_session, site):
    add_event(db_session, visitor_id="a")

    body = signed_in.get(f"/sites/{SITE_DOMAIN}", params={"period": "7d"}).text

    assert f'href="/sites/{SITE_DOMAIN}?period=7d"\n       class="current"' in body


def test_a_site_with_no_traffic_says_so(signed_in, site):
    body = signed_in.get(f"/sites/{SITE_DOMAIN}").text

    assert "No data for this period." in body


def test_signed_out_visitors_are_sent_to_the_login_page(client, site):
    response = client.get(f"/sites/{SITE_DOMAIN}", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_another_persons_dashboard_is_a_404(signed_in, db_session):
    stranger = accounts.register(db_session, email="stranger@example.com", password=OWNER_PASSWORD)
    accounts.add_site(db_session, owner=stranger, domain="not-yours.example")

    assert signed_in.get("/sites/not-yours.example").status_code == 404


def test_an_invalid_period_is_rejected(signed_in, site):
    assert signed_in.get(f"/sites/{SITE_DOMAIN}", params={"period": "forever"}).status_code == 422


def test_index_lists_only_your_own_sites(signed_in, db_session, site, account):
    accounts.add_site(db_session, owner=account, domain="second.example")
    stranger = accounts.register(db_session, email="stranger@example.com", password=OWNER_PASSWORD)
    accounts.add_site(db_session, owner=stranger, domain="theirs.example")

    body = signed_in.get("/").text

    assert f"/sites/{SITE_DOMAIN}" in body
    assert "/sites/second.example" in body
    assert "theirs.example" not in body


def test_index_prompts_for_a_first_site(signed_in):
    body = signed_in.get("/").text

    assert "No sites yet." in body
    assert 'name="domain"' in body


def test_index_redirects_when_signed_out(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
