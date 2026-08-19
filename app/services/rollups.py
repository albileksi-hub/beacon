"""Building the pre-aggregated tables the dashboard reads.

Rebuilds rather than incrementally updates. Rebuilding a day is idempotent:
running the job twice, or resuming after it died halfway through, converges on
the same numbers. An incremental counter that double-counts once stays wrong
forever, and nothing in the raw events tells you it happened.
"""

import datetime as dt
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.models import DailyStat, Event, HourlyStat
from app.services.stats import (
    BREAKDOWN_COLUMNS,
    BREAKDOWN_FILTERS,
    bucket_column,
    pageview_count,
    visitor_count,
)
from app.services.timeranges import Interval

TOTAL = "total"
VALUE_LIMIT = 512

# How far back a routine refresh reaches. More than one day, because an event
# can arrive after midnight for the day that just ended.
RECENT_DAYS = 2

# Retention shorter than this would delete raw events the refresh above still
# needs, so a smaller setting is refused rather than honoured.
MINIMUM_RETENTION_DAYS = 7


def _day_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime.combine(day, dt.time.min, tzinfo=dt.UTC)
    return start, start + dt.timedelta(days=1)


def rebuild_day(db: Session, *, site_id: str, day: dt.date) -> int:
    """Recompute every DailyStat row for one site and one day."""
    start, end = _day_bounds(day)
    scope = (Event.site_id == site_id, Event.timestamp >= start, Event.timestamp < end)

    db.execute(delete(DailyStat).where(DailyStat.site_id == site_id, DailyStat.day == day))

    visitors, pageviews = db.execute(
        select(visitor_count(), pageview_count()).where(*scope)
    ).one()

    rows: list[DailyStat] = []
    if pageviews:
        rows.append(
            DailyStat(
                site_id=site_id,
                day=day,
                dimension=TOTAL,
                value="",
                visitors=visitors,
                pageviews=pageviews,
            )
        )

    for prop, column in BREAKDOWN_COLUMNS.items():
        statement = (
            select(column, visitor_count(), pageview_count()).where(*scope).group_by(column)
        )

        # The same narrowing the raw queries apply, so the aggregates cover
        # exactly the rows the definition covers.
        narrowing = BREAKDOWN_FILTERS.get(prop)
        if narrowing is not None:
            statement = statement.where(narrowing)

        grouped = db.execute(statement)
        rows.extend(
            DailyStat(
                site_id=site_id,
                day=day,
                dimension=prop.value,
                value=str(value)[:VALUE_LIMIT],
                visitors=group_visitors,
                pageviews=group_pageviews,
            )
            for value, group_visitors, group_pageviews in grouped
        )

    db.add_all(rows)
    db.commit()
    return len(rows)


def rebuild_hours(db: Session, *, site_id: str, day: dt.date) -> int:
    """Recompute the hourly totals for one site and one day."""
    start, end = _day_bounds(day)

    db.execute(
        delete(HourlyStat).where(
            HourlyStat.site_id == site_id, HourlyStat.hour >= start, HourlyStat.hour < end
        )
    )

    bucket = bucket_column(db, Interval.HOUR)
    grouped = db.execute(
        select(bucket, visitor_count(), pageview_count())
        .where(Event.site_id == site_id, Event.timestamp >= start, Event.timestamp < end)
        .group_by(bucket)
    )

    rows = [
        HourlyStat(
            site_id=site_id,
            hour=dt.datetime.fromisoformat(label).replace(tzinfo=dt.UTC),
            visitors=visitors,
            pageviews=pageviews,
        )
        for label, visitors, pageviews in grouped
    ]

    db.add_all(rows)
    db.commit()
    return len(rows)


def refresh(db: Session, *, days_back: int = RECENT_DAYS, today: dt.date | None = None) -> int:
    """Rebuild recent days for every site that has traffic. Returns days rebuilt."""
    last_day = today or dt.datetime.now(dt.UTC).date()
    rebuilt = 0

    for site_id in db.scalars(select(Event.site_id).distinct()):
        for offset in range(days_back):
            day = last_day - dt.timedelta(days=offset)
            rebuild_day(db, site_id=site_id, day=day)
            rebuild_hours(db, site_id=site_id, day=day)
            rebuilt += 1

    return rebuilt


def purge_expired_events(
    db: Session, *, retention_days: int, today: dt.date | None = None
) -> int:
    """Delete raw events the aggregates already account for.

    Raw events are needed for exactly two things: the live counter, which looks
    at the last five minutes, and rebuilding a day's aggregates. Once a day is
    rolled up and out of the refresh window, its raw rows are dead weight -- and
    on a busy site they are almost all of the database.

    Two guards, because this is not reversible:

    * a retention shorter than MINIMUM_RETENTION_DAYS is refused, so the setting
      cannot be turned into a way to delete data the refresh still needs;
    * only sites that actually have aggregates are touched. A site whose rollups
      never ran would otherwise lose its history to a job that could no longer
      rebuild it.
    """
    if retention_days < MINIMUM_RETENTION_DAYS:
        return 0

    last_day = today or dt.datetime.now(dt.UTC).date()
    cutoff, _ = _day_bounds(last_day - dt.timedelta(days=retention_days))

    aggregated = select(DailyStat.site_id).distinct()
    result = cast(
        CursorResult[Any],
        db.execute(
            delete(Event).where(Event.timestamp < cutoff, Event.site_id.in_(aggregated))
        ),
    )
    deleted = result.rowcount
    db.commit()
    return deleted
