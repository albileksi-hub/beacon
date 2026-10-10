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
from app.services.visitors import visitor_id

TODAY = dt.date(2026, 6, 15)


def _beacon_id(page: str) -> str:
    found = re.search(r'class="ident beacon">([a-f0-9]+)<', page)
    assert found is not None, "the page no longer shows a Beacon identifier"
    return found.group(1)


def _cookie_id(page: str) -> str:
    found = re.search(r'class="ident cookie">([a-f0-9-]+)<', page)
    assert found is not None, "the page no longer shows a cookie identifier"
    return found.group(1)


# --- the three claims ------------------------------------------------------


def test_the_same_person_is_a_different_number_tomorrow():
    """The whole argument. If this ever stops being true, so does the pitch."""
    today = showcase.identity(person="ada", site=showcase.SITES[0], day_offset=0, today=TODAY)
    tomorrow = showcase.identity(person="ada", site=showcase.SITES[0], day_offset=1, today=TODAY)

    assert today.beacon_id != tomorrow.beacon_id


def test_the_same_person_is_a_different_number_on_another_site():
    """Scoping by site is what stops anything being correlated across customers."""
    here = showcase.identity(person="ada", site=showcase.SITES[0], day_offset=0, today=TODAY)
    there = showcase.identity(person="ada", site=showcase.SITES[1], day_offset=0, today=TODAY)

    assert here.beacon_id != there.beacon_id


def test_two_people_are_different_numbers():
    ada = showcase.identity(person="ada", site=showcase.SITES[0], day_offset=0, today=TODAY)
    grace = showcase.identity(person="grace", site=showcase.SITES[0], day_offset=0, today=TODAY)

    assert ada.beacon_id != grace.beacon_id


@pytest.mark.parametrize("site", showcase.SITES)
@pytest.mark.parametrize("day", showcase.DAYS)
def test_the_cookie_moves_for_nothing(site: str, day: int):
    """The contrast the panel beside it exists to draw."""
    assert showcase.identity(person="ada", site=site, day_offset=day, today=TODAY).cookie_id == (
        showcase.identity(
            person="ada", site=showcase.SITES[0], day_offset=0, today=TODAY
        ).cookie_id
    )


# --- it has to be the real thing ------------------------------------------


def test_it_calls_the_function_that_runs_on_real_traffic():
    """A demo that reimplemented the hash would drift from it and start lying.

    Recomputed here with visitors.visitor_id directly, against the same salt
    the demo derives, so the page cannot quietly diverge from the collector.
    """
    shown = showcase.identity(person="ada", site="blue-mug.example", day_offset=0, today=TODAY)

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

    showcase.identity(person="ada", site="blue-mug.example", day_offset=0)

    assert db_session.scalars(select(DailySalt)).all() == []


def test_a_day_past_retention_is_shown_as_unrecoverable():
    """Two days is the retention, so the third state is the one that matters."""
    assert showcase.identity(person="ada", site=showcase.SITES[0], day_offset=0).salt_still_exists
    assert showcase.identity(person="ada", site=showcase.SITES[0], day_offset=1).salt_still_exists
    assert not showcase.identity(
        person="ada", site=showcase.SITES[0], day_offset=2
    ).salt_still_exists


# --- nonsense in the query string -----------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {"person": "nobody"},
        {"site": "not-a-site.example"},
        {"day": 99},
        {"day": -5},
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


def test_changing_the_day_changes_what_the_page_shows(client):
    today = client.get("/", params={"person": "ada", "site": showcase.SITES[0], "day": 0}).text
    tomorrow = client.get("/", params={"person": "ada", "site": showcase.SITES[0], "day": 1}).text

    assert _beacon_id(today) != _beacon_id(tomorrow)
    assert _cookie_id(today) == _cookie_id(tomorrow)


def test_changing_the_site_changes_what_the_page_shows(client):
    here = client.get("/", params={"person": "ada", "site": showcase.SITES[0], "day": 0}).text
    there = client.get("/", params={"person": "ada", "site": showcase.SITES[1], "day": 0}).text

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

    assert section.count("<a class=\"chip") == len(showcase.PEOPLE) + len(showcase.SITES) + len(
        showcase.DAYS
    )
    assert "<button" not in section
    assert "onclick" not in section


def test_the_page_names_the_salt_that_produced_the_number(client):
    """Without the date it is a number that changes for unexplained reasons."""
    page = client.get("/", params={"person": "ada", "site": showcase.SITES[0], "day": 0}).text
    expected = showcase.identity(person="ada", site=showcase.SITES[0], day_offset=0).day

    assert expected.isoformat() in page
