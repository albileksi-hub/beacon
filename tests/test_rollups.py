import datetime as dt

from sqlalchemy import delete, select

from app.models import DailyStat, Event, HourlyStat
from app.services import reports, rollups
from app.services.rollups import TOTAL, VALUE_LIMIT
from app.services.timeranges import Period, resolve
from tests.conftest import with_local_bucket

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
    db.add(Event(**with_local_bucket(defaults | overrides)))
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

    by_hour = {row.hour: row.visitors for row in db_session.scalars(select(HourlyStat))}
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


def _aged_event(db, days_ago: int, visitor: str):
    add_event(db, visitor_id=visitor, timestamp=NOON - dt.timedelta(days=days_ago))


def test_retention_is_off_by_default(db_session):
    _aged_event(db_session, 400, "ancient")
    rollups.rebuild_day(db_session, site_id=SITE, day=DAY - dt.timedelta(days=400))

    assert rollups.purge_expired_events(db_session, retention_days=0, today=DAY) == 0
    assert len(db_session.scalars(select(Event)).all()) == 1


def test_a_reckless_retention_setting_is_refused(db_session):
    """Shorter than the refresh window would delete days still being rebuilt."""
    _aged_event(db_session, 30, "old")
    rollups.refresh(db_session, days_back=40, today=DAY)

    deleted = rollups.purge_expired_events(
        db_session, retention_days=rollups.MINIMUM_RETENTION_DAYS - 1, today=DAY
    )

    assert deleted == 0
    assert db_session.scalars(select(Event)).all() != []


def test_events_past_the_retention_window_are_deleted(db_session):
    _aged_event(db_session, 60, "old")
    _aged_event(db_session, 3, "recent")
    rollups.refresh(db_session, days_back=90, today=DAY)

    deleted = rollups.purge_expired_events(db_session, retention_days=30, today=DAY)

    assert deleted == 1
    remaining = db_session.scalars(select(Event)).all()
    assert [event.visitor_id for event in remaining] == ["recent"]


def test_a_site_without_aggregates_is_left_alone(db_session):
    """Otherwise its history would go to a job that could not rebuild it."""
    _aged_event(db_session, 60, "old")

    deleted = rollups.purge_expired_events(db_session, retention_days=30, today=DAY)

    assert deleted == 0
    assert db_session.scalars(select(Event)).all() != []


def test_the_dashboard_still_reports_purged_days(db_session):
    """The whole point: the aggregates outlive the rows they were built from."""
    for offset in (60, 61, 62):
        _aged_event(db_session, offset, f"visitor-{offset}")
    rollups.refresh(db_session, days_back=90, today=DAY)

    before = reports.summary(
        db_session, site_id=SITE, time_range=resolve(Period.LAST_12_MONTHS, now=NOON)
    )
    rollups.purge_expired_events(db_session, retention_days=30, today=DAY)
    after = reports.summary(
        db_session, site_id=SITE, time_range=resolve(Period.LAST_12_MONTHS, now=NOON)
    )

    assert db_session.scalars(select(Event)).all() == []
    assert before == after
    assert after.visitors == 3


def test_a_rebuild_refuses_a_day_whose_raw_events_are_gone(db_session, site):
    """The aggregates are the only remaining copy, so a rebuild would destroy them.

    A rebuild deletes the day's rows before recomputing. Once retention has
    purged the events behind them there is nothing to recompute from, so the
    delete is the whole operation.
    """
    _aged_event(db_session, 60, "old")
    _aged_event(db_session, 3, "recent")
    rollups.refresh(db_session, days_back=90, today=DAY)
    old_day = DAY - dt.timedelta(days=60)
    assert totals_for(db_session, day=old_day) is not None

    rollups.purge_expired_events(db_session, retention_days=30, today=DAY)
    written = rollups.rebuild_day(db_session, site_id=SITE, day=old_day)

    assert written == 0
    assert totals_for(db_session, day=old_day) is not None, "the day was destroyed"


def test_the_hourly_rebuild_refuses_a_purged_day_too(db_session, site):
    _aged_event(db_session, 60, "old")
    _aged_event(db_session, 3, "recent")
    rollups.refresh(db_session, days_back=90, today=DAY)
    old_day = DAY - dt.timedelta(days=60)

    rollups.purge_expired_events(db_session, retention_days=30, today=DAY)
    written = rollups.rebuild_hours(db_session, site_id=SITE, day=old_day)

    assert written == 0
    hours = db_session.scalars(
        select(HourlyStat).where(HourlyStat.site_id == SITE, HourlyStat.day == old_day)
    ).all()
    assert hours != [], "the hourly rows were destroyed"


def test_the_documented_backfill_survives_retention(db_session, site):
    """DESIGN.md recommends both of these, and together they used to be fatal.

    `BEACON_RAW_EVENT_RETENTION_DAYS=30` and `manage.py rollup --days 400` --
    the second deleted every aggregate the first had made unrebuildable, which
    on a seeded database was 69% of the history.
    """
    for offset in (60, 61, 62, 3):
        _aged_event(db_session, offset, f"visitor-{offset}")
    rollups.refresh(db_session, days_back=90, today=DAY)

    period = resolve(Period.LAST_12_MONTHS, now=NOON)
    before = reports.summary(db_session, site_id=SITE, time_range=period)
    rollups.purge_expired_events(db_session, retention_days=30, today=DAY)

    rollups.refresh(db_session, days_back=400, today=DAY)

    after = reports.summary(db_session, site_id=SITE, time_range=period)
    assert after == before, "the backfill destroyed history retention had made permanent"


def test_a_day_still_backed_by_raw_events_rebuilds_normally(db_session):
    """The guard has to refuse the unrebuildable without refusing everything.

    A guard that returned False always would satisfy the tests above while
    quietly turning the rollup job into a no-op.
    """
    add_event(db_session, visitor_id="a")

    assert rollups.rebuild_day(db_session, site_id=SITE, day=DAY) > 0
    assert totals_for(db_session) is not None


def test_events_deleted_for_any_other_reason_still_clear_their_aggregates(db_session):
    """The distinction the watermark exists to draw.

    Retention makes aggregates irreplaceable. Every other way of losing raw
    events -- spam removed, a bad deploy rolled back, a test run cleaned up --
    leaves the rollups simply wrong, and a rebuild is how they get fixed. A
    guard that inferred "no events" from what survives could not tell those
    apart and would leave the wrong numbers standing forever.
    """
    add_event(db_session, visitor_id="a")
    rollups.rebuild_day(db_session, site_id=SITE, day=DAY)
    assert totals_for(db_session) is not None

    db_session.execute(delete(Event))
    db_session.commit()
    rollups.rebuild_day(db_session, site_id=SITE, day=DAY)

    assert db_session.scalars(select(DailyStat)).all() == []
    assert rollups.purged_through(db_session, site_id=SITE) is None


def test_the_watermark_is_recorded_where_a_rebuild_will_read_it(db_session, site):
    _aged_event(db_session, 60, "old")
    rollups.refresh(db_session, days_back=90, today=DAY)

    assert rollups.purged_through(db_session, site_id=SITE) is None
    rollups.purge_expired_events(db_session, retention_days=30, today=DAY)

    marked = rollups.purged_through(db_session, site_id=SITE)
    assert marked == DAY - dt.timedelta(days=30)
    assert rollups.can_rebuild(marked, marked) is False
    assert rollups.can_rebuild(marked + dt.timedelta(days=1), marked) is True
