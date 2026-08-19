"""The aggregate queries behind the dashboard."""

import datetime as dt
from enum import StrEnum
from typing import Any

from sqlalchemy import Select, distinct, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.functions import Function

from app.models import Event
from app.schemas import BreakdownRow, LiveVisitors, StatsSummary, TimeseriesPoint
from app.services.timeranges import Interval, TimeRange, bucket_labels

LIVE_WINDOW = dt.timedelta(minutes=5)
DEFAULT_BREAKDOWN_LIMIT = 10


class BreakdownProperty(StrEnum):
    PAGE = "page"
    SOURCE = "source"
    COUNTRY = "country"
    DEVICE = "device"
    BROWSER = "browser"
    OS = "os"
    SCREEN = "screen"


# A whitelist, so a request parameter never reaches a column name directly.
BREAKDOWN_COLUMNS = {
    BreakdownProperty.PAGE: Event.pathname,
    BreakdownProperty.SOURCE: Event.source,
    # Visits with no country resolve to a bucket rather than vanishing, which
    # would silently make the percentages not add up.
    BreakdownProperty.COUNTRY: func.coalesce(Event.country, "Unknown"),
    BreakdownProperty.DEVICE: Event.device,
    BreakdownProperty.BROWSER: Event.browser,
    BreakdownProperty.OS: Event.os,
    BreakdownProperty.SCREEN: Event.screen,
}

# SQLite and Postgres disagree completely about date truncation. This module is
# the only place in the codebase that has to know it.
_SQLITE_BUCKETS = {
    Interval.HOUR: "%Y-%m-%dT%H:00:00",
    Interval.DAY: "%Y-%m-%d",
    Interval.MONTH: "%Y-%m-01",
}
_POSTGRES_BUCKETS = {
    Interval.HOUR: 'YYYY-MM-DD"T"HH24:00:00',
    Interval.DAY: "YYYY-MM-DD",
    Interval.MONTH: "YYYY-MM-01",
}


def _visitors() -> Function[int]:
    """Unique visitors. The expensive one: a distinct count over the window."""
    return func.count(distinct(Event.visitor_id))


def _pageviews() -> Function[int]:
    return func.count(Event.id)


def _scoped(statement: Select[Any], site_id: str, time_range: TimeRange) -> Select[Any]:
    return statement.where(
        Event.site_id == site_id,
        Event.timestamp >= time_range.start,
        Event.timestamp <= time_range.end,
    )


def bucket_column(db: Session, interval: Interval) -> Function[str]:
    if db.get_bind().dialect.name == "sqlite":
        return func.strftime(_SQLITE_BUCKETS[interval], Event.timestamp)
    return func.to_char(
        func.date_trunc(interval.value, Event.timestamp), _POSTGRES_BUCKETS[interval]
    )


def summary(db: Session, *, site_id: str, time_range: TimeRange) -> StatsSummary:
    row = db.execute(
        _scoped(
            select(_visitors().label("visitors"), _pageviews().label("pageviews")),
            site_id,
            time_range,
        )
    ).one()

    return StatsSummary.of(visitors=row.visitors, pageviews=row.pageviews)


def timeseries(db: Session, *, site_id: str, time_range: TimeRange) -> list[TimeseriesPoint]:
    bucket = bucket_column(db, time_range.interval)

    rows = db.execute(
        _scoped(
            select(
                bucket.label("bucket"),
                _visitors().label("visitors"),
                _pageviews().label("pageviews"),
            ),
            site_id,
            time_range,
        ).group_by(bucket)
    ).all()

    counted = {row.bucket: row for row in rows}

    # Zero-fill: a chart with holes in it reads as broken.
    return [
        TimeseriesPoint(
            bucket=label,
            visitors=counted[label].visitors if label in counted else 0,
            pageviews=counted[label].pageviews if label in counted else 0,
        )
        for label in bucket_labels(time_range)
    ]


def breakdown(
    db: Session,
    *,
    site_id: str,
    time_range: TimeRange,
    prop: BreakdownProperty,
    limit: int = DEFAULT_BREAKDOWN_LIMIT,
) -> list[BreakdownRow]:
    column = BREAKDOWN_COLUMNS[prop]
    visitors = _visitors()

    rows = db.execute(
        _scoped(
            select(
                column.label("value"),
                visitors.label("visitors"),
                _pageviews().label("pageviews"),
            ),
            site_id,
            time_range,
        )
        .group_by(column)
        # Ties broken by value so the ordering is stable between requests.
        .order_by(visitors.desc(), column)
        .limit(limit)
    ).all()

    return [
        BreakdownRow(value=str(row.value), visitors=row.visitors, pageviews=row.pageviews)
        for row in rows
    ]


def live_visitors(
    db: Session,
    *,
    site_id: str,
    now: dt.datetime | None = None,
    window: dt.timedelta = LIVE_WINDOW,
) -> LiveVisitors:
    since = (now or dt.datetime.now(dt.UTC)) - window
    count = db.scalar(
        select(_visitors()).where(Event.site_id == site_id, Event.timestamp >= since)
    )

    return LiveVisitors(visitors=count or 0, window_minutes=int(window.total_seconds() // 60))
