"""The aggregate queries behind the dashboard."""

import datetime as dt
from collections import defaultdict
from enum import StrEnum
from typing import Any

from sqlalchemy import Select, case, distinct, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.functions import Function

from app.models import Event
from app.schemas import BreakdownRow, LiveVisitors, StatsSummary, TimeseriesPoint
from app.services import visits
from app.services.timeranges import LABEL_FORMATS, Interval, TimeRange, bucket_labels

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
    EVENT = "event"
    MEDIUM = "medium"
    CAMPAIGN = "campaign"
    ENTRY_PAGE = "entry_page"
    EXIT_PAGE = "exit_page"


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
    BreakdownProperty.EVENT: Event.name,
    BreakdownProperty.MEDIUM: Event.medium,
    BreakdownProperty.CAMPAIGN: Event.campaign,
}

# Dimensions that are not a column at all: they are the first and last page of
# a visit, which only exists once the events are grouped into visits. Handled
# by app.services.visits, in both this module and the rollup builder.
BOUNDARY_EDGES = {
    BreakdownProperty.ENTRY_PAGE: visits.Edge.START,
    BreakdownProperty.EXIT_PAGE: visits.Edge.END,
}

# Dimensions that count only a subset of events. Applied by both the raw
# queries and the rollup builder, so the two cannot disagree about scope.
BREAKDOWN_FILTERS = {
    # Goals are about what people did besides reading a page.
    BreakdownProperty.EVENT: Event.name != visits.PAGEVIEW,
    # Most visits carry no campaign tag, and a "(none)" row dwarfing every real
    # campaign would make the panel useless.
    BreakdownProperty.MEDIUM: Event.medium.is_not(None),
    BreakdownProperty.CAMPAIGN: Event.campaign.is_not(None),
}


def visitor_count() -> Function[int]:
    """Unique visitors. The expensive one: a distinct count over the window."""
    return func.count(distinct(Event.visitor_id))


def revenue_sum() -> Function[int]:
    """Revenue in minor units. Null for the events that are not worth anything."""
    return func.coalesce(func.sum(Event.revenue_minor), 0)


def pageview_count() -> Function[int]:
    """Only pageviews count as pageviews.

    Custom events share this table, so counting rows would inflate every site's
    pageview figure the moment somebody started tracking sign-ups.
    """
    return func.coalesce(func.sum(case((Event.name == visits.PAGEVIEW, 1), else_=0)), 0)


def _scoped[*Columns](
    statement: Select[*Columns], site_id: str, time_range: TimeRange
) -> Select[*Columns]:
    """Narrow to one site and one span of that site's days.

    Compared on the stored local day, not on the timestamp: the range came from
    the site's own calendar, and a day there is not a day in UTC.

    Generic over the columns rather than `Select[Any]`, because SQLAlchemy 2.1
    made `Select` variadic: `Select[Any]` used to mean "a select of anything"
    and now means "a select of exactly one column of type Any". Every caller
    here passes three or four, so the old annotation stopped describing any of
    them -- and because it erased the shape, the rows came back as `Row[Any]`
    and unpacking them into four names became an error too. Carrying the
    columns through says the true thing: this function adds predicates and
    changes nothing about what is selected.
    """
    first, last = time_range.days
    return statement.where(Event.site_id == site_id, Event.day >= first, Event.day <= last)


def summary(db: Session, *, site_id: str, time_range: TimeRange) -> StatsSummary:
    row = db.execute(
        _scoped(
            select(
                visitor_count().label("visitors"),
                pageview_count().label("pageviews"),
                revenue_sum().label("revenue"),
            ),
            site_id,
            time_range,
        )
    ).one()

    first, last = time_range.days

    return StatsSummary.of(
        visitors=row.visitors,
        pageviews=row.pageviews,
        bounces=visits.bounce_count(db, site_id=site_id, first_day=first, last_day=last),
        revenue_minor=row.revenue,
    )


