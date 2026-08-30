"""How far people get along a path, and the two things that must not count.

A funnel that widens is not a funnel: somebody who lands on the confirmation
page without passing through the basket has not been through it, and neither
has somebody who did the steps in the wrong order.
"""

import datetime as dt

import pytest
from sqlalchemy import select

from app.models import Event, Funnel, FunnelStep, Role, StepKind
from app.services import accounts, funnels
from tests.conftest import OWNER_PASSWORD, SITE_DOMAIN, with_local_bucket

DAY = dt.date(2026, 8, 20)
BASE = dt.datetime(2026, 8, 20, 12, tzinfo=dt.UTC)


def visit(db, visitor, minute, *, path=None, goal=None):
    db.add(Event(**with_local_bucket({
        "site_id": SITE_DOMAIN, "visitor_id": visitor,
        "timestamp": BASE + dt.timedelta(minutes=minute),
        "name": goal or "pageview", "pathname": path or "/checkout",
        "source": "Direct", "browser": "Chrome", "os": "Windows",
        "device": "desktop", "country": "DE", "screen": "Desktop",
    })))
    db.commit()


@pytest.fixture
def checkout(db_session, site):
    funnel = Funnel(site_id=site.id, name="Checkout", steps=[
        FunnelStep(position=0, kind=StepKind.PAGE, value="/pricing"),
        FunnelStep(position=1, kind=StepKind.PAGE, value="/basket"),
        FunnelStep(position=2, kind=StepKind.GOAL, value="purchase"),
    ])
    db_session.add(funnel)
    db_session.commit()
    return funnel


def measured(db, funnel):
    return [s.visits for s in funnels.measure(db, funnel=funnel, first_day=DAY, last_day=DAY)]


def test_a_funnel_narrows_as_people_fall_out(db_session, checkout):
    for who in ("v1", "v2", "v3", "v4"):
        visit(db_session, who, 0, path="/pricing")
    for who in ("v1", "v2", "v3"):
        visit(db_session, who, 5, path="/basket")
    for who in ("v1", "v2"):
        visit(db_session, who, 10, goal="purchase")

    assert measured(db_session, checkout) == [4, 3, 2]


def test_arriving_at_the_end_without_the_middle_does_not_count(db_session, checkout):
    """The confirmation page is reachable directly; the funnel is not."""
    visit(db_session, "skipper", 0, goal="purchase")

    assert measured(db_session, checkout) == [0, 0, 0]


def test_the_steps_have_to_happen_in_the_order_given(db_session, checkout):
    """Basket first and pricing after is not the path the funnel describes."""
    visit(db_session, "backwards", 0, path="/basket")
    visit(db_session, "backwards", 5, path="/pricing")

    assert measured(db_session, checkout) == [1, 0, 0]


def test_a_visit_that_doubles_back_still_counts_once(db_session, checkout):
    """Reaching a step twice is one visit reaching it, not two."""
    visit(db_session, "browser", 0, path="/pricing")
    visit(db_session, "browser", 2, path="/basket")
    visit(db_session, "browser", 4, path="/pricing")
    visit(db_session, "browser", 6, path="/basket")

    assert measured(db_session, checkout) == [1, 1, 0]


def test_two_people_on_different_days_are_not_one_journey(db_session, checkout):
    """The limit the whole feature has to be honest about.

    A visitor ID is a keyed hash of a salt that rotates at the site's midnight,
    so the same person on two days is two identities by construction. Reading
    the pricing page on Tuesday and buying on Wednesday is not a conversion
    this system can see, and no amount of SQL will make it one.
    """
    visit(db_session, "tuesday-id", 0, path="/pricing")
    db_session.add(Event(**with_local_bucket({
        "site_id": SITE_DOMAIN, "visitor_id": "wednesday-id",
        "timestamp": BASE + dt.timedelta(days=1), "name": "purchase",
        "pathname": "/checkout", "source": "Direct", "browser": "Chrome",
        "os": "Windows", "device": "desktop", "country": "DE", "screen": "Desktop",
    })))
    db_session.commit()

    steps = funnels.measure(
        db_session, funnel=checkout, first_day=DAY, last_day=DAY + dt.timedelta(days=1)
    )
    assert [s.visits for s in steps] == [1, 0, 0]


