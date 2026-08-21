"""Entrances, exits and bounces, over the visitor-day this project calls a visit.

The interesting cases are all about what counts as a visit boundary: custom
events share the events table and must not become landing pages, two pageviews
in the same instant must still have a defined order, and a visitor ID that
appears on two days is two visits rather than one long one.
"""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models import Event
from app.services import visits
from app.services.visits import Edge
from tests.conftest import SITE_DOMAIN

DAY = dt.date(2026, 8, 18)
NOON = dt.datetime(2026, 8, 18, 12, 0, tzinfo=dt.UTC)


def add(db, *, visitor, path, minutes=0, name="pageview", day=None, commit=True):
    moment = NOON + dt.timedelta(minutes=minutes)
    db.add(
        Event(
            site_id=SITE_DOMAIN,
            visitor_id=visitor,
            pathname=path,
            name=name,
            timestamp=moment,
            day=day or moment.date(),
            hour=moment.hour,
        )
    )
    if commit:
        db.commit()


def entries(db, first=DAY, last=DAY):
    return visits.boundary_pages(
        db, site_id=SITE_DOMAIN, first_day=first, last_day=last, edge=Edge.START
    )


def exits(db, first=DAY, last=DAY):
    return visits.boundary_pages(
        db, site_id=SITE_DOMAIN, first_day=first, last_day=last, edge=Edge.END
    )


def bounces(db, first=DAY, last=DAY):
    return visits.bounce_count(db, site_id=SITE_DOMAIN, first_day=first, last_day=last)


def test_a_visit_enters_on_its_first_page_and_leaves_from_its_last(db_session):
    add(db_session, visitor="a", path="/landing", minutes=0)
    add(db_session, visitor="a", path="/middle", minutes=5)
    add(db_session, visitor="a", path="/goodbye", minutes=9)

    assert entries(db_session) == [("/landing", 1, 3)]
    assert exits(db_session) == [("/goodbye", 1, 3)]


def test_the_pageview_column_counts_what_the_visit_went_on_to_read(db_session):
    """The column that makes a landing page worth looking at.

    Two pages can pull the same number of visits and be worth entirely
    different amounts, and without this they are indistinguishable.
    """
    add(db_session, visitor="a", path="/sticky", minutes=0)
    add(db_session, visitor="a", path="/more", minutes=1)
    add(db_session, visitor="a", path="/more-still", minutes=2)
    add(db_session, visitor="b", path="/dead-end", minutes=0)

    assert entries(db_session) == [("/dead-end", 1, 1), ("/sticky", 1, 3)]


def test_a_single_page_visit_bounces_and_a_longer_one_does_not(db_session):
    add(db_session, visitor="stayed", path="/", minutes=0)
    add(db_session, visitor="stayed", path="/about", minutes=3)
    add(db_session, visitor="left", path="/", minutes=0)

    assert bounces(db_session) == 1


def test_a_custom_event_neither_starts_nor_ends_a_visit(db_session):
    """Goals share the table, and a sign-up is not a landing page."""
    add(db_session, visitor="a", path="/pricing", minutes=0)
    add(db_session, visitor="a", path="/checkout", minutes=4)
    add(db_session, visitor="a", path="/checkout", minutes=5, name="signup")

    assert entries(db_session) == [("/pricing", 1, 2)]
    assert exits(db_session) == [("/checkout", 1, 2)]


def test_a_goal_does_not_rescue_a_visit_from_bouncing(db_session):
    """One page read is one page read, whatever else the visit fired."""
    add(db_session, visitor="a", path="/", minutes=0)
    add(db_session, visitor="a", path="/", minutes=1, name="signup")

    assert bounces(db_session) == 1


def test_a_visit_with_no_pageview_at_all_is_not_a_bounce(db_session):
    """It did not land on a page and leave; it never landed on one."""
    add(db_session, visitor="a", path="/", minutes=0, name="signup")

    assert bounces(db_session) == 0
    assert entries(db_session) == []


def test_two_pageviews_in_the_same_instant_still_have_an_order(db_session):
    """The tiebreak that keeps the rollups and the raw queries agreeing.

    Batched writes can land two of a visitor's pageviews on the same timestamp.
    Without a total order the first page of that visit is whichever row the
    database felt like returning, and the two code paths would disagree at
    random -- the one failure tests/test_reports.py exists to catch.
    """
    add(db_session, visitor="a", path="/first", minutes=0, commit=False)
    add(db_session, visitor="a", path="/second", minutes=0)

    assert entries(db_session) == [("/first", 1, 2)]
    assert exits(db_session) == [("/second", 1, 2)]


def test_the_same_visitor_on_two_days_is_two_visits(db_session):
    """What the salt rotation guarantees in production, asserted directly.

    The partition is the visitor *and* the day, so a visit cannot straddle
    midnight even if an ID somehow did -- which is what makes these numbers
    additive across days.
    """
    other = DAY + dt.timedelta(days=1)
    add(db_session, visitor="a", path="/monday", minutes=0)
    add(db_session, visitor="a", path="/tuesday", minutes=0, day=other)

    assert bounces(db_session, first=DAY, last=other) == 2
    assert entries(db_session, first=DAY, last=other) == [
        ("/monday", 1, 1),
        ("/tuesday", 1, 1),
    ]


def test_entrances_are_ranked_by_visits_and_tied_names_sort_predictably(db_session):
    add(db_session, visitor="a", path="/popular", minutes=0)
    add(db_session, visitor="b", path="/popular", minutes=0)
    add(db_session, visitor="c", path="/beta", minutes=0)
    add(db_session, visitor="d", path="/alpha", minutes=0)

    assert entries(db_session) == [("/popular", 2, 2), ("/alpha", 1, 1), ("/beta", 1, 1)]


def test_a_limit_keeps_the_top_of_the_ranking(db_session):
    add(db_session, visitor="a", path="/popular", minutes=0)
    add(db_session, visitor="b", path="/popular", minutes=0)
    add(db_session, visitor="c", path="/quiet", minutes=0)

    assert visits.boundary_pages(
        db_session,
        site_id=SITE_DOMAIN,
        first_day=DAY,
        last_day=DAY,
        edge=Edge.START,
        limit=1,
    ) == [("/popular", 2, 2)]


def test_another_site_is_not_counted(db_session):
    add(db_session, visitor="a", path="/", minutes=0)
    db_session.add(
        Event(
            site_id="somewhere-else.example",
            visitor_id="b",
            pathname="/theirs",
            timestamp=NOON,
            day=DAY,
            hour=12,
        )
    )
    db_session.commit()

    assert entries(db_session) == [("/", 1, 1)]
    assert bounces(db_session) == 1


def test_a_period_with_no_traffic_answers_zero(db_session):
    assert bounces(db_session) == 0
    assert entries(db_session) == []
    assert exits(db_session) == []


def test_the_visit_shape_compiles_on_postgres():
    """CI runs the suite twice; a developer's machine usually has one database.

    Window functions are standard SQL and both dialects have had them for
    years, but nothing else in this project uses one, so the compile is worth
    pinning down here rather than discovering it in the Postgres job.
    """
    shape = visits._visit_shape(SITE_DOMAIN, DAY, DAY)
    statement = select(shape.c.pathname, shape.c.views).where(shape.c.from_start == 1)

    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "row_number() OVER" in sql
    assert "PARTITION BY" in sql
    assert "count(*) OVER" in sql
