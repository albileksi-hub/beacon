"""Where visits began, where they ended, and whether anything happened between.

A "visit" here is a visitor-day. That is less a modelling choice than a
consequence of the privacy design: the salt behind a visitor ID rotates at the
site's own midnight, so an identity cannot outlive the day it was minted in.
There is no session to hang these metrics on and there cannot be one without
giving up the rotation, which is the whole argument.

That turns out to be convenient rather than limiting. The visitor-day is
already the grain the aggregates are built on, so entrances, exits and bounces
are additive across days for exactly the same reason unique visitors are, and
none of them need a session table, a thirty-minute inactivity timer, or a
sketch. A visit cannot straddle midnight, so a day's numbers are complete on
their own.

One metric is deliberately absent. Plausible and Matomo report a visit
duration because a session there ends after half an hour of inactivity; here
the unit runs to midnight, so the same arithmetic would report the gap between
somebody's breakfast reading and their evening reading as one seven-hour visit.
A number that is wrong in a way nobody can see is worse than a number that is
missing, so it is missing. See the README.
"""

import datetime as dt
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.selectable import Subquery

from app.models import Event

# The event name every tracking script sends on a page load. Custom events
# share the table, and neither starts a visit nor ends one -- counting them
# would make a sign-up look like a landing page.
PAGEVIEW = "pageview"


class Edge(StrEnum):
    """Which end of a visit a page sat at.

    The values are the column labels _visit_shape gives the two rankings.
    """

    START = "from_start"
    END = "from_end"


def _visit_shape(site_id: str, first_day: dt.date, last_day: dt.date) -> Subquery:
    """One row per pageview, tagged with its place in the visit it belongs to.

    Window functions rather than a self-join or a correlated subquery: both
    dialects have had them for years, and this way entrances, exits and bounces
    all fall out of a single pass over the index the rollup builder already
    uses.

    The ranking breaks ties on the primary key as well as the timestamp.
    Batched writes can land two of a visitor's pageviews in the same instant,
    and without a total order the first page of that visit would be whichever
    row the database happened to return -- so the rollups and the raw queries
    would disagree at random, which is the one thing tests/test_reports.py
    exists to prevent.
    """
    visit = (Event.visitor_id, Event.day)

    return (
        select(
            Event.pathname.label("pathname"),
            func.row_number()
            .over(partition_by=visit, order_by=(Event.timestamp.asc(), Event.id.asc()))
            .label(Edge.START.value),
            func.row_number()
            .over(partition_by=visit, order_by=(Event.timestamp.desc(), Event.id.desc()))
            .label(Edge.END.value),
            func.count().over(partition_by=visit).label("views"),
        )
        .where(
            Event.site_id == site_id,
            Event.day >= first_day,
            Event.day <= last_day,
            Event.name == PAGEVIEW,
        )
        .subquery()
    )


def boundary_pages(
    db: Session,
    *,
    site_id: str,
    first_day: dt.date,
    last_day: dt.date,
    edge: Edge,
    limit: int | None = None,
) -> list[tuple[str, int, int]]:
    """The pages visits started or finished on: (path, visits, pageviews).

    ``visits`` is how many visits touched that boundary. ``pageviews`` is how
    many pages those visits read in total, which is what makes the row worth
    reading: a landing page that sends people onward is visibly different from
    one they arrive at and leave, and the two are otherwise indistinguishable.

    Ordered by visits and then by path, so ties do not reshuffle between
    requests.
    """
    shape = _visit_shape(site_id, first_day, last_day)
    visits = func.count().label("visits")

    statement = (
        select(shape.c.pathname, visits, func.sum(shape.c.views).label("pageviews"))
        .where(shape.c[edge.value] == 1)
        .group_by(shape.c.pathname)
        .order_by(visits.desc(), shape.c.pathname)
    )
    if limit is not None:
        statement = statement.limit(limit)

    return [(str(row[0]), int(row[1]), int(row[2])) for row in db.execute(statement)]


def bounce_count(db: Session, *, site_id: str, first_day: dt.date, last_day: dt.date) -> int:
    """Visits that read exactly one page and went no further.

    A visit of one pageview is one row in the shape above, so counting those
    rows counts each such visit exactly once -- no need to filter on the
    ranking as well.
    """
    shape = _visit_shape(site_id, first_day, last_day)
    counted = db.scalar(select(func.count()).select_from(shape).where(shape.c.views == 1))

    return int(counted or 0)
