"""Measuring how far people get along a path, and how many stop where.

A funnel here is measured **inside one visit**. That is not a simplification,
it is the schema: a visitor ID is a keyed hash of a salt that rotates at the
site's midnight, so the same person on Monday and Tuesday is two unrelated
identities by construction. "Read the pricing page on Monday and signed up on
Thursday" is a question this system cannot answer, and the salt rotation --
which is the entire privacy argument -- is the reason. Plausible and Umami both
answer it because both keep an identity that outlives the day.

It also reads raw events rather than the aggregates. The rollups hold one
dimension at a time and a funnel is a question about the order of several, so
there is nothing in them to answer it with. That bounds a funnel to whatever
BEACON_RAW_EVENT_RETENTION_DAYS keeps, which the report says out loud rather
than quietly returning a shorter answer.

The counting is one pass. The inner query reduces each visit to the first
moment it reached each step; the outer one counts the visits whose moments
arrive in order. Pulling a row per visit into Python instead would be fine for
a demo and hopeless for a site with a hundred thousand of them a day.
"""

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import ColumnElement, case, func, select
from sqlalchemy.orm import Session

from app.models import Event, Funnel, FunnelStep, Site, StepKind
from app.services.visits import PAGEVIEW

# Enough to describe a checkout, few enough that the query stays one pass and
# the chart stays readable.
MAX_STEPS = 8


@dataclass(frozen=True, slots=True)
class Step:
    """One rung, and how many visits were still on the path when they reached it."""

    position: int
    label: str
    visits: int

    def share_of(self, entered: int) -> float:
        """Of everyone who started, the portion still here."""
        return round(self.visits / entered * 100, 1) if entered else 0.0

    def dropped_from(self, previous: int) -> int:
        return max(previous - self.visits, 0)


def _matches(step: FunnelStep) -> ColumnElement[bool]:
    """The condition that says this event is this step.

    A page step is a page being read; a goal step is the goal firing. Custom
    events share the events table with pageviews, so a page step has to say
    that it means a pageview or "/checkout" would also match a goal somebody
    happened to call "/checkout".
    """
    if step.kind == StepKind.PAGE:
        return (Event.name == PAGEVIEW) & (Event.pathname == step.value)
    return Event.name == step.value


def measure(
    db: Session, *, funnel: Funnel, first_day: dt.date, last_day: dt.date
) -> list[Step]:
    """How many visits reached each step, in order, within the window.

    A visit counts at step N only if it reached every step before it, and
    reached them earlier. Someone who lands on the confirmation page without
    ever seeing the basket has not been through the funnel; counting them would
    make a funnel that widens, which is not a funnel.
    """
    steps = list(funnel.steps)
    if not steps:
        return []

    # First moment this visit satisfied each step, or null if it never did.
    reached = [
        func.min(case((_matches(step), Event.timestamp))).label(f"t{index}")
        for index, step in enumerate(steps)
    ]
    per_visit = (
        select(*reached)
        .where(
            Event.site_id == funnel.site.domain,
            Event.day >= first_day,
            Event.day <= last_day,
        )
        .group_by(Event.visitor_id, Event.day)
        .subquery()
    )

    # A visit is at step N if every moment up to N exists and never goes
    # backwards. Strictly increasing rather than merely present, so the order
    # the funnel describes is the order that has to have happened.
    counters = []
    for index in range(len(steps)):
        column = per_visit.c[f"t{index}"]
        condition: ColumnElement[bool] = column.is_not(None)
        if index:
            previous = per_visit.c[f"t{index - 1}"]
            condition = condition & (column >= previous) & previous.is_not(None)
        counters.append(func.sum(case((condition, 1), else_=0)).label(f"n{index}"))

    row = db.execute(select(*counters).select_from(per_visit)).one()

    return [
        Step(position=index, label=step.value, visits=int(row[index] or 0))
        for index, step in enumerate(steps)
    ]


class FunnelError(ValueError):
    """A definition that does not describe a path anyone could walk."""


GOAL_PREFIX = "goal:"


def parse_steps(raw: str) -> list[tuple[StepKind, str]]:
    """Read a funnel's steps from one-per-line text.

        /pricing
        /basket
        goal:purchase

    A line is a path unless it says otherwise, because most steps are pages and
    the common case should need no ceremony. Blank lines are ignored so a
    trailing newline is not a step.
    """
    steps: list[tuple[StepKind, str]] = []
    for line in raw.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue

        if cleaned.lower().startswith(GOAL_PREFIX):
            goal = cleaned[len(GOAL_PREFIX):].strip()
            if not goal:
                raise FunnelError("A goal step needs a name after 'goal:'.")
            steps.append((StepKind.GOAL, goal))
        else:
            steps.append((StepKind.PAGE, cleaned))

    if len(steps) < 2:
        raise FunnelError("A funnel needs at least two steps.")
    if len(steps) > MAX_STEPS:
        raise FunnelError(f"A funnel can have at most {MAX_STEPS} steps.")

    return steps


def create(db: Session, *, site: Site, name: str, raw_steps: str) -> Funnel:
    """Define a funnel for a site."""
    label = name.strip()[:64]
    if not label:
        raise FunnelError("Give the funnel a name.")

    steps = parse_steps(raw_steps)
    if db.scalar(
        select(Funnel).where(Funnel.site_id == site.id, Funnel.name == label)
    ) is not None:
        raise FunnelError(f"This site already has a funnel called {label}.")

    funnel = Funnel(
        site_id=site.id,
        name=label,
        steps=[
            FunnelStep(position=index, kind=kind, value=value[:1024])
            for index, (kind, value) in enumerate(steps)
        ],
    )
    db.add(funnel)
    db.commit()
    return funnel


def delete(db: Session, *, site: Site, funnel_id: int) -> None:
    """Remove a funnel, but only from the site it belongs to.

    Scoped by site as well as by id, so a funnel id from one dashboard cannot
    be posted at another.
    """
    funnel = db.scalar(
        select(Funnel).where(Funnel.id == funnel_id, Funnel.site_id == site.id)
    )
    if funnel is None:
        raise FunnelError("No such funnel.")

    db.delete(funnel)
    db.commit()


def for_site(db: Session, site_id: int) -> list[Funnel]:
    return list(
        db.scalars(select(Funnel).where(Funnel.site_id == site_id).order_by(Funnel.name))
    )
