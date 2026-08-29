"""Windows a person chose, rather than the five this dashboard offers.

Daily rollups make an arbitrary range a plain sum, so the reporting side needed
nothing: the work is in agreeing what a pair of dates means and refusing the
pairs that mean nothing.
"""

import datetime as dt

import pytest

from app.services import timeranges
from app.services.timeranges import (
    MAX_RANGE_DAYS,
    Interval,
    Period,
    bucket_labels,
    resolve_range,
    resolve_window,
)
from tests.conftest import SITE_DOMAIN

BERLIN = "Europe/Berlin"


@pytest.mark.parametrize(
    ("first", "last", "interval", "buckets"),
    [
        # A single day is drawn by the hour, exactly as "today" is.
        (dt.date(2026, 8, 1), dt.date(2026, 8, 1), Interval.HOUR, 24),
        (dt.date(2026, 8, 1), dt.date(2026, 8, 15), Interval.DAY, 15),
        (dt.date(2026, 8, 1), dt.date(2026, 10, 31), Interval.DAY, 92),
        # Past a quarter it becomes months, or a year would be 365 points.
        (dt.date(2026, 1, 1), dt.date(2026, 12, 31), Interval.MONTH, 12),
    ],
)
def test_the_bucket_follows_the_length_of_the_range(first, last, interval, buckets):
    window = resolve_range(first, last)

    assert window.interval is interval
    assert len(bucket_labels(window)) == buckets


def test_both_ends_are_included():
    """What a person means by "the 1st to the 15th"."""
    window = resolve_range(dt.date(2026, 8, 1), dt.date(2026, 8, 15))

    assert window.days == (dt.date(2026, 8, 1), dt.date(2026, 8, 15))


def test_the_range_is_reckoned_in_the_site_zone():
    """The same rule the named periods follow: a day is the site's day."""
    window = resolve_range(dt.date(2026, 8, 1), dt.date(2026, 8, 1), timezone=BERLIN)

    assert window.start.utcoffset() == dt.timedelta(hours=2)
    assert window.start.hour == 0


def test_a_backwards_range_is_refused():
    with pytest.raises(timeranges.InvalidRange, match="comes before"):
        resolve_range(dt.date(2026, 8, 15), dt.date(2026, 8, 1))


def test_an_absurd_range_is_refused():
    """A typo in a URL should not ask for a hundred thousand buckets."""
    first = dt.date(2020, 1, 1)

    with pytest.raises(timeranges.InvalidRange, match="at most"):
        resolve_range(first, first + dt.timedelta(days=MAX_RANGE_DAYS))


def test_the_longest_allowed_range_is_allowed():
    """The boundary itself, so the guard cannot be off by one."""
    first = dt.date(2020, 1, 1)

    window = resolve_range(first, first + dt.timedelta(days=MAX_RANGE_DAYS - 1))

    assert window.interval is Interval.MONTH


def test_no_dates_means_the_named_period():
    assert resolve_window(Period.LAST_7_DAYS, None, None).interval is Interval.DAY


def test_dates_win_over_the_period():
    window = resolve_window(Period.LAST_30_DAYS, dt.date(2026, 8, 1), dt.date(2026, 8, 3))

    assert window.days == (dt.date(2026, 8, 1), dt.date(2026, 8, 3))


@pytest.mark.parametrize(
    ("first", "last"),
    [(dt.date(2026, 8, 1), None), (None, dt.date(2026, 8, 1))],
)
def test_half_a_range_is_a_mistake_worth_naming(first, last):
    """Rather than quietly reading as a month, which is what a default does."""
    with pytest.raises(timeranges.InvalidRange, match="both"):
        resolve_window(Period.LAST_30_DAYS, first, last)


def test_the_api_accepts_a_range(signed_in, site):
    response = signed_in.get(
        f"/api/stats/{SITE_DOMAIN}/summary?from=2026-08-01&to=2026-08-15"
    )

    assert response.status_code == 200
    assert set(response.json()) == {
        "visitors", "pageviews", "views_per_visitor", "bounce_rate", "revenue_minor",
    }


def test_a_range_reaches_every_reporting_endpoint(signed_in, site):
    """One dependency, so nothing is left behind still taking only a period."""
    query = "from=2026-08-01&to=2026-08-15"

    for path in (
        f"/api/stats/{SITE_DOMAIN}/summary?{query}",
        f"/api/stats/{SITE_DOMAIN}/timeseries?{query}",
        f"/api/stats/{SITE_DOMAIN}/breakdown/page?{query}",
        f"/sites/{SITE_DOMAIN}/export.csv?{query}",
    ):
        assert signed_in.get(path).status_code == 200, path


def test_the_timeseries_returns_a_point_for_every_day_asked_for(signed_in, site):
    response = signed_in.get(
        f"/api/stats/{SITE_DOMAIN}/timeseries?from=2026-08-01&to=2026-08-05"
    )

    assert [point["bucket"] for point in response.json()] == [
        "2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04", "2026-08-05",
    ]


@pytest.mark.parametrize(
    "query",
    ["from=2026-08-15&to=2026-08-01", "from=2026-08-01", "to=2026-08-01"],
)
def test_the_api_refuses_a_range_that_means_nothing(signed_in, site, query):
    response = signed_in.get(f"/api/stats/{SITE_DOMAIN}/summary?{query}")

    assert response.status_code == 422


def test_the_dashboard_renders_a_chosen_range(signed_in, site):
    response = signed_in.get(f"/sites/{SITE_DOMAIN}?from=2026-08-01&to=2026-08-15")

    assert response.status_code == 200
    assert "1 Aug 2026 to 15 Aug 2026" in response.text
    assert 'value="2026-08-01"' in response.text


def test_a_bad_range_still_renders_the_page(signed_in, site):
    """A 422 here is a blank screen for a typo in a URL somebody pasted.

    The page comes back on the default period with the reason above the
    numbers, which is the difference between a broken link and a wrong one.
    """
    response = signed_in.get(f"/sites/{SITE_DOMAIN}?from=2026-08-15&to=2026-08-01")

    assert response.status_code == 200
    assert "comes before" in response.text
    assert "30 days, compared with the period before" in response.text


def test_a_chosen_range_says_what_it_is_compared_against(signed_in, site):
    """The tiles carry a comparison however the window was chosen.

    A named period says "compared with the period before" and a chosen one
    used to say only its dates -- leaving a "-8.6%" on screen with nothing to
    read it against. The comparison itself was always there; the sentence
    describing it was not.
    """
    body = signed_in.get(f"/sites/{SITE_DOMAIN}?from=2026-08-01&to=2026-08-15").text

    assert "1 Aug 2026 to 15 Aug 2026" in body
    assert "compared with the 15 days before" in body


def test_a_one_day_window_reads_as_one_day(signed_in, site):
    body = signed_in.get(f"/sites/{SITE_DOMAIN}?from=2026-08-10&to=2026-08-10").text

    assert "compared with the 1 day before" in body
    assert "1 days before" not in body
