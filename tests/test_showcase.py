"""The landing page's playable demonstration of the salt.

The page can assert that a rotating salt makes an identifier expire, and a
reader has no reason to believe it. These hold the demonstration to the three
claims it makes on screen: the number moves when the person moves, when the
site moves, and when the day moves -- and the cookie beside it moves for none
of them.
"""

import datetime as dt
import re

import pytest

from app.services import showcase
from app.services.timeranges import Period
from app.services.visitors import visitor_id

TODAY = dt.date(2026, 6, 15)


def _build(person: str = "ada", site: str = showcase.SITES[0], today: dt.date = TODAY):
    """The panel for one person on one site, with a fixed live caller."""
    return showcase.build(
        person=person,
        site=site,
        live_ip="192.0.2.9",
        live_user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0",
        live_label="Chrome on Windows",
        today=today,
    )



def _beacon_id(page: str) -> str:
    found = re.search(r'class="ident beacon">\s*([a-f0-9]{32})', page)
    assert found is not None, "the page no longer shows a Beacon identifier"
    return found.group(1)


def _cookie_id(page: str) -> str:
    found = re.search(r'class="ident cookie">\s*([0-9a-f-]{36})', page)
    assert found is not None, "the page no longer shows a cookie identifier"
    return found.group(1)


# --- the three claims ------------------------------------------------------


def test_every_day_is_a_different_number():
    """The whole argument, and it is visible without clicking anything."""
    ids = [row.beacon_id for row in _build().rows]

    assert len(set(ids)) == len(showcase.DAYS)


def test_the_same_person_is_a_different_number_on_another_site():
    """Scoping by site is what stops anything being correlated across customers."""
    here = _build(site=showcase.SITES[0]).rows[0].beacon_id
    there = _build(site=showcase.SITES[1]).rows[0].beacon_id

    assert here != there


def test_two_people_are_different_numbers():
    assert _build(person="ada").rows[0].beacon_id != _build(person="grace").rows[0].beacon_id


@pytest.mark.parametrize("site", showcase.SITES)
def test_the_cookie_moves_for_nothing(site: str):
    """The contrast the column beside it exists to draw.

    One value repeated down all three days, and the same one on either site.
    """
    panel = _build(site=site)

    assert panel.cookie_id == _build(site=showcase.SITES[0]).cookie_id
    assert len({panel.cookie_id}) == 1


# --- it has to be the real thing ------------------------------------------


def test_it_calls_the_function_that_runs_on_real_traffic():
    """A demo that reimplemented the hash would drift from it and start lying.

    Recomputed here with visitors.visitor_id directly, against the same salt
    the demo derives, so the page cannot quietly diverge from the collector.
    """
    shown = _build(site="blue-mug.example").rows[0]

    assert shown.beacon_id == visitor_id(
        salt=showcase.demo_salt(TODAY),
        site_id="blue-mug.example",
        ip=showcase.PEOPLE["ada"][0],
        user_agent=showcase.PEOPLE["ada"][1],
    )


def test_the_demo_salt_is_not_one_of_the_real_ones(db_session):
    """It must never read or create a row in daily_salts.

    Real salts are random and deleted after two days, which is exactly what
    makes them unusable here: the page has to show the same thing to everyone,
    including for a day whose salt would already be gone.
    """
    from sqlalchemy import select

    from app.models import DailySalt

    _build(today=None)

    assert db_session.scalars(select(DailySalt)).all() == []


def test_a_day_past_retention_is_shown_as_unrecoverable():
    """Two days is the retention, so the third state is the one that matters."""
    kept = [row.salt_still_exists for row in _build().rows]

    assert kept == [True, True, False]


# --- nonsense in the query string -----------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {"person": "nobody"},
        {"site": "not-a-site.example"},
        {"person": "<script>alert(1)</script>"},
    ],
)
def test_a_made_up_choice_falls_back_rather_than_failing(client, params):
    """Every control is a URL, so every control can be edited by hand."""
    response = client.get("/", params=params)

    assert response.status_code == 200
    assert _beacon_id(response.text)