def test_a_page_step_does_not_match_a_goal_of_the_same_name(db_session, site):
    """Custom events share the table with pageviews."""
    funnel = Funnel(site_id=site.id, name="Trap", steps=[
        FunnelStep(position=0, kind=StepKind.PAGE, value="/pricing"),
        FunnelStep(position=1, kind=StepKind.PAGE, value="/basket"),
    ])
    db_session.add(funnel)
    db_session.commit()

    visit(db_session, "v1", 0, path="/pricing")
    # A goal that somebody named after a path, which must not satisfy step two.
    visit(db_session, "v1", 5, goal="/basket")

    assert measured(db_session, funnel) == [1, 0]


def test_an_empty_funnel_measures_nothing(db_session, site):
    funnel = Funnel(site_id=site.id, name="Empty", steps=[])
    db_session.add(funnel)
    db_session.commit()

    assert funnels.measure(db_session, funnel=funnel, first_day=DAY, last_day=DAY) == []


def test_the_shares_and_drop_offs_read_off_the_counts(db_session, checkout):
    for who in ("v1", "v2", "v3", "v4"):
        visit(db_session, who, 0, path="/pricing")
    for who in ("v1", "v2"):
        visit(db_session, who, 5, path="/basket")

    steps = funnels.measure(db_session, funnel=checkout, first_day=DAY, last_day=DAY)
    entered = steps[0].visits

    assert steps[1].share_of(entered) == 50.0
    assert steps[1].dropped_from(steps[0].visits) == 2
    # Nobody entered means no division, not a crash.
    assert steps[0].share_of(0) == 0.0


# ---- defining them ----------------------------------------------------------


def test_steps_are_read_one_per_line(db_session):
    parsed = funnels.parse_steps("/pricing\n/basket\ngoal:purchase\n")

    assert parsed == [
        (StepKind.PAGE, "/pricing"),
        (StepKind.PAGE, "/basket"),
        (StepKind.GOAL, "purchase"),
    ]


def test_blank_lines_are_not_steps(db_session):
    """A trailing newline should not become a step nobody can ever reach."""
    assert len(funnels.parse_steps("/a\n\n   \n/b\n")) == 2


@pytest.mark.parametrize(
    ("raw", "complaint"),
    [
        ("/only-one", "at least two"),
        ("", "at least two"),
        ("goal:\n/b", "needs a name"),
        ("\n".join(f"/step-{i}" for i in range(9)), "at most"),
    ],
)
def test_a_definition_that_describes_no_path_is_refused(raw, complaint):
    with pytest.raises(funnels.FunnelError, match=complaint):
        funnels.parse_steps(raw)


def test_a_funnel_needs_a_name(db_session, site):
    with pytest.raises(funnels.FunnelError, match="Give the funnel a name"):
        funnels.create(db_session, site=site, name="   ", raw_steps="/a\n/b")


def test_two_funnels_cannot_share_a_name_on_one_site(db_session, site):
    funnels.create(db_session, site=site, name="Checkout", raw_steps="/a\n/b")

    with pytest.raises(funnels.FunnelError, match="already has a funnel"):
        funnels.create(db_session, site=site, name="Checkout", raw_steps="/c\n/d")


def test_deleting_a_funnel_takes_its_steps_with_it(db_session, site):
    """The cascade is real only because PRAGMA foreign_keys is on."""
    created = funnels.create(db_session, site=site, name="Checkout", raw_steps="/a\n/b")

    funnels.delete(db_session, site=site, funnel_id=created.id)

    assert db_session.scalars(select(Funnel)).all() == []
    assert db_session.scalars(select(FunnelStep)).all() == []


