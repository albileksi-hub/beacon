"""Building the pre-aggregated tables the dashboard reads.

Rebuilds rather than incrementally updates. Rebuilding a day is idempotent:
running the job twice, or resuming after it died halfway through, converges on
the same numbers. An incremental counter that double-counts once stays wrong
forever, and nothing in the raw events tells you it happened.
"""

import datetime as dt
import logging
from typing import Any, cast

from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.models import DailyStat, Event, HourlyStat, Site
from app.services import visits
from app.services.stats import (
    BOUNDARY_EDGES,
    BREAKDOWN_COLUMNS,
    BREAKDOWN_FILTERS,
    pageview_count,
    visitor_count,
)

logger = logging.getLogger(__name__)

TOTAL = "total"
VALUE_LIMIT = 512

# How far back a routine refresh reaches. More than one day, because an event
# can arrive after midnight for the day that just ended.
RECENT_DAYS = 2

# Retention shorter than this would delete raw events the refresh above still
# needs, so a smaller setting is refused rather than honoured.
MINIMUM_RETENTION_DAYS = 7


def purged_through(db: Session, *, site_id: str) -> dt.date | None:
    """The last day retention has taken raw events from, if it ever has.

    Recorded by purge_expired_events rather than inferred from what survives.
    The two are not the same question: a site whose events were deleted for
    some other reason -- spam, a bad deploy, a test run -- has no raw events
    either, and clearing its stale aggregates is the correct thing to do. Only
    retention makes the aggregates irreplaceable, so only retention says so.
    """
    return db.scalar(
        select(Site.raw_events_purged_through).where(Site.domain == site_id)
    )


def can_rebuild(day: dt.date, purged: dt.date | None) -> bool:
    """Whether a day can still be reconstructed from raw events.

    A rebuild deletes the day's aggregates before recomputing them, so it is
    only safe while the rows behind them are still there. Once retention has
    taken that day, recomputing produces nothing and the delete is the entire
    operation -- against the only surviving copy, which is the whole premise
    of retention.
    """
    return purged is None or day > purged


def rebuild_day(db: Session, *, site_id: str, day: dt.date) -> int:
    """Recompute every DailyStat row for one site and one day.

    Refuses a day whose raw events have been purged, leaving what is stored
    untouched. Without that, `manage.py rollup --days 400` on an instance with
    retention enabled -- both of which the documentation recommends -- deletes
    every aggregate it cannot rebuild, which is most of the site's history.
    """
    if not can_rebuild(day, purged_through(db, site_id=site_id)):
        return 0

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
    """Recompute the hourly totals for one site and one day.

    Refuses a purged day for the same reason rebuild_day does.
    """
    if not can_rebuild(day, purged_through(db, site_id=site_id)):
        return 0

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
    skipped = 0

    for site_id in db.scalars(select(Event.site_id).distinct()):
        # Once per site rather than once per day: the answer cannot change
        # while this loop runs, and a long backfill would otherwise ask it
        # hundreds of times.
        watermark = purged_through(db, site_id=site_id)

        for offset in range(days_back):
            day = last_day - dt.timedelta(days=offset)
            if not can_rebuild(day, watermark):
                skipped += 1
                continue

            rebuild_day(db, site_id=site_id, day=day)
            rebuild_hours(db, site_id=site_id, day=day)
            rebuilt += 1

    if skipped:
        # Loud, because the alternative reading is that the backfill worked.
        logger.warning(
            "left %s site-days alone: their raw events are past retention, so the "
            "stored aggregates are the only copy and rebuilding would delete them",
            f"{skipped:,}",
        )

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

    # Record how far back the raw events are now gone, so a later rebuild
    # refuses those days instead of deleting the aggregates that replaced
    # them. Marked for every site this ran against rather than only the ones
    # that lost rows: retention has still passed over the others, and a day
    # with nothing to delete is equally unrebuildable.
    #
    # cutoff is midnight UTC of the first retained day, so any local day at or
    # before its date may have lost events -- a local day after it begins no
    # earlier than cutoff in every zone, from UTC-12 to UTC+14.
    db.execute(
        update(Site)
        .where(Site.domain.in_(aggregated))
        .values(raw_events_purged_through=cutoff.date())
    )

    db.commit()
    return deleted
