from typing import Annotated

from fastapi import APIRouter, Query

from app.dependencies import DbSession, OwnedSite
from app.schemas import BreakdownRow, LiveVisitors, StatsSummary, TimeseriesPoint
from app.services import stats
from app.services.stats import DEFAULT_BREAKDOWN_LIMIT, BreakdownProperty
from app.services.timeranges import Period, resolve

# Every route here resolves {site_id} through OwnedSite, so a signed-in user
# can only ever read their own numbers.
router = APIRouter(prefix="/api/stats/{site_id}", tags=["stats"])

BreakdownLimit = Annotated[int, Query(ge=1, le=100)]


@router.get("/summary")
def read_summary(
    site: OwnedSite,
    db: DbSession,
    period: Period = Period.LAST_30_DAYS,
) -> StatsSummary:
    """Headline totals for the period."""
    return stats.summary(db, site_id=site.domain, time_range=resolve(period))


@router.get("/timeseries")
def read_timeseries(
    site: OwnedSite,
    db: DbSession,
    period: Period = Period.LAST_30_DAYS,
) -> list[TimeseriesPoint]:
    """One point per bucket, including buckets with no traffic.

    Bucket size follows from the period: hours for today, days for a month,
    months for a year.
    """
    return stats.timeseries(db, site_id=site.domain, time_range=resolve(period))


@router.get("/breakdown/{prop}")
def read_breakdown(
    site: OwnedSite,
    prop: BreakdownProperty,
    db: DbSession,
    period: Period = Period.LAST_30_DAYS,
    limit: BreakdownLimit = DEFAULT_BREAKDOWN_LIMIT,
) -> list[BreakdownRow]:
    """Top values of one dimension: pages, sources, countries, devices."""
    return stats.breakdown(
        db, site_id=site.domain, time_range=resolve(period), prop=prop, limit=limit
    )


@router.get("/live")
def read_live(site: OwnedSite, db: DbSession) -> LiveVisitors:
    """Visitors seen in the last few minutes."""
    return stats.live_visitors(db, site_id=site.domain)
