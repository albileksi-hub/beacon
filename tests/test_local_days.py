"""Days belong to the site, not to the server.

The point of the whole arrangement: a site's day is decided in its own zone,
and the visitor salt rotates on that same boundary. These tests are written
around the case that used to be wrong.
"""

import datetime as dt

from sqlalchemy import select

from app.models import DailyStat, Event
from app.services import reports, rollups, zones
from app.services.timeranges import Period, bucket_labels, resolve
from app.services.visitors import current_salt, visitor_id

BERLIN = "Europe/Berlin"
SITE = "berlin.example"

# Either side of UTC midnight, but the same Berlin day: 01:30 and 02:30 on the
# 19th, local time.
BEFORE_UTC_MIDNIGHT = dt.datetime(2026, 8, 18, 23, 30, tzinfo=dt.UTC)
AFTER_UTC_MIDNIGHT = dt.datetime(2026, 8, 19, 0, 30, tzinfo=dt.UTC)


def _record(db, moment: dt.datetime, address: str = "203.0.113.7") -> Event:
    """What the collector does, for one event."""
    day, hour = zones.local_parts(moment, BERLIN)
    event = Event(
        site_id=SITE,
        timestamp=moment,
        day=day,
        hour=hour,
        visitor_id=visitor_id(
            salt=current_salt(db, site_id=SITE, day=day),
            site_id=SITE,
            ip=address,
            user_agent="Chrome",
        ),
        name="pageview",
        pathname="/",
        source="Direct",
        browser="Chrome",
        os="Windows",
        device="desktop",
        country="DE",
        screen="Laptop",
    )
    db.add(event)
    db.commit()
    return event


def test_both_halves_of_a_local_night_land_on_the_same_day(db_session):
    first = _record(db_session, BEFORE_UTC_MIDNIGHT)
    second = _record(db_session, AFTER_UTC_MIDNIGHT)

    assert first.day == second.day == dt.date(2026, 8, 19)
    # And on different UTC days, which is exactly what used to split them.
    assert first.timestamp.date() != second.timestamp.date()


def test_one_person_across_utc_midnight_is_one_visitor(db_session):
    """The reason the salt rotates locally rather than at UTC midnight.

    With a salt turning over at 02:00 Berlin time, this person would hold two
    identities inside one Berlin day and the day's total would overcount.
    """
    first = _record(db_session, BEFORE_UTC_MIDNIGHT)
    second = _record(db_session, AFTER_UTC_MIDNIGHT)

    assert first.visitor_id == second.visitor_id


def test_the_day_totals_that_follow_are_right(db_session):
    _record(db_session, BEFORE_UTC_MIDNIGHT)
    _record(db_session, AFTER_UTC_MIDNIGHT)

    rollups.rebuild_day(db_session, site_id=SITE, day=dt.date(2026, 8, 19))

    total = db_session.scalar(
        select(DailyStat).where(DailyStat.dimension == "total", DailyStat.site_id == SITE)
    )
    assert (total.visitors, total.pageviews) == (1, 2)


def test_different_people_are_still_different(db_session):
    first = _record(db_session, BEFORE_UTC_MIDNIGHT, address="203.0.113.7")
    second = _record(db_session, AFTER_UTC_MIDNIGHT, address="203.0.113.8")

    assert first.visitor_id != second.visitor_id


def test_the_hour_recorded_is_the_local_hour(db_session):
    event = _record(db_session, BEFORE_UTC_MIDNIGHT)

    assert event.hour == 1  # 01:30 in Berlin, 23:30 in UTC


def test_today_means_today_where_the_site_is(db_session):
    """A Los Angeles dashboard must not roll over at four in the afternoon."""
    # 05:00 UTC on the 19th is already 07:00 on the 19th in Berlin, and still
    # 22:00 on the 18th in Los Angeles.
    moment = dt.datetime(2026, 8, 19, 5, 0, tzinfo=dt.UTC)

    berlin = resolve(Period.TODAY, now=moment, timezone=BERLIN)
    los_angeles = resolve(Period.TODAY, now=moment, timezone="America/Los_Angeles")

    assert berlin.start.date() == dt.date(2026, 8, 19)
    assert los_angeles.start.date() == dt.date(2026, 8, 18)


def test_the_report_reads_the_local_day(db_session):
    _record(db_session, BEFORE_UTC_MIDNIGHT)
    _record(db_session, AFTER_UTC_MIDNIGHT)
    rollups.refresh(db_session, days_back=3, today=dt.date(2026, 8, 19))

    berlin_19th = resolve(
        Period.TODAY, now=AFTER_UTC_MIDNIGHT, timezone=BERLIN
    )
    summary = reports.summary(db_session, site_id=SITE, time_range=berlin_19th)

    assert summary.visitors == 1
    assert summary.pageviews == 2


def _real_local_hours(day: dt.date) -> list[int]:
    """The hours that actually occur on a day, derived as ingest derives them."""
    hours: list[int] = []
    cursor = dt.datetime.combine(day, dt.time.min, tzinfo=zones.zone(BERLIN)).astimezone(dt.UTC)
    while True:
        local_day, hour = zones.local_parts(cursor, BERLIN)
        if local_day != day:
            return hours
        hours.append(hour)
        cursor += dt.timedelta(hours=1)


def _today_buckets(day: dt.date) -> list[str]:
    now = dt.datetime.combine(day, dt.time(23, 30), tzinfo=zones.zone(BERLIN))
    return bucket_labels(resolve(Period.TODAY, now=now, timezone=BERLIN))


def test_the_hourly_chart_has_no_bucket_for_an_hour_that_never_happened():
    """The clocks go forward at 02:00, so 29 March 2026 has 23 hours in Berlin.

    Counting wall-clock hours produced 24 buckets and a 02:00 bar that no event
    could ever land in, because ingest derives the hour by converting the
    instant rather than by counting.
    """
    day = dt.date(2026, 3, 29)
    labels = {label[11:16] for label in _today_buckets(day)}
    real = {f"{hour:02d}:00" for hour in _real_local_hours(day)}

    assert len(_real_local_hours(day)) == 23
    assert "02:00" not in labels
    assert labels == real


def test_the_repeated_hour_is_one_bucket_holding_both():
    """The clocks go back, so 25 October 2026 has 25 hours and 24 hour values.

    Both 02:00s are stored as hour 2, so one bucket is the honest shape: the
    labels have to agree with the grain the events were bucketed on.
    """
    day = dt.date(2026, 10, 25)
    labels = [label[11:16] for label in _today_buckets(day)]
    real = _real_local_hours(day)

    assert len(real) == 25, "a 25-hour day"
    assert len(set(real)) == 24, "sharing 24 distinct hour values"
    assert labels == sorted(set(labels)), "no duplicate bucket"
    assert {f"{hour:02d}:00" for hour in real} == set(labels)


def test_an_ordinary_day_still_has_twenty_four_buckets():
    """The guard against fixing the edge case by breaking every other day."""
    labels = _today_buckets(dt.date(2026, 10, 20))

    assert len(labels) == 24
    assert [label[11:16] for label in labels][:3] == ["00:00", "01:00", "02:00"]
