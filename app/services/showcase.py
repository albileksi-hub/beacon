"""A playable version of the one idea the product rests on.

The landing page can assert that a rotating salt makes an identifier expire,
and nobody has to believe it. This shows three days at once, so the three
different numbers and the one unchanging cookie beside them are visible
without anybody clicking anything, and lets a reader swap the visitor and the
site to watch all of them move together.

Two things keep it from reading as decoration.

It runs on the reader's own request when they ask it to. Their browser, system
and device are their real ones, which no mock-up can produce; the address that
goes into the hash is deliberately never shown, because displaying the one
value the product exists not to keep would undercut the whole argument.

It calls the real visitor_id rather than a reimplementation, and the page
prints the command that reproduces the number. A demonstration that invented
its own hash would be a drawing of the argument rather than the argument, and
would start lying the first time the two drifted apart.

What it does not use is the real salt table. Those salts are random and
deleted after two days, which is exactly what makes them unusable here: the
page has to show the same thing to every reader, including for a day whose
salt would already be gone. These come from a fixed, clearly-named seed.
"""

import datetime as dt
import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.schemas import StatsSummary, TimeseriesPoint
from app.services import accounts, reports
from app.services.timeranges import Period, resolve
from app.services.visitors import keyed_hash

# Not a secret, and not used for anything real. Named so it cannot be mistaken
# for one of the rotating salts in daily_salts.
DEMO_SEED = b"beacon-landing-page-demonstration-only"

# Stand-ins for an address and a User-Agent. A reader can also pick themselves,
# in which case these are replaced by the real ones off the request.
PEOPLE: dict[str, tuple[str, str]] = {
    "ada": ("203.0.113.7", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15"),
    "grace": ("198.51.100.42", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"),
}

LIVE = "you"
SITES = ("blue-mug.example", "red-bowl.example")

# Far enough to cross the two-day salt retention, which is the point.
DAYS = (0, 1, 2)
DAY_LABELS = {0: "Today", 1: "Tomorrow", 2: "In two days"}

# Two days of retention: beyond that the salt is gone and nothing can
# recompute the identifier that used it.
SALT_RETENTION_DAYS = 2


@dataclass(frozen=True, slots=True)
class Row:
    """One day of the same person on the same site."""

    offset: int
    label: str
    day: dt.date
    beacon_id: str
    salt_still_exists: bool


@dataclass(frozen=True, slots=True)
class Showcase:
    """Everything the panel renders."""

    person: str
    person_label: str
    site: str
    rows: list[Row]
    cookie_id: str
    # The real inputs, so the number has visible provenance. The address is
    # named rather than shown.
    user_agent: str
    is_live: bool


def demo_salt(day: dt.date) -> bytes:
    """A reproducible stand-in for that day's random salt."""
    return hashlib.blake2b(day.isoformat().encode("utf-8"), key=DEMO_SEED, digest_size=32).digest()


def cookie_id(person: str) -> str:
    """What a cookie holds: one value, assigned once, derived from nothing.

    No site and no day go into it, which is the whole of the difference. It
    cannot expire, because nothing about it is computed from anything that
    does.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_OID, f"beacon-demo-cookie:{person}"))


def build(
    *,
    person: str,
    site: str,
    live_ip: str,
    live_user_agent: str,
    live_label: str,
    today: dt.date | None = None,
) -> Showcase:
    """The three days for one person on one site, and the cookie beside them."""
    if person not in PEOPLE and person != LIVE:
        person = LIVE
    if site not in SITES:
        site = SITES[0]

    if person == LIVE:
        ip, user_agent, label = live_ip, live_user_agent, live_label
    else:
        ip, user_agent = PEOPLE[person]
        label = person.title()

    start = today or dt.datetime.now(dt.UTC).date()
    rows = []
    for offset in DAYS:
        day = start + dt.timedelta(days=offset)
        rows.append(
            Row(
                offset=offset,
                label=DAY_LABELS[offset],
                day=day,
                # The same construction the collector uses, in the same order.
                beacon_id=keyed_hash(site, ip, user_agent, salt=demo_salt(day)),
                salt_still_exists=offset < SALT_RETENTION_DAYS,
            )
        )

    return Showcase(
        person=person,
        person_label=label,
        site=site,
        rows=rows,
        cookie_id=cookie_id(person),
        user_agent=user_agent,
        is_live=person == LIVE,
    )


# The site whose numbers the front page puts on display. Seeded by seed.py and
# published, so it is readable without an account.
PREVIEW_SITE = "demo.example"

PREVIEW_PERIODS = (Period.LAST_7_DAYS, Period.LAST_30_DAYS, Period.LAST_12_MONTHS)


@dataclass(frozen=True, slots=True)
class Preview:
    """Enough of a real dashboard to put in a hero."""

    site: str
    period: Period
    summary: StatsSummary
    series: list[TimeseriesPoint]
    live: int
    interval: str


def preview(db: Session, *, period: Period) -> Preview | None:
    """The demo site's real numbers, or None when there are none to show.

    A fresh clone has no traffic until seed.py runs, and a hero built around
    an empty chart is worse than no card at all. The caller omits it in that
    case and the page opens on the sky alone.
    """
    if not accounts.site_is_registered(db, PREVIEW_SITE):
        return None

    window = resolve(period)
    summary = reports.summary(db, site_id=PREVIEW_SITE, time_range=window)
    if summary.pageviews == 0:
        return None

    return Preview(
        site=PREVIEW_SITE,
        period=period,
        summary=summary,
        series=reports.timeseries(db, site_id=PREVIEW_SITE, time_range=window),
        live=reports.live_visitors(db, site_id=PREVIEW_SITE).visitors,
        interval=window.interval.value,
    )
