"""Generate plausible demo traffic so the dashboard has something to show.

    python seed.py --days 30 --site demo.example

Writes straight to the database rather than through the collector, because the
point is to backfill historical timestamps, which the API deliberately will not
let a caller do.
"""

import argparse
import datetime as dt
import os
import random
import sys
from itertools import batched
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import delete, insert, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import SessionLocal, upgrade_database  # noqa: E402
from app.models import DailyStat, Event, HourlyStat, Site, User  # noqa: E402
from app.services import accounts, screens, zones  # noqa: E402

PAGES = [
    ("/", 34),
    ("/products/blue-mug", 22),
    ("/products/speckled-bowl", 14),
    ("/about", 11),
    ("/journal/how-we-glaze", 9),
    ("/shipping", 6),
    ("/contact", 4),
]
SOURCES = [
    ("Direct", 30),
    ("Google", 27),
    ("X (Twitter)", 12),
    ("Reddit", 10),
    ("Hacker News", 8),
    ("Instagram", 6),
    ("Pinterest", 4),
    ("someblog.example", 3),
]
COUNTRIES = [
    ("DE", 26), ("US", 22), ("GB", 14), ("FR", 9),
    ("NL", 7), ("ES", 6), ("IT", 5), (None, 11),
]
DESKTOP = [
    ("Chrome", "Windows"),
    ("Chrome", "Mac OS X"),
    ("Safari", "Mac OS X"),
    ("Firefox", "Linux"),
]
MOBILE = [("Mobile Safari", "iOS"), ("Chrome Mobile", "Android")]

# Daytime-weighted hours, so the "today" view has a believable shape.
HOUR_WEIGHTS = [1, 1, 1, 1, 1, 2, 4, 7, 11, 14, 16, 16, 15, 15, 16, 17, 16, 14, 12, 10, 8, 6, 4, 2]
BATCH_SIZE = 10_000
GOALS = ["signup", "add-to-basket", "newsletter"]

DEMO_EMAIL = "demo@beacon.local"
DEMO_PASSWORD = "local-demo-password"


def _ensure_site(session: Session, domain: str) -> None:
    """Give the seeded traffic an owner.

    Without this the events belong to a domain no account can see, so the
    dashboard shows nothing and a fresh clone looks broken.
    """
    if session.scalar(select(Site).where(Site.domain == domain)) is not None:
        return

    owner = session.scalar(select(User))
    if owner is None:
        owner = accounts.register(session, email=DEMO_EMAIL, password=DEMO_PASSWORD)
        print(f"created demo account {DEMO_EMAIL} / {DEMO_PASSWORD}")

    accounts.add_site(session, owner=owner, domain=domain)
    print(f"registered {domain} to {owner.email}")


def _pick(weighted):
    values, weights = zip(*weighted, strict=True)
    return random.choices(values, weights=weights, k=1)[0]


def _bucket(moment: dt.datetime, timezone: str) -> dict:
    """The three time fields an event carries, as the collector would set them."""
    day, hour = zones.local_parts(moment, timezone)
    return {"timestamp": moment, "day": day, "hour": hour}


def _visitors_for(day: dt.date, baseline: int) -> int:
    # Quieter at weekends, with a slow upward trend and one viral spike.
    weekday_factor = 0.62 if day.weekday() >= 5 else 1.0
    return max(1, int(random.gauss(baseline * weekday_factor, baseline * 0.18)))


def _rows_for_day(
    site_id: str,
    midnight: dt.datetime,
    visitors: int,
    now: dt.datetime,
    goal_rate: float,
    timezone: str,
) -> list[dict]:
    """One day's events. Built a day at a time so memory stays flat."""
    rows: list[dict] = []

    for _ in range(visitors):
        device = "mobile" if random.random() < 0.44 else "desktop"
        browser, operating_system = random.choice(MOBILE if device == "mobile" else DESKTOP)
        source = _pick(SOURCES)
        # Drawn from the seeded generator, so a given --seed really does
        # reproduce the same dataset; secrets deliberately ignores the seed.
        visitor = f"{random.getrandbits(64):016x}"
        width = random.choice([390, 414, 768, 1280, 1440, 1920])

        hour = random.choices(range(24), weights=HOUR_WEIGHTS, k=1)[0]
        arrived = midnight + dt.timedelta(
            hours=hour, minutes=random.randrange(60), seconds=random.randrange(60)
        )
        if arrived > now:
            continue

        # Everything that stays the same for one visitor, merged into each of
        # their events rather than rebuilt per row.
        base: dict = {
            "site_id": site_id,
            "visitor_id": visitor,
            "referrer_host": None,
            "browser": browser,
            "os": operating_system,
            "device": device,
            "country": _pick(COUNTRIES),
            "screen": screens.bucket(width),
        }

        # Most people read one page; a few browse several.
        depth = random.choices([1, 2, 3, 4], weights=[58, 24, 12, 6], k=1)[0]
        for step in range(depth):
            rows.append(
                base
                | {
                    **_bucket(
                        arrived + dt.timedelta(minutes=step * random.randrange(1, 4)),
                        timezone,
                    ),
                    "name": "pageview",
                    "pathname": _pick(PAGES),
                    # Only the entry page carries the referring source.
                    "source": source if step == 0 else "Direct",
                }
            )

        # A minority convert, which is what the Goals panel reports.
        if random.random() < goal_rate:
            rows.append(
                base
                | {
                    **_bucket(arrived + dt.timedelta(minutes=depth * 3), timezone),
                    "name": random.choice(GOALS),
                    "pathname": _pick(PAGES),
                    "source": "Direct",
                }
            )

    return rows


def generate(
    site_id: str, days: int, baseline: int, seed: int, reset: bool, goal_rate: float
) -> int:
    random.seed(seed)
    upgrade_database()

    with SessionLocal() as session:
        _ensure_site(session, site_id)
        timezone = accounts.timezone_for(session, site_id)

    if reset:
        with SessionLocal() as session:
            for table in (Event, DailyStat, HourlyStat):
                session.execute(delete(table))
            session.commit()

    now = dt.datetime.now(dt.UTC)
    start = (now - dt.timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    spike_day = random.randrange(4, max(5, days - 3))

    written = 0
    with SessionLocal() as session:
        for offset in range(days):
            midnight = start + dt.timedelta(days=offset)
            visitors = _visitors_for(midnight.date(), baseline)
            if offset == spike_day:
                visitors *= 4  # somebody posted a link

            rows = _rows_for_day(site_id, midnight, visitors, now, goal_rate, timezone)

            # Core inserts rather than ORM objects: at this size the identity
            # map and unit of work cost far more than the database write does.
            for chunk in batched(rows, BATCH_SIZE):
                session.execute(insert(Event), list(chunk))
            session.commit()

            written += len(rows)
            if days > 60 and offset % 50 == 0:
                print(f"  day {offset + 1}/{days}  {written:,} events", flush=True)

    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", default="demo.example")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--baseline", type=int, default=90, help="typical visitors per weekday")
    parser.add_argument("--seed", type=int, default=7, help="fixed for reproducible demo data")
    parser.add_argument("--reset", action="store_true", help="clear existing events first")
    parser.add_argument(
        "--goal-rate",
        type=float,
        default=0.06,
        help="fraction of visitors who fire a goal",
    )
    args = parser.parse_args()

    written = generate(
        args.site, args.days, args.baseline, args.seed, args.reset, args.goal_rate
    )
    print(f"seeded {written:,} events for {args.site} across {args.days} days")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
