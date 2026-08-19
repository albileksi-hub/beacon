import datetime as dt

from sqlalchemy import delete, select

from app.models import DailyStat, Event, HourlyStat
from app.services import rollups
from app.services.rollups import TOTAL, VALUE_LIMIT

DAY = dt.date(2026, 8, 18)
NOON = dt.datetime(2026, 8, 18, 12, 0, tzinfo=dt.UTC)
SITE = "blue-mug.example"


def add_event(db, **overrides):
    defaults = {
        "site_id": SITE,
        "visitor_id": "visitor-1",
        "pathname": "/",
        "timestamp": NOON,
        "name": "pageview",
        "source": "Direct",
        "browser": "Chrome",
        "os": "Windows",
        "device": "desktop",
        "country": "DE",
        "screen": "Desktop",
    }
    db.add(Event(**(defaults | overrides)))
    db.commit()


def totals_for(db, day=DAY):
    return db.scalar(
        select(DailyStat).where(
            DailyStat.site_id == SITE, DailyStat.day == day, DailyStat.dimension == TOTAL
        )
    )


def test_a_day_rolls_up_to_totals_and_dimensions(db_session):
    add_event(db_session, visitor_id="a", pathname="/one")
    add_event(db_session, visitor_id="a", pathname="/two")
    add_event(db_session, visitor_id="b", pathname="/one")

    rollups.rebuild_day(db_session, site_id=SITE, day=DAY)

    total = totals_for(db_session)
    assert (total.visitors, total.pageviews) == (2, 3)

    pages = {
        row.value: row.visitors
        for row in db_session.scalars(select(DailyStat).where(DailyStat.dimension == "page"))
    }
    assert pages == {"/one": 2, "/two": 1}


def test_rebuilding_is_idempotent(db_session):
    add_event(db_session, visitor_id="a")

    rollups.rebuild_day(db_session, site_id=SITE, day=DAY)
    first = db_session.scalars(select(DailyStat)).all()

    rollups.rebuild_day(db_session, site_id=SITE, day=DAY)
    second = db_session.scalars(select(DailyStat)).all()

    assert len(first) == len(second)
    assert totals_for(db_session).pageviews == 1


def test_rebuilding_clears_rows_that_no_longer_have_events(db_session):
    add_event(db_session, visitor_id="a")
    rollups.rebuild_day(db_session, site_id=SITE, day=DAY)
    assert totals_for(db_session) is not None

    db_session.execute(delete(Event))
    db_session.commit()
    rollups.rebuild_day(db_session, site_id=SITE, day=DAY)

    assert db_session.scalars(select(DailyStat)).all() == []


def test_a_day_with_no_traffic_produces_no_rows(db_session):
    rollups.rebuild_day(db_session, site_id=SITE, day=DAY)

    assert db_session.scalars(select(DailyStat)).all() == []


def test_events_from_neighbouring_days_are_not_counted(db_session):
    add_event(db_session, visitor_id="today", timestamp=NOON)
    add_event(db_session, visitor_id="yesterday", timestamp=NOON - dt.timedelta(days=1))
    add_event(db_session, visitor_id="tomorrow", timestamp=NOON + dt.timedelta(days=1))

    rollups.rebuild_day(db_session, site_id=SITE, day=DAY)

    assert totals_for(db_session).visitors == 1


def test_hours_roll_up_separately(db_session):
    add_event(db_session, visitor_id="a", timestamp=NOON.replace(hour=9))
    add_event(db_session, visitor_id="b", timestamp=NOON.replace(hour=9, minute=40))
    add_event(db_session, visitor_id="c", timestamp=NOON.replace(hour=14))

    rollups.rebuild_hours(db_session, site_id=SITE, day=DAY)

    by_hour = {
        row.hour.hour: row.visitors for row in db_session.scalars(select(HourlyStat))
    }
    assert by_hour == {9: 2, 14: 1}


def test_long_values_are_truncated_to_fit_the_index(db_session):
    add_event(db_session, visitor_id="a", pathname="/" + "x" * 900)

    rollups.rebuild_day(db_session, site_id=SITE, day=DAY)

    page = db_session.scalar(select(DailyStat).where(DailyStat.dimension == "page"))
    assert len(page.value) == VALUE_LIMIT


def test_refresh_covers_every_site(db_session):
    add_event(db_session, site_id="one.example", visitor_id="a")
    add_event(db_session, site_id="two.example", visitor_id="b")

    rebuilt = rollups.refresh(db_session, days_back=1, today=DAY)

    assert rebuilt == 2
    sites = set(db_session.scalars(select(DailyStat.site_id).distinct()))
    assert sites == {"one.example", "two.example"}


def test_refresh_reaches_back_far_enough_to_catch_late_events(db_session):
    """An event can land after midnight for the day that just ended."""
    add_event(db_session, visitor_id="late", timestamp=NOON - dt.timedelta(days=1))

    rollups.refresh(db_session, days_back=rollups.RECENT_DAYS, today=DAY)

    assert totals_for(db_session, day=DAY - dt.timedelta(days=1)) is not None
