import datetime as dt

from app.models import Event
from app.services import accounts
from tests.conftest import OWNER_PASSWORD, SITE_DOMAIN, with_local_bucket


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
        "screen": "Desktop",
    }
    db.add(Event(**with_local_bucket(defaults | overrides)))
    db.commit()


def test_dashboard_renders_the_headline_numbers(signed_in, db_session, rebuild_rollups, site):
    add_event(db_session, visitor_id="a")
    add_event(db_session, visitor_id="b")

    rebuild_rollups()

    response = signed_in.get(f"/sites/{SITE_DOMAIN}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert SITE_DOMAIN in response.text
    assert "Visitors" in response.text
    assert "Pageviews" in response.text


def test_dashboard_renders_each_breakdown_panel(signed_in, db_session, rebuild_rollups, site):
    add_event(db_session, visitor_id="a")

    rebuild_rollups()

    body = signed_in.get(f"/sites/{SITE_DOMAIN}").text

    assert "Pages" in body
    assert "/products/blue-mug" in body
    assert "Hacker News" in body
    assert "desktop" in body
    # Every dimension gets a tab, not just the four that used to fit.
    for tab in ("Sources", "Countries", "Devices", "Browsers", "Systems"):
        assert tab in body


def test_dashboard_draws_a_chart(signed_in, db_session, rebuild_rollups, site):
    add_event(db_session, visitor_id="a")

    rebuild_rollups()

    body = signed_in.get(f"/sites/{SITE_DOMAIN}").text

    assert "<svg" in body
    assert 'class="chart-curve"' in body
    # Sparklines in the headline tiles.
    assert 'class="spark"' in body


def test_the_selected_period_is_marked_current(signed_in, db_session, rebuild_rollups, site):
    add_event(db_session, visitor_id="a")

    rebuild_rollups()

    body = signed_in.get(f"/sites/{SITE_DOMAIN}", params={"period": "7d"}).text

    assert f'href="/sites/{SITE_DOMAIN}?period=7d"\n       class="current"' in body


def test_a_site_with_no_traffic_says_so(signed_in, site):
    body = signed_in.get(f"/sites/{SITE_DOMAIN}").text

    assert "No visitors in this period yet." in body


def test_signed_out_visitors_are_sent_to_the_login_page(client, site):
    response = client.get(f"/sites/{SITE_DOMAIN}", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_another_persons_dashboard_is_a_404(signed_in, db_session, rebuild_rollups):
    stranger = accounts.register(db_session, email="stranger@example.com", password=OWNER_PASSWORD)
    accounts.add_site(db_session, owner=stranger, domain="not-yours.example")

    rebuild_rollups()

    assert signed_in.get("/sites/not-yours.example").status_code == 404


def test_an_invalid_period_is_rejected(signed_in, site):
    assert signed_in.get(f"/sites/{SITE_DOMAIN}", params={"period": "forever"}).status_code == 422


def test_index_lists_only_your_own_sites(signed_in, db_session, rebuild_rollups, site, account):
    accounts.add_site(db_session, owner=account, domain="second.example")
    stranger = accounts.register(db_session, email="stranger@example.com", password=OWNER_PASSWORD)
    accounts.add_site(db_session, owner=stranger, domain="theirs.example")

    rebuild_rollups()

    body = signed_in.get("/").text

    assert f"/sites/{SITE_DOMAIN}" in body
    assert "/sites/second.example" in body
    assert "theirs.example" not in body


def test_index_prompts_for_a_first_site(signed_in):
    body = signed_in.get("/").text

    assert "No sites yet" in body
    assert 'name="domain"' in body


def test_the_front_page_explains_itself_to_signed_out_visitors(client):
    """A portfolio link should not open onto a bare login box."""
    response = client.get("/")

    assert response.status_code == 200
    assert "Know what your visitors read" in response.text
    assert "/signup" in response.text


def test_a_published_dashboard_is_readable_by_anyone(
    client, db_session, site, rebuild_rollups
):
    """The point of publishing: a link somebody can follow without signing up."""
    add_event(db_session, visitor_id="a")
    accounts.set_visibility(db_session, site=site, public=True)
    rebuild_rollups()

    response = client.get(f"/sites/{SITE_DOMAIN}")

    assert response.status_code == 200
    assert "Visitors" in response.text
    assert "Public" in response.text


def test_a_visitor_to_a_published_dashboard_gets_no_controls(client, db_session, site):
    accounts.set_visibility(db_session, site=site, public=True)

    body = client.get(f"/sites/{SITE_DOMAIN}").text

    assert "Make private" not in body
    assert "Publish this dashboard" not in body


def test_the_owner_sees_the_publish_control(signed_in, site):
    body = signed_in.get(f"/sites/{SITE_DOMAIN}").text

    assert "Publish this dashboard" in body
    assert "Only you can see this." in body


def test_the_owner_can_publish_and_unpublish(signed_in, db_session, site):
    published = signed_in.post(
        f"/sites/{SITE_DOMAIN}/visibility", data={"public": "true"}, follow_redirects=False
    )

    assert published.status_code == 303
    db_session.refresh(site)
    assert site.public is True

    signed_in.post(
        f"/sites/{SITE_DOMAIN}/visibility", data={"public": "false"}, follow_redirects=False
    )
    db_session.refresh(site)
    assert site.public is False


def test_a_stranger_cannot_publish_somebody_elses_site(signed_in, db_session):
    stranger = accounts.register(db_session, email="stranger@example.com", password=OWNER_PASSWORD)
    theirs = accounts.add_site(db_session, owner=stranger, domain="theirs.example")

    response = signed_in.post(
        "/sites/theirs.example/visibility", data={"public": "true"}, follow_redirects=False
    )

    assert response.status_code == 404
    db_session.refresh(theirs)
    assert theirs.public is False


def test_publishing_requires_an_account(client, site):
    assert client.post(
        f"/sites/{SITE_DOMAIN}/visibility", data={"public": "true"}
    ).status_code == 401


def test_the_goals_panel_explains_itself_when_empty(signed_in, site):
    body = signed_in.get(f"/sites/{SITE_DOMAIN}").text

    assert "Goals" in body
    assert "No custom events yet" in body


def test_the_owner_can_set_the_timezone(signed_in, db_session, site):
    response = signed_in.post(
        f"/sites/{SITE_DOMAIN}/timezone",
        data={"timezone": "Europe/Berlin"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    db_session.refresh(site)
    assert site.timezone == "Europe/Berlin"


def test_an_invented_timezone_is_refused(signed_in, db_session, site):
    response = signed_in.post(
        f"/sites/{SITE_DOMAIN}/timezone",
        data={"timezone": "Mars/Olympus_Mons"},
        follow_redirects=False,
    )

    assert response.status_code == 422
    db_session.refresh(site)
    assert site.timezone == "UTC"


def test_a_stranger_cannot_set_somebody_elses_timezone(signed_in, db_session):
    stranger = accounts.register(db_session, email="stranger@example.com", password=OWNER_PASSWORD)
    theirs = accounts.add_site(db_session, owner=stranger, domain="theirs.example")

    response = signed_in.post(
        "/sites/theirs.example/timezone",
        data={"timezone": "Asia/Tokyo"},
        follow_redirects=False,
    )

    assert response.status_code == 404
    db_session.refresh(theirs)
    assert theirs.timezone == "UTC"


def test_the_dashboard_says_which_clock_it_is_using(signed_in, db_session, site):
    accounts.set_timezone(db_session, site=site, timezone="Asia/Tokyo")

    body = signed_in.get(f"/sites/{SITE_DOMAIN}").text

    assert "days here start at midnight in Asia/Tokyo" in body