def timeseries(db: Session, *, site_id: str, time_range: TimeRange) -> list[TimeseriesPoint]:
    """The series, grouped on the buckets the events already carry.

    Hours for a single day, otherwise days folded into whatever the period asks
    for. No date truncation in SQL, so nothing here differs between databases.
    """
    fmt = LABEL_FORMATS[time_range.interval]

    if time_range.interval is Interval.HOUR:
        # Named apart from the daily rows below rather than reusing one name:
        # the two branches select a different number of columns, so they are
        # genuinely different types, and now that _scoped carries the columns
        # through, mypy can say so. The single name was only ever tenable while
        # the annotation erased the shape.
        by_hour = db.execute(
            _scoped(
                select(
                    Event.day,
                    Event.hour,
                    visitor_count().label("visitors"),
                    pageview_count().label("pageviews"),
                ),
                site_id,
                time_range,
            ).group_by(Event.day, Event.hour)
        ).all()
        counted = {
            f"{row.day.isoformat()}T{row.hour:02d}:00:00": (row.visitors, row.pageviews)
            for row in by_hour
        }
    else:
        by_day = db.execute(
            _scoped(
                select(
                    Event.day,
                    visitor_count().label("visitors"),
                    pageview_count().label("pageviews"),
                ),
                site_id,
                time_range,
            ).group_by(Event.day)
        ).all()

        folded: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for row in by_day:
            bucket = folded[row.day.strftime(fmt)]
            bucket[0] += row.visitors
            bucket[1] += row.pageviews
        counted = {label: (values[0], values[1]) for label, values in folded.items()}

    # Zero-fill: a chart with holes in it reads as broken.
    return [
        TimeseriesPoint(
            bucket=label,
            visitors=counted.get(label, (0, 0))[0],
            pageviews=counted.get(label, (0, 0))[1],
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
    edge = BOUNDARY_EDGES.get(prop)
    if edge is not None:
        # Not a column: the first and last page of a visit only exist once the
        # events have been grouped into visits.
        first, last = time_range.days
        # No revenue on these two. They are derived from the pageviews of a
        # visit, and a purchase is a custom event -- so attributing money to an
        # entry page would mean a different grouping, not a wider select. It
        # reports zero rather than a number arrived at by accident.
        return [
            BreakdownRow(value=value, visitors=visit_count, pageviews=pageviews, revenue_minor=0)
            for value, visit_count, pageviews in visits.boundary_pages(
                db,
                site_id=site_id,
                first_day=first,
                last_day=last,
                edge=edge,
                limit=limit,
            )
        ]

    column = BREAKDOWN_COLUMNS[prop]
    visitors = visitor_count()

    # Spelled out because the first column cannot be inferred: BREAKDOWN_COLUMNS
    # holds plain columns alongside a coalesce() over one, so the whitelist's
    # value type is the join of those and the label over it lands as Any. The
    # three counts after it are not Any, and saying so keeps the row unpacking
    # below checked rather than waved through.
    statement: Select[Any, int, int, int] = (
        _scoped(
            select(
                column.label("value"),
                visitors.label("visitors"),
                pageview_count().label("pageviews"),
                revenue_sum().label("revenue"),
            ),
            site_id,
            time_range,
        )
        .group_by(column)
        # Ties broken by value so the ordering is stable between requests.
        .order_by(visitors.desc(), column)
        .limit(limit)
    )

    narrowing = BREAKDOWN_FILTERS.get(prop)
    if narrowing is not None:
        statement = statement.where(narrowing)

    rows = db.execute(statement).all()

    return [
        BreakdownRow(
            value=str(row.value),
            visitors=row.visitors,
            pageviews=row.pageviews,
            revenue_minor=row.revenue,
        )
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
        select(visitor_count()).where(Event.site_id == site_id, Event.timestamp >= since)
    )

    return LiveVisitors(visitors=count or 0, window_minutes=int(window.total_seconds() // 60))
