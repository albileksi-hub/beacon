"""Dashboard queries served from the pre-aggregated tables.

These return the same answers as app.services.stats, which reads raw events.
stats is the definition; this is the fast path. tests/test_reports.py asserts
the two agree across every period and dimension, so the optimisation cannot
drift away from the truth unnoticed.
"""

import datetime as dt
from collections import defaultdict

from sqlalchemy import func, select
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


def _day_span(time_range: TimeRange) -> tuple[dt.date, dt.date]:
    return time_range.start.date(), time_range.end.date()


def summary(db: Session, *, site_id: str, time_range: TimeRange) -> StatsSummary:
    first, last = _day_span(time_range)

    visitors, pageviews = db.execute(
        select(
            func.coalesce(func.sum(DailyStat.visitors), 0),
            func.coalesce(func.sum(DailyStat.pageviews), 0),
        ).where(
            DailyStat.site_id == site_id,
            DailyStat.dimension == TOTAL,
            DailyStat.day >= first,
            DailyStat.day <= last,
        )
    ).one()

    return StatsSummary.of(visitors=visitors, pageviews=pageviews)


def breakdown(
    db: Session,
    *,
    site_id: str,
    time_range: TimeRange,
    prop: BreakdownProperty,
    limit: int = DEFAULT_BREAKDOWN_LIMIT,
) -> list[BreakdownRow]:
    first, last = _day_span(time_range)
    visitors = func.sum(DailyStat.visitors)

    rows = db.execute(
        select(
            DailyStat.value,
            visitors.label("visitors"),
            func.sum(DailyStat.pageviews).label("pageviews"),
        )
        .where(
            DailyStat.site_id == site_id,
            DailyStat.dimension == prop.value,
            DailyStat.day >= first,
            DailyStat.day <= last,
        )
        .group_by(DailyStat.value)
        # Ties broken by value so the ordering is stable between requests.
        .order_by(visitors.desc(), DailyStat.value)
        .limit(limit)
    ).all()

    return [
        BreakdownRow(value=row.value, visitors=row.visitors, pageviews=row.pageviews)
        for row in rows
    ]


def _hourly_totals(db: Session, site_id: str, time_range: TimeRange) -> dict[str, list[int]]:
    """Hours of the site's own day, keyed by the label the chart asks for."""
    first, last = _day_span(time_range)
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
    first, last = _day_span(time_range)
    fmt = LABEL_FORMATS[time_range.interval]

    rows = db.execute(
        select(DailyStat.day, DailyStat.visitors, DailyStat.pageviews).where(
            DailyStat.site_id == site_id,
            DailyStat.dimension == TOTAL,
            DailyStat.day >= first,
            DailyStat.day <= last,
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
