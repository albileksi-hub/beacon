"""Dashboard queries served from the pre-aggregated tables.

These return the same answers as app.services.stats, which reads raw events.
stats is the definition; this is the fast path. tests/test_reports.py asserts
the two agree across every period and dimension, so the optimisation cannot
drift away from the truth unnoticed.
"""

from collections import defaultdict
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models import DailyStat, HourlyStat
from app.schemas import (
    BreakdownRow,
    Change,
    StatsSummary,
    SummaryWithComparison,
    TimeseriesPoint,
)
from app.services import stats
from app.services.rollups import TOTAL
from app.services.stats import DEFAULT_BREAKDOWN_LIMIT, BreakdownProperty
from app.services.timeranges import (
    LABEL_FORMATS,
    Interval,
    TimeRange,
    bucket_labels,
    preceding,
)

# The live counter keeps reading raw events. It needs the last five minutes,
# which no rollup grain can answer, and the (site, timestamp) index makes it
# cheap regardless of table size.
live_visitors = stats.live_visitors


def _scoped(
    statement: Select[Any], site_id: str, time_range: TimeRange, dimension: str
) -> Select[Any]:
    """Narrow to one site, one dimension, and one span of that site's days.

    The sibling of stats._scoped, which does the same for raw events. Three
    queries here spelled the same four predicates out by hand; a query that
    forgot one of the day bounds would read another period's numbers and look
    entirely plausible doing it.
    """
    first, last = time_range.days
    return statement.where(
        DailyStat.site_id == site_id,
        DailyStat.dimension == dimension,
        DailyStat.day >= first,
        DailyStat.day <= last,
    )


def summary(db: Session, *, site_id: str, time_range: TimeRange) -> StatsSummary:
    visitors, pageviews, bounces, revenue = db.execute(
        _scoped(
            select(
                func.coalesce(func.sum(DailyStat.visitors), 0),
                func.coalesce(func.sum(DailyStat.pageviews), 0),
                func.coalesce(func.sum(DailyStat.bounces), 0),
                func.coalesce(func.sum(DailyStat.revenue_minor), 0),
            ),
            site_id,
            time_range,
            TOTAL,
        )
    ).one()

    # Summing bounces and dividing at the end, rather than averaging each day's
    # rate: a day with four visits would otherwise weigh as heavily as a day
    # with forty thousand.
    return StatsSummary.of(
        visitors=visitors, pageviews=pageviews, bounces=bounces, revenue_minor=revenue
    )


def breakdown(
    db: Session,
    *,
    site_id: str,
    time_range: TimeRange,
    prop: BreakdownProperty,
    limit: int = DEFAULT_BREAKDOWN_LIMIT,
) -> list[BreakdownRow]:
    visitors = func.sum(DailyStat.visitors)

    rows = db.execute(
        _scoped(
            select(
                DailyStat.value,
                visitors.label("visitors"),
                func.sum(DailyStat.pageviews).label("pageviews"),
                func.sum(DailyStat.revenue_minor).label("revenue"),
            ),
            site_id,
            time_range,
            prop.value,
        )
        .group_by(DailyStat.value)
        # Ties broken by value so the ordering is stable between requests.
        .order_by(visitors.desc(), DailyStat.value)
        .limit(limit)
    ).all()

    return [
        BreakdownRow(
            value=row.value,
            visitors=row.visitors,
            pageviews=row.pageviews,
            revenue_minor=row.revenue,
        )
        for row in rows
    ]


def _hourly_totals(db: Session, site_id: str, time_range: TimeRange) -> dict[str, list[int]]:
    """Hours of the site's own day, keyed by the label the chart asks for."""
    first, last = time_range.days
    rows = db.execute(
        select(
            HourlyStat.day, HourlyStat.hour, HourlyStat.visitors, HourlyStat.pageviews
        ).where(
            HourlyStat.site_id == site_id,
            HourlyStat.day >= first,
            HourlyStat.day <= last,
        )
    )
    return {
        f"{day.isoformat()}T{hour:02d}:00:00": [visitors, pageviews]
        for day, hour, visitors, pageviews in rows
    }


def _daily_totals(db: Session, site_id: str, time_range: TimeRange) -> dict[str, list[int]]:
    """Daily rows, folded into whichever bucket the range asked for.

    Summing days into a month is only sound because the visitor salt rotates
    at midnight -- see the note on models.DailyStat.
    """
    fmt = LABEL_FORMATS[time_range.interval]

    rows = db.execute(
        _scoped(
            select(DailyStat.day, DailyStat.visitors, DailyStat.pageviews),
            site_id,
            time_range,
            TOTAL,
        )
    )

    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for day, visitors, pageviews in rows:
        bucket = totals[day.strftime(fmt)]
        bucket[0] += visitors
        bucket[1] += pageviews

    return totals


def timeseries(db: Session, *, site_id: str, time_range: TimeRange) -> list[TimeseriesPoint]:
    if time_range.interval is Interval.HOUR:
        counted = _hourly_totals(db, site_id, time_range)
    else:
        counted = _daily_totals(db, site_id, time_range)

    # Zero-fill: a chart with holes in it reads as broken.
    return [
        TimeseriesPoint(
            bucket=label,
            visitors=counted.get(label, (0, 0))[0],
            pageviews=counted.get(label, (0, 0))[1],
        )
        for label in bucket_labels(time_range)
    ]


def summary_with_comparison(
    db: Session, *, site_id: str, time_range: TimeRange
) -> SummaryWithComparison:
    """This period's totals, next to the equivalent window before it."""
    current = summary(db, site_id=site_id, time_range=time_range)
    earlier = summary(db, site_id=site_id, time_range=preceding(time_range))

    return SummaryWithComparison(
        summary=current,
        visitors=Change.between(current.visitors, earlier.visitors),
        pageviews=Change.between(current.pageviews, earlier.pageviews),
    )
