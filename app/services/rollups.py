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
from app.services import visits
from app.services.stats import (
    BOUNDARY_EDGES,
    BREAKDOWN_COLUMNS,
    BREAKDOWN_FILTERS,
    pageview_count,
    visitor_count,
)

TOTAL = "total"
VALUE_LIMIT = 512

# How far back a routine refresh reaches. More than one day, because an event
# can arrive after midnight for the day that just ended.
RECENT_DAYS = 2

# Retention shorter than this would delete raw events the refresh above still
# needs, so a smaller setting is refused rather than honoured.
MINIMUM_RETENTION_DAYS = 7


def rebuild_day(db: Session, *, site_id: str, day: dt.date) -> int:
    """Recompute every DailyStat row for one site and one day."""
    # The event carries the site's local day, so this is a plain equality.
    scope = (Event.site_id == site_id, Event.day == day)

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
                # Only on this row. A visit bounced or it did not; attributing
                # one to each dimension value it touched would count it several
                # times over.
                bounces=visits.bounce_count(
                    db, site_id=site_id, first_day=day, last_day=day
                ),
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

    # The two dimensions that are not columns. Computed a day at a time here
    # and summed over ranges by the report layer, which is sound for the same
    # reason the rest of this table is: a visit cannot straddle midnight, so
    # every entrance and exit belongs to exactly one day.
    for prop, edge in BOUNDARY_EDGES.items():
        rows.extend(
            DailyStat(
                site_id=site_id,
                day=day,
                dimension=prop.value,
                value=value[:VALUE_LIMIT],
                visitors=boundary_visits,
                pageviews=boundary_pageviews,
            )
            for value, boundary_visits, boundary_pageviews in visits.boundary_pages(
                db, site_id=site_id, first_day=day, last_day=day, edge=edge
            )
        )

    db.add_all(rows)
    db.commit()
    return len(rows)


def rebuild_hours(db: Session, *, site_id: str, day: dt.date) -> int:
    """Recompute the hourly totals for one site and one day."""
    db.execute(
        delete(HourlyStat).where(HourlyStat.site_id == site_id, HourlyStat.day == day)
    )

    grouped = db.execute(
        select(Event.hour, visitor_count(), pageview_count())
        .where(Event.site_id == site_id, Event.day == day)
        .group_by(Event.hour)
    )

    rows = [
        HourlyStat(
            site_id=site_id, day=day, hour=hour, visitors=visitors, pageviews=pageviews
        )
        for hour, visitors, pageviews in grouped
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
    cutoff = dt.datetime.combine(
        last_day - dt.timedelta(days=retention_days), dt.time.min, tzinfo=dt.UTC
    )

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
