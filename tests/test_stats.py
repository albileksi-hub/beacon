import datetime as dt
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql, sqlite

from app.models import Event
from app.services import stats
from app.services.stats import BreakdownProperty
from app.services.timeranges import Interval, Period, resolve

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
        "screen_width": 1920,
    }
    db.add(Event(**(defaults | overrides)))
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


class _FakeSession:
    """Just enough Session to answer "which dialect are we on?"."""

    def __init__(self, dialect_name):
        self._dialect_name = dialect_name

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name=self._dialect_name))


def _compiled(expression, dialect):
    return str(expression.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))


def test_sqlite_buckets_with_strftime():
    expression = stats._bucket_column(_FakeSession("sqlite"), Interval.DAY)

    assert "strftime" in _compiled(expression, sqlite.dialect())


def test_postgres_buckets_with_date_trunc():
    # Postgres never runs in the test suite, so this is the only thing standing
    # between a dialect typo and a broken dashboard in production.
    expression = stats._bucket_column(_FakeSession("postgresql"), Interval.MONTH)
    compiled = _compiled(expression, postgresql.dialect())

    assert "date_trunc" in compiled
    assert "to_char" in compiled
    assert "YYYY-MM-01" in compiled