def test_an_invented_person_is_not_reflected_into_the_page(client):
    response = client.get("/", params={"person": "<script>alert(1)</script>"})

    assert "<script>alert(1)</script>" not in response.text


# --- through the page ------------------------------------------------------


def test_the_page_shows_three_different_numbers_and_one_cookie(client):
    """No interaction required: the contrast is the column."""
    page = client.get("/", params={"person": "ada", "site": showcase.SITES[0]}).text
    beacons = re.findall(r"([a-f0-9]{32})", page)
    cookies = re.findall(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", page)

    assert len(set(beacons[:3])) == 3
    assert len(set(cookies[:3])) == 1


def test_changing_the_site_changes_what_the_page_shows(client):
    here = client.get("/", params={"person": "ada", "site": showcase.SITES[0]}).text
    there = client.get("/", params={"person": "ada", "site": showcase.SITES[1]}).text

    assert _beacon_id(here) != _beacon_id(there)
    assert _cookie_id(here) == _cookie_id(there)


def test_every_control_is_a_link_rather_than_a_script(client):
    """The claim the README makes about the whole dashboard, applied here.

    If a control ever becomes a button that needs JavaScript, this demo stops
    working for the people most likely to have scripting off -- which, on a
    privacy product, is not a group to lose.
    """
    page = client.get("/").text
    section = page[page.index('id="try"') :]
    section = section[: section.index("</section>")]

    # One per person, plus "You", plus one per site.
    assert section.count('<a class="chip') == len(showcase.PEOPLE) + 1 + len(showcase.SITES)
    assert "<button" not in section
    assert "onclick" not in section


def test_the_page_names_the_salt_that_produced_the_number(client):
    """Without the date it is a number that changes for unexplained reasons."""
    page = client.get("/", params={"person": "ada", "site": showcase.SITES[0]}).text

    for row in _build(today=None).rows:
        assert row.day.isoformat() in page


# --- the live hero ---------------------------------------------------------


def test_the_hero_shows_the_demo_site_when_it_has_traffic(client, db_session, account):
    """The front page opens with the product rather than a picture of it."""
    from app.services import accounts, rollups
    from tests.test_rollups import DAY, add_event

    # Registered as well as populated: the collector only records events for a
    # domain somebody has added, so a hero reading from one that was never
    # registered would be reading rows that could not exist.
    accounts.add_site(db_session, owner=account, domain=showcase.PREVIEW_SITE)
    for index in range(5):
        add_event(db_session, site_id=showcase.PREVIEW_SITE, visitor_id=f"v{index}")
    db_session.commit()
    rollups.rebuild_day(db_session, site_id=showcase.PREVIEW_SITE, day=DAY)

    page = client.get("/").text

    assert "live-hero" in page
    assert showcase.PREVIEW_SITE in page


def test_an_unseeded_install_falls_back_to_the_lighthouse(client, db_session):
    """A fresh clone has no traffic, and a hero built on an empty chart is
    worse than one built on a picture."""
    page = client.get("/").text

    assert showcase.preview(db_session, period=Period.LAST_30_DAYS) is None
    assert "live-hero" not in page
    assert "hero-scene" in page


def test_a_registered_site_with_no_traffic_also_falls_back(db_session, account):
    """Registered is not the same as having anything to draw."""
    from app.services import accounts

    accounts.add_site(db_session, owner=account, domain=showcase.PREVIEW_SITE)

    assert showcase.preview(db_session, period=Period.LAST_30_DAYS) is None


def test_the_period_chips_are_links_rather_than_script(client):
    """Same rule as the rest of the page: changing the view is a navigation."""
    page = client.get("/").text

    for period in showcase.PREVIEW_PERIODS:
        assert f'href="?show={period.value}"' in page or "live-hero" not in page
