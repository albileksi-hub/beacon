import datetime as dt
from pathlib import Path

import pytest

from app.models import Event
from app.services import stats
from app.services.stats import BreakdownProperty
from app.services.timeranges import Period, resolve
from tests.conftest import with_local_bucket

NOW = dt.datetime(2026, 8, 18, 12, 0, tzinfo=dt.UTC)
SITE = "blue-mug.example"


def add_event(db, **overrides):
    defaults = {
        "site_id": SITE,
        "visitor_id": "visitor-1",
        "pathname": "/",
        "timestamp": NOW,
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


@pytest.fixture
def month():
    return resolve(Period.LAST_30_DAYS, now=NOW)


def test_summary_counts_visitors_and_pageviews_separately(db_session, month):
    add_event(db_session, visitor_id="a", pathname="/")
    add_event(db_session, visitor_id="a", pathname="/about")
    add_event(db_session, visitor_id="b", pathname="/")

    result = stats.summary(db_session, site_id=SITE, time_range=month)

    assert result.visitors == 2
    assert result.pageviews == 3
    assert result.views_per_visitor == 1.5


def test_summary_of_an_empty_site_is_all_zeroes(db_session, month):
    result = stats.summary(db_session, site_id=SITE, time_range=month)

    assert (result.visitors, result.pageviews, result.views_per_visitor) == (0, 0, 0.0)


def test_one_site_never_sees_another_sites_traffic(db_session, month):
    add_event(db_session, site_id=SITE, visitor_id="a")
    add_event(db_session, site_id="someone-else.example", visitor_id="b")
    add_event(db_session, site_id="someone-else.example", visitor_id="c")

    assert stats.summary(db_session, site_id=SITE, time_range=month).visitors == 1


def test_events_outside_the_window_are_excluded(db_session, month):
    add_event(db_session, visitor_id="recent", timestamp=NOW)
    add_event(db_session, visitor_id="ancient", timestamp=NOW - dt.timedelta(days=90))

    assert stats.summary(db_session, site_id=SITE, time_range=month).visitors == 1


def test_timeseries_zero_fills_days_without_traffic(db_session, month):
    add_event(db_session, visitor_id="a", timestamp=NOW)
    add_event(db_session, visitor_id="b", timestamp=NOW - dt.timedelta(days=2))

    points = stats.timeseries(db_session, site_id=SITE, time_range=month)

    assert len(points) == 30
    by_bucket = {point.bucket: point for point in points}
    assert by_bucket["2026-08-18"].visitors == 1
    assert by_bucket["2026-08-16"].visitors == 1
    assert by_bucket["2026-08-17"].visitors == 0
    assert by_bucket["2026-08-17"].pageviews == 0


def test_timeseries_buckets_by_hour_for_today(db_session):
    add_event(db_session, visitor_id="a", timestamp=NOW.replace(hour=9))
    add_event(db_session, visitor_id="b", timestamp=NOW.replace(hour=9, minute=45))

    points = stats.timeseries(db_session, site_id=SITE, time_range=resolve(Period.TODAY, now=NOW))

    by_bucket = {point.bucket: point for point in points}
    assert by_bucket["2026-08-18T09:00:00"].visitors == 2
    assert by_bucket["2026-08-18T08:00:00"].visitors == 0


def test_breakdown_ranks_pages_by_unique_visitors(db_session, month):
    add_event(db_session, visitor_id="a", pathname="/popular")
    add_event(db_session, visitor_id="b", pathname="/popular")
    add_event(db_session, visitor_id="c", pathname="/quiet")

    rows = stats.breakdown(db_session, site_id=SITE, time_range=month, prop=BreakdownProperty.PAGE)

    assert [(row.value, row.visitors) for row in rows] == [("/popular", 2), ("/quiet", 1)]


def test_breakdown_respects_the_limit(db_session, month):
    for index in range(5):
        add_event(db_session, visitor_id=f"v{index}", pathname=f"/page-{index}")

    rows = stats.breakdown(
        db_session, site_id=SITE, time_range=month, prop=BreakdownProperty.PAGE, limit=3
    )

    assert len(rows) == 3


def test_unknown_countries_are_bucketed_rather_than_dropped(db_session, month):
    # Silently discarding them would make the percentages fail to add up.
    add_event(db_session, visitor_id="a", country="DE")
    add_event(db_session, visitor_id="b", country=None)

    rows = stats.breakdown(
        db_session, site_id=SITE, time_range=month, prop=BreakdownProperty.COUNTRY
    )

    assert {row.value for row in rows} == {"DE", "Unknown"}


@pytest.mark.parametrize(
    ("prop", "field", "value"),
    [
        (BreakdownProperty.SOURCE, "source", "Hacker News"),
        (BreakdownProperty.DEVICE, "device", "mobile"),
        (BreakdownProperty.BROWSER, "browser", "Firefox"),
        (BreakdownProperty.OS, "os", "Linux"),
    ],
)
def test_every_dimension_can_be_broken_down(db_session, month, prop, field, value):
    add_event(db_session, visitor_id="a", **{field: value})

    rows = stats.breakdown(db_session, site_id=SITE, time_range=month, prop=prop)

    assert [row.value for row in rows] == [value]


def test_live_counts_only_the_last_few_minutes(db_session):
    add_event(db_session, visitor_id="here-now", timestamp=NOW - dt.timedelta(minutes=1))
    add_event(db_session, visitor_id="long-gone", timestamp=NOW - dt.timedelta(minutes=30))

    result = stats.live_visitors(db_session, site_id=SITE, now=NOW)

    assert result.visitors == 1
    assert result.window_minutes == 5


def test_live_count_is_zero_on_a_quiet_site(db_session):
    assert stats.live_visitors(db_session, site_id=SITE, now=NOW).visitors == 0


def test_no_reporting_query_needs_dialect_specific_date_sql():
    """The architectural guard that replaced two dialect tests.

    Buckets are decided at ingest and stored on the event, so no query has to
    truncate a timestamp -- which is what used to make the reporting SQL differ
    between SQLite and Postgres. If that ever creeps back, this fails.
    """
    offenders = []
    for module in (Path(__file__).parent.parent / "app").rglob("*.py"):
        body = module.read_text(encoding="utf-8")
        for call in ("func.strftime", "func.date_trunc", "func.to_char"):
            if call in body:
                offenders.append(f"{module.name}: {call}")

    assert offenders == []


def test_custom_events_do_not_count_as_pageviews(db_session, month):
    """Otherwise every site's pageviews inflate the day it tracks a sign-up."""
    add_event(db_session, visitor_id="a", name="pageview")
    add_event(db_session, visitor_id="a", name="signup")
    add_event(db_session, visitor_id="a", name="add-to-basket")

    result = stats.summary(db_session, site_id=SITE, time_range=month)

    assert result.pageviews == 1
    assert result.visitors == 1


def test_somebody_who_only_fired_a_goal_still_counts_as_a_visitor(db_session, month):
    add_event(db_session, visitor_id="quiet", name="signup")

    assert stats.summary(db_session, site_id=SITE, time_range=month).visitors == 1


def test_the_goals_breakdown_leaves_out_page_reads(db_session, month):
    add_event(db_session, visitor_id="a", name="pageview")
    add_event(db_session, visitor_id="a", name="signup")
    add_event(db_session, visitor_id="b", name="signup")
    add_event(db_session, visitor_id="c", name="add-to-basket")

    rows = stats.breakdown(
        db_session, site_id=SITE, time_range=month, prop=BreakdownProperty.EVENT
    )

    assert [(row.value, row.visitors) for row in rows] == [
        ("signup", 2),
        ("add-to-basket", 1),
    ]


def test_the_campaign_breakdown_ignores_untagged_traffic(db_session, month):
    """A "(none)" row would otherwise dwarf every real campaign on every site."""
    add_event(db_session, visitor_id="a", campaign="spring", medium="email")
    add_event(db_session, visitor_id="b", campaign="spring", medium="email")
    add_event(db_session, visitor_id="c")  # ordinary visit, no tags

    campaigns_seen = stats.breakdown(
        db_session, site_id=SITE, time_range=month, prop=BreakdownProperty.CAMPAIGN
    )
    mediums = stats.breakdown(
        db_session, site_id=SITE, time_range=month, prop=BreakdownProperty.MEDIUM
    )

    assert [(row.value, row.visitors) for row in campaigns_seen] == [("spring", 2)]
    assert [(row.value, row.visitors) for row in mediums] == [("email", 2)]