def test_a_funnel_cannot_be_deleted_through_another_site(db_session, site, account):
    """The id is scoped by site, so one dashboard cannot post at another."""
    theirs = accounts.add_site(db_session, owner=account, domain="other.example")
    created = funnels.create(db_session, site=site, name="Checkout", raw_steps="/a\n/b")

    with pytest.raises(funnels.FunnelError, match="No such funnel"):
        funnels.delete(db_session, site=theirs, funnel_id=created.id)

    assert db_session.scalars(select(Funnel)).all() != []


# ---- through the page -------------------------------------------------------


def test_the_page_shows_a_funnel_and_its_numbers(signed_in, db_session, site, checkout):
    for who in ("v1", "v2"):
        visit(db_session, who, 0, path="/pricing")
    visit(db_session, "v1", 5, path="/basket")

    body = signed_in.get(f"/sites/{SITE_DOMAIN}/funnels?period=30d").text

    assert "Checkout" in body
    assert "/pricing" in body and "/basket" in body


def test_an_owner_can_add_and_delete_a_funnel(signed_in, db_session, site):
    added = signed_in.post(
        f"/sites/{SITE_DOMAIN}/funnels",
        data={"name": "Signup", "steps": "/pricing\ngoal:signup"},
        follow_redirects=False,
    )
    assert added.status_code == 303
    created = db_session.scalars(select(Funnel)).one()
    assert [s.value for s in created.steps] == ["/pricing", "signup"]

    removed = signed_in.post(
        f"/sites/{SITE_DOMAIN}/funnels/{created.id}/delete", follow_redirects=False
    )
    assert removed.status_code == 303
    assert db_session.scalars(select(Funnel)).all() == []


def test_a_bad_definition_is_reported_on_the_page(signed_in, site):
    response = signed_in.post(
        f"/sites/{SITE_DOMAIN}/funnels", data={"name": "Nope", "steps": "/only-one"}
    )

    assert response.status_code == 400
    assert "at least two steps" in response.text


def test_a_viewer_cannot_see_or_change_funnels(client, db_session, site, account):
    """A funnel is a setting, so it sits behind AdministeredSite."""
    accounts.register(db_session, email="mate@example.com", password=OWNER_PASSWORD)
    accounts.add_member(db_session, site=site, email="mate@example.com", role=Role.VIEWER)
    client.post("/login", data={"email": "mate@example.com", "password": OWNER_PASSWORD})

    assert client.get(f"/sites/{SITE_DOMAIN}/funnels").status_code == 404
    assert client.post(
        f"/sites/{SITE_DOMAIN}/funnels", data={"name": "x", "steps": "/a\n/b"}
    ).status_code == 404


def test_the_page_knows_who_is_looking_at_it(signed_in, site, checkout):
    """base.html renders the signed-out header without a user in the context.

    Both pages added here missed it, so a signed-in owner was shown a "Sign in"
    link on a page only a signed-in owner can reach. Nothing failed; it just
    looked wrong, which is the kind of thing a test does not notice and a
    screenshot does.
    """
    body = signed_in.get(f"/sites/{SITE_DOMAIN}/funnels").text

    assert "Sign out" in body
    assert ">Sign in<" not in body


def test_the_people_page_knows_too(signed_in, site):
    body = signed_in.get(f"/sites/{SITE_DOMAIN}/people").text

    assert "Sign out" in body
    assert ">Sign in<" not in body


def test_deleting_a_funnel_that_is_not_there_is_reported_on_the_page(signed_in, site):
    """A stale Delete button on a page somebody left open in another tab."""
    response = signed_in.post(f"/sites/{SITE_DOMAIN}/funnels/999/delete")

    assert response.status_code == 400
    assert "No such funnel" in response.text
