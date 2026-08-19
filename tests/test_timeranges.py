import datetime as dt

import pytest

from app.services.timeranges import (
    Interval,
    Period,
    bucket_labels,
    preceding,
    resolve,
)

NOON = dt.datetime(2026, 8, 18, 12, 30, tzinfo=dt.UTC)
FEBRUARY = dt.datetime(2026, 2, 9, tzinfo=dt.UTC)


def test_today_starts_at_midnight_and_buckets_by_hour():
    time_range = resolve(Period.TODAY, now=NOON)

    assert time_range.start == dt.datetime(2026, 8, 18, 0, 0, tzinfo=dt.UTC)
    assert time_range.interval is Interval.HOUR
    assert len(bucket_labels(time_range)) == 13  # 00:00 through 12:00


@pytest.mark.parametrize(
    ("period", "expected_buckets", "interval"),
    [
        (Period.LAST_7_DAYS, 7, Interval.DAY),
        (Period.LAST_30_DAYS, 30, Interval.DAY),
        (Period.LAST_6_MONTHS, 6, Interval.MONTH),
        (Period.LAST_12_MONTHS, 12, Interval.MONTH),
    ],
)
def test_periods_produce_the_expected_number_of_buckets(period, expected_buckets, interval):
    time_range = resolve(period, now=NOON)

    assert time_range.interval is interval
    assert len(bucket_labels(time_range)) == expected_buckets


def test_ranges_include_the_current_bucket():
    # Seven days means today plus six, not eight points on the chart.
    labels = bucket_labels(resolve(Period.LAST_7_DAYS, now=NOON))

    assert labels[0] == "2026-08-12"
    assert labels[-1] == "2026-08-18"


def test_month_arithmetic_crosses_the_year_boundary():
    labels = bucket_labels(resolve(Period.LAST_12_MONTHS, now=FEBRUARY))

    assert labels[0] == "2025-03-01"
    assert labels[-1] == "2026-02-01"


def test_six_month_range_starts_on_the_first_of_the_month():
    time_range = resolve(Period.LAST_6_MONTHS, now=NOON)

    assert time_range.start == dt.datetime(2026, 3, 1, tzinfo=dt.UTC)


def test_hour_labels_are_iso_like():
    labels = bucket_labels(resolve(Period.TODAY, now=NOON))

    assert labels[0] == "2026-08-18T00:00:00"
    assert labels[-1] == "2026-08-18T12:00:00"


def test_unsupported_period_is_rejected():
    with pytest.raises(ValueError, match="unsupported period"):
        resolve("nonsense", now=NOON)


def test_the_preceding_window_is_the_same_length_and_ends_where_this_one_starts():
    this_week = resolve(Period.LAST_7_DAYS, now=NOON)
    last_week = preceding(this_week)

    assert bucket_labels(last_week)[0] == "2026-08-05"
    assert bucket_labels(last_week)[-1] == "2026-08-11"
    assert len(bucket_labels(last_week)) == len(bucket_labels(this_week))


def test_today_is_compared_against_yesterday_at_the_same_time():
    """Comparing a half-finished day against a whole one shows a fall every morning."""
    yesterday = preceding(resolve(Period.TODAY, now=NOON))

    assert yesterday.start == dt.datetime(2026, 8, 17, 0, 0, tzinfo=dt.UTC)
    assert yesterday.end == dt.datetime(2026, 8, 17, 12, 30, tzinfo=dt.UTC)
