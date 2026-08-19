"""Compare a dashboard render against raw events versus the rollups.

    python bench.py --site demo.example --period 30d

Times the six queries a single dashboard page issues, run against both paths.
"""

import argparse
import os
import statistics
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import func, select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import DailyStat, Event  # noqa: E402
from app.routers.dashboard import PANELS  # noqa: E402
from app.services import reports, stats  # noqa: E402
from app.services.timeranges import Period, resolve  # noqa: E402


def render(module, session, site_id, time_range) -> None:
    """Exactly the queries one dashboard page issues."""
    module.summary(session, site_id=site_id, time_range=time_range)
    module.timeseries(session, site_id=site_id, time_range=time_range)
    for _, prop in PANELS:
        module.breakdown(session, site_id=site_id, time_range=time_range, prop=prop)


def time_it(module, session, site_id, time_range, runs: int) -> list[float]:
    timings = []
    for _ in range(runs):
        started = time.perf_counter()
        render(module, session, site_id, time_range)
        timings.append((time.perf_counter() - started) * 1000)
    return timings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", default="demo.example")
    parser.add_argument("--period", default=Period.LAST_30_DAYS, type=Period)
    parser.add_argument("--runs", type=int, default=7)
    args = parser.parse_args()

    time_range = resolve(args.period)

    with SessionLocal() as session:
        events = session.scalar(
            select(func.count(Event.id)).where(Event.site_id == args.site)
        )
        aggregates = session.scalar(
            select(func.count(DailyStat.id)).where(DailyStat.site_id == args.site)
        )

        raw = time_it(stats, session, args.site, time_range, args.runs)
        rolled = time_it(reports, session, args.site, time_range, args.runs)

    raw_median = statistics.median(raw)
    rolled_median = statistics.median(rolled)

    print(f"site      {args.site}")
    print(f"period    {args.period}")
    print(f"events    {events:,}")
    print(f"rollups   {aggregates:,} daily rows")
    print(f"runs      {args.runs}")
    print()
    print(f"{'':10}{'median':>12}{'min':>12}{'max':>12}")
    print(f"{'raw':10}{raw_median:>10.1f}ms{min(raw):>10.1f}ms{max(raw):>10.1f}ms")
    print(f"{'rollup':10}{rolled_median:>10.1f}ms{min(rolled):>10.1f}ms{max(rolled):>10.1f}ms")
    print()
    print(f"speedup   {raw_median / rolled_median:.1f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
