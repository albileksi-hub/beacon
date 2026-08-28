"""Turning a requested reporting period into a concrete window and bucket size.

Bucket size is derived from the period rather than chosen by the caller: a
year of hourly buckets is 8,760 points, which is neither readable on a chart
nor cheap to compute.
"""

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum

from app.services import zones


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

    @property
    def days(self) -> tuple[dt.date, dt.date]:
        """The site's own calendar days this range covers, both inclusive.

        Every query below the reporting layer compares against the stored
        local day rather than the timestamp, so this is the form they all
        want -- it was being spelled out by hand in five places.
        """
        return self.start.date(), self.end.date()


def _start_of_day(moment: dt.datetime) -> dt.datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def _start_of_month(moment: dt.datetime) -> dt.datetime:
    return _start_of_day(moment).replace(day=1)


def _months_before(moment: dt.datetime, count: int) -> dt.datetime:
    month_index = moment.year * 12 + (moment.month - 1) - count
    return _start_of_month(moment).replace(
        year=month_index // 12, month=month_index % 12 + 1
    )


def resolve(
    period: Period, *, now: dt.datetime | None = None, timezone: str = zones.DEFAULT
) -> TimeRange:
    """Expand a period into the window it covers, in the site's own zone.

    "Today" has to mean today where the site is, not where the server is: a
    dashboard in Los Angeles that rolls over at four in the afternoon is simply
    wrong.

    Ranges are inclusive of the current bucket, so "7d" means today plus the
    six days before it -- seven points on the chart, not eight.
    """
    end = (now or dt.datetime.now(dt.UTC)).astimezone(zones.zone(timezone))

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
            # Walked in absolute time and converted back, rather than by adding
            # an hour to the wall clock. A local day is not always 24 hours
            # long, and the events themselves are bucketed by the local hour
            # they actually fell in -- so counting wall-clock hours produced a
            # 02:00 bucket on the morning the clocks go forward, which no event
            # can ever land in because that hour does not exist.
            #
            # The reverse day has 25 hours and 24 distinct hour values: the
            # repeated hour is one bucket holding both, which is what the
            # stored (day, hour) pair says too. Deduplicated for that reason,
            # and labels carry their date, so this cannot collapse two days.
            zone = time_range.start.tzinfo
            cursor = time_range.start.replace(minute=0, second=0, microsecond=0)
            last = time_range.end.astimezone(dt.UTC)
            step = dt.timedelta(hours=1)
            seen: set[str] = set()

            while cursor.astimezone(dt.UTC) <= last:
                label = cursor.astimezone(zone).strftime(fmt)
                if label not in seen:
                    seen.add(label)
                    labels.append(label)
                cursor = cursor.astimezone(dt.UTC) + step
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


def preceding(time_range: TimeRange) -> TimeRange:
    """The window of equal length immediately before this one.

    Shifted by whole days so the comparison lands on the same grain the
    aggregates are built on. For "today" that makes the comparison yesterday
    up to this same time, which is the honest one -- comparing a half-finished
    day against a whole one would show a fall every morning.
    """
    first, last = time_range.days
    span = dt.timedelta(days=(last - first).days + 1)
    return TimeRange(time_range.start - span, time_range.end - span, time_range.interval)
