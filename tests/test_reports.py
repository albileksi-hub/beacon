"""The rollups must agree with the raw queries, always.

app.services.stats reads raw events and is the definition of a correct answer.
app.services.reports reads the pre-aggregated tables and is what the dashboard
actually calls. If the two ever disagree, the optimisation has quietly started
lying, so every period and every dimension is compared here.
"""

import datetime as dt
import random

import pytest
from sqlalchemy import distinct, func, select

from app.models import DailyStat, Event
from app.services import reports, rollups, stats
from app.services.stats import BreakdownProperty
from app.services.timeranges import Period, resolve

NOW = dt.datetime(2026, 8, 18, 15, 30, tzinfo=dt.UTC)
SITE = "blue-mug.example"
DAYS_OF_HISTORY = 40

PAGES = ["/", "/products/blue-mug", "/about", "/contact"]
SOURCES = ["Direct", "Google", "Reddit", "Hacker News"]
COUNTRIES = ["DE", "US", "GB", None]
DEVICES = ["desktop", "mobile", "tablet"]
BROWSERS = ["Chrome", "Firefox", "Safari"]
SYSTEMS = ["Windows", "Mac OS X", "Linux"]
SCREENS = ["Phone", "Tablet", "Laptop", "Desktop"]
# A minority of events are goals rather than page reads, so the comparison
# covers the dimension that filters them.
NAMES = ["pageview"] * 8 + ["signup", "add-to-basket"]


@pytest.fixture
def traffic(db_session):
    """Forty days of varied events, then a full rollup rebuild.

    Visitor IDs are minted per day, exactly as the daily salt rotation forces
    in production. That property is what makes daily figures summable, so the
    fixture has to honour it or it would be testing a system that cannot exist.
    """
    random.seed(11)
    events = []

    for offset in range(DAYS_OF_HISTORY):
        day = (NOW - dt.timedelta(days=offset)).date()
        for visitor_number in range(random.randrange(1, 8)):
            visitor_id = f"{day.isoformat()}-{visitor_number}"
            for _ in range(random.randrange(1, 4)):
                moment = dt.datetime.combine(day, dt.time.min, tzinfo=dt.UTC) + dt.timedelta(
                    hours=random.randrange(24), minutes=random.randrange(60)
                )
                if moment > NOW:
                    continue
                events.append(
                    Event(
                        site_id=SITE,
                        visitor_id=visitor_id,
                        timestamp=moment,
                        name=random.choice(NAMES),
                        pathname=random.choice(PAGES),
                        source=random.choice(SOURCES),
                        browser=random.choice(BROWSERS),
                        os=random.choice(SYSTEMS),
                        device=random.choice(DEVICES),
                        country=random.choice(COUNTRIES),
                        screen=random.choice(SCREENS),
                    )
                )

    db_session.add_all(events)
    db_session.commit()
    rollups.refresh(db_session, days_back=DAYS_OF_HISTORY + 2, today=NOW.date())
    return events


def test_the_fixture_actually_produced_traffic(db_session, traffic):
    assert db_session.scalar(select(func.count(Event.id))) > 100
    assert db_session.scalar(select(func.count(DailyStat.id))) > 100


@pytest.mark.parametrize("period", list(Period))
def test_summary_matches_the_raw_query(db_session, traffic, period):
    time_range = resolve(period, now=NOW)

    assert reports.summary(db_session, site_id=SITE, time_range=time_range) == stats.summary(
        db_session, site_id=SITE, time_range=time_range
    )


@pytest.mark.parametrize("period", list(Period))
def test_timeseries_matches_the_raw_query(db_session, traffic, period):
    time_range = resolve(period, now=NOW)

    assert reports.timeseries(db_session, site_id=SITE, time_range=time_range) == stats.timeseries(
        db_session, site_id=SITE, time_range=time_range
    )


@pytest.mark.parametrize("period", [Period.TODAY, Period.LAST_7_DAYS, Period.LAST_30_DAYS])
@pytest.mark.parametrize("prop", list(BreakdownProperty))
def test_breakdown_matches_the_raw_query(db_session, traffic, period, prop):
    time_range = resolve(period, now=NOW)

    assert reports.breakdown(
        db_session, site_id=SITE, time_range=time_range, prop=prop
    ) == stats.breakdown(db_session, site_id=SITE, time_range=time_range, prop=prop)


def test_summing_days_into_a_month_is_sound(db_session, traffic):
    """Only true because a visitor ID cannot survive midnight."""
    time_range = resolve(Period.LAST_12_MONTHS, now=NOW)

    summed_from_days = reports.summary(db_session, site_id=SITE, time_range=time_range).visitors
    counted_distinct = db_session.scalar(
        select(func.count(distinct(Event.visitor_id))).where(
            Event.site_id == SITE,
            Event.timestamp >= time_range.start,
            Event.timestamp <= time_range.end,
        )
    )

    assert summed_from_days == counted_distinct


def test_summing_hours_into_a_day_would_not_be_sound(db_session, traffic):
    """The reason hourly rows are never folded upwards.

    One person browsing at 09:00 and again at 14:00 is one visitor that day but
    appears in two hourly buckets. This is the mistake the daily grain exists to
    avoid, so it is worth pinning down that it really would be a mistake.
    """
    today = resolve(Period.TODAY, now=NOW)

    from_hours = sum(point.visitors for point in reports.timeseries(
        db_session, site_id=SITE, time_range=today
    ))
    from_the_day = reports.summary(db_session, site_id=SITE, time_range=today).visitors

    assert from_hours >= from_the_day
