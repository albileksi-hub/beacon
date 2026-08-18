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
import secrets
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Event  # noqa: E402

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


def _pick(weighted):
    values, weights = zip(*weighted, strict=True)
    return random.choices(values, weights=weights, k=1)[0]


def _visitors_for(day: dt.date, baseline: int) -> int:
    # Quieter at weekends, with a slow upward trend and one viral spike.
    weekday_factor = 0.62 if day.weekday() >= 5 else 1.0
    return max(1, int(random.gauss(baseline * weekday_factor, baseline * 0.18)))


def generate(site_id: str, days: int, baseline: int, seed: int) -> int:
    random.seed(seed)
    init_db()

    now = dt.datetime.now(dt.UTC)
    start = (now - dt.timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    spike_day = random.randrange(4, max(5, days - 3))

    events: list[Event] = []
    for offset in range(days):
        midnight = start + dt.timedelta(days=offset)
        count = _visitors_for(midnight.date(), baseline)
        if offset == spike_day:
            count *= 4  # somebody posted a link

        for _ in range(count):
            device = "mobile" if random.random() < 0.44 else "desktop"
            browser, operating_system = random.choice(MOBILE if device == "mobile" else DESKTOP)
            source = _pick(SOURCES)
            visitor = secrets.token_hex(8)

            hour = random.choices(range(24), weights=HOUR_WEIGHTS, k=1)[0]
            arrived = midnight + dt.timedelta(
                hours=hour, minutes=random.randrange(60), seconds=random.randrange(60)
            )
            if arrived > now:
                continue

            # Most people read one page; a few browse several.
            for depth in range(random.choices([1, 2, 3, 4], weights=[58, 24, 12, 6], k=1)[0]):
                events.append(
                    Event(
                        site_id=site_id,
                        visitor_id=visitor,
                        timestamp=arrived + dt.timedelta(minutes=depth * random.randrange(1, 4)),
                        name="pageview",
                        pathname=_pick(PAGES),
                        # Only the entry page carries the referring source.
                        source=source if depth == 0 else "Direct",
                        referrer_host=None,
                        browser=browser,
                        os=operating_system,
                        device=device,
                        country=_pick(COUNTRIES),
                        screen_width=random.choice([390, 414, 768, 1280, 1440, 1920]),
                    )
                )

    with SessionLocal() as session:
        session.add_all(events)
        session.commit()

    return len(events)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", default="demo.example")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--baseline", type=int, default=90, help="typical visitors per weekday")
    parser.add_argument("--seed", type=int, default=7, help="fixed for reproducible demo data")
    args = parser.parse_args()

    written = generate(args.site, args.days, args.baseline, args.seed)
    print(f"seeded {written:,} events for {args.site} across {args.days} days")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
