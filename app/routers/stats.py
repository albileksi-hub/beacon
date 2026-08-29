from typing import Annotated

from fastapi import APIRouter, Query

from app.dependencies import DbSession, ReadableSite, Window
from app.schemas import BreakdownRow, LiveVisitors, StatsSummary, TimeseriesPoint
from app.services import reports
from app.services.stats import DEFAULT_BREAKDOWN_LIMIT, BreakdownProperty

# Every route resolves {site_id} through ReadableSite, so a caller sees only
# their own sites and any their owner has published.
router = APIRouter(prefix="/api/stats/{site_id}", tags=["stats"])

BreakdownLimit = Annotated[int, Query(ge=1, le=100)]


@router.get("/summary")
def read_summary(
    site: ReadableSite,
    db: DbSession,
    window: Window,
) -> StatsSummary:
    """Headline totals for the window: a named period, or `from` and `to`."""
    return reports.summary(db, site_id=site.domain, time_range=window)


@router.get("/timeseries")
def read_timeseries(
    site: ReadableSite,
    db: DbSession,
    window: Window,
) -> list[TimeseriesPoint]:
    """One point per bucket, including buckets with no traffic.

    Bucket size follows from the period: hours for today, days for a month,
    months for a year.
    """
    return reports.timeseries(db, site_id=site.domain, time_range=window)


@router.get("/breakdown/{prop}")
def read_breakdown(
    site: ReadableSite,
    prop: BreakdownProperty,
    db: DbSession,
    window: Window,
    limit: BreakdownLimit = DEFAULT_BREAKDOWN_LIMIT,
) -> list[BreakdownRow]:
    """Top values of one dimension: pages, sources, countries, devices."""
    return reports.breakdown(
        db,
        site_id=site.domain,
        time_range=window,
        prop=prop,
        limit=limit,
    )


@router.get("/live")
def read_live(site: ReadableSite, db: DbSession) -> LiveVisitors:
    """Visitors seen in the last few minutes."""
    return reports.live_visitors(db, site_id=site.domain)
