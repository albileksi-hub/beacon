"""Turning a requested reporting period into a concrete window and bucket size.

Bucket size is derived from the period rather than chosen by the caller: a
year of hourly buckets is 8,760 points, which is neither readable on a chart
nor cheap to compute.
"""

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum


class Period(StrEnum):
    TODAY = "today"
    LAST_7_DAYS = "7d"
    LAST_30_DAYS = "30d"
    LAST_6_MONTHS = "6mo"
    LAST_12_MONTHS = "12mo"


class Interval(StrEnum):
    HOUR = "hour"
    DAY = "day"
    MONTH = "month"


LABEL_FORMATS = {
    Interval.HOUR: "%Y-%m-%dT%H:00:00",
    Interval.DAY: "%Y-%m-%d",
    Interval.MONTH: "%Y-%m-01",
}


@dataclass(frozen=True, slots=True)
class TimeRange:
    start: dt.datetime
    end: dt.datetime
    interval: Interval


def _start_of_day(moment: dt.datetime) -> dt.datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def _start_of_month(moment: dt.datetime) -> dt.datetime:
    return _start_of_day(moment).replace(day=1)


def _months_before(moment: dt.datetime, count: int) -> dt.datetime:
    month_index = moment.year * 12 + (moment.month - 1) - count
    return _start_of_month(moment).replace(
        year=month_index // 12, month=month_index % 12 + 1
    )


def resolve(period: Period, *, now: dt.datetime | None = None) -> TimeRange:
    """Expand a period into the window it covers.

    Ranges are inclusive of the current bucket, so "7d" means today plus the
    six days before it -- seven points on the chart, not eight.
    """
    end = now or dt.datetime.now(dt.UTC)

    match period:
        case Period.TODAY:
            return TimeRange(_start_of_day(end), end, Interval.HOUR)
        case Period.LAST_7_DAYS:
            return TimeRange(_start_of_day(end) - dt.timedelta(days=6), end, Interval.DAY)
        case Period.LAST_30_DAYS:
            return TimeRange(_start_of_day(end) - dt.timedelta(days=29), end, Interval.DAY)
        case Period.LAST_6_MONTHS:
            return TimeRange(_months_before(end, 5), end, Interval.MONTH)
        case Period.LAST_12_MONTHS:
            return TimeRange(_months_before(end, 11), end, Interval.MONTH)

    raise ValueError(f"unsupported period: {period}")


def bucket_labels(time_range: TimeRange) -> list[str]:
    """Every bucket in the range, including the ones with no traffic.

    A chart with holes in it reads as broken, so empty buckets are produced
    here and zero-filled by the caller rather than being absent from the SQL
    result.
    """
    fmt = LABEL_FORMATS[time_range.interval]
    labels: list[str] = []

    match time_range.interval:
        case Interval.HOUR:
            cursor = time_range.start.replace(minute=0, second=0, microsecond=0)
            step = dt.timedelta(hours=1)
            while cursor <= time_range.end:
                labels.append(cursor.strftime(fmt))
                cursor += step
        case Interval.DAY:
            cursor = _start_of_day(time_range.start)
            step = dt.timedelta(days=1)
            while cursor <= time_range.end:
                labels.append(cursor.strftime(fmt))
                cursor += step
        case Interval.MONTH:
            cursor = _start_of_month(time_range.start)
            while cursor <= time_range.end:
                labels.append(cursor.strftime(fmt))
                cursor = _months_before(cursor, -1)

    return labels
