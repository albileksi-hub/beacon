"""Show what the database actually does with the queries the app issues.

    python analyse.py --site big.example

Rather than re-typing the SQL by hand and hoping it still matches, this hooks
the engine, runs the real service functions, captures every statement they
issue, and asks the database to explain each one. A plan that stops using an
index shows up here as a full scan.

Exits non-zero if any query in the hot path scans a table, so it can be used as
a check rather than only as a report.
"""

import argparse
import datetime as dt
import os
import statistics
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import event, func, select  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.models import DailyStat, Event, HourlyStat  # noqa: E402
from app.services import accounts, reports, rollups, stats  # noqa: E402
from app.services.stats import BreakdownProperty  # noqa: E402
from app.services.timeranges import Period, resolve  # noqa: E402

# Statements too trivial to be worth a plan, and noisy in the output.
BORING = ("BEGIN", "COMMIT", "ROLLBACK", "PRAGMA", "SAVEPOINT", "RELEASE")


@dataclass
class Captured:
    sql: str
    parameters: object


@dataclass
class Result:
    name: str
    milliseconds: list[float]
    statements: list[Captured] = field(default_factory=list)

    @property
    def median(self) -> float:
        return statistics.median(self.milliseconds)


@contextmanager
def capture() -> Iterator[list[Captured]]:
    """Every statement the engine runs inside the block."""
    seen: list[Captured] = []

    def before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        if not statement.lstrip().upper().startswith(BORING):
            seen.append(Captured(statement, parameters))

    event.listen(engine, "before_cursor_execute", before)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", before)


def measure(name: str, work: Callable[[], object], runs: int) -> Result:
    work()  # warm any caches so the first run does not skew the median

    timings = []
    for _ in range(runs):
        with SessionLocal():
            started = time.perf_counter()
            work()
            timings.append((time.perf_counter() - started) * 1000)

    with capture() as statements:
        work()

    return Result(name=name, milliseconds=timings, statements=statements)


def explain(captured: Captured) -> list[str]:
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "EXPLAIN QUERY PLAN " + captured.sql, captured.parameters or ()
        ).fetchall()
    return [row[-1] for row in rows]


def scenarios(site: str) -> list[tuple[str, Callable[[], object]]]:
    today = resolve(Period.TODAY)
    month = resolve(Period.LAST_30_DAYS)
    year = resolve(Period.LAST_12_MONTHS)

    def run(fn, **kwargs):
        def go():
            with SessionLocal() as session:
                return fn(session, site_id=site, **kwargs)

        return go

    return [
        ("summary, raw events, 30d", run(stats.summary, time_range=month)),
        ("summary, rollups, today", run(reports.summary, time_range=today)),
        ("summary, rollups, 30d", run(reports.summary, time_range=month)),
        ("summary, rollups, 12mo", run(reports.summary, time_range=year)),
        ("timeseries, rollups, today", run(reports.timeseries, time_range=today)),
        ("timeseries, rollups, 30d", run(reports.timeseries, time_range=month)),
        ("timeseries, rollups, 12mo", run(reports.timeseries, time_range=year)),
        (
            "breakdown pages, rollups, 30d",
            run(reports.breakdown, time_range=month, prop=BreakdownProperty.PAGE),
        ),
        (
            "breakdown goals, rollups, 12mo",
            run(reports.breakdown, time_range=year, prop=BreakdownProperty.EVENT),
        ),
        ("live visitors, raw events", run(reports.live_visitors)),
        (
            "collector: is this domain registered",
            lambda: _site_lookup(site),
        ),
    ]


def _site_lookup(site: str) -> bool:
    with SessionLocal() as session:
        return accounts.site_is_registered(session, site)


def ingest_rate(site: str, count: int) -> None:
    """What the collector costs per event, committed one at a time.

    Batching would go faster, but it would also lose events on a crash, and
    the collector answers 202 on the promise that the event is safe.
    """
    from app.services.visitors import current_salt, visitor_id

    now = dt.datetime.now(dt.UTC)
    with SessionLocal() as session:
        salt = current_salt(session)

        started = time.perf_counter()
        for index in range(count):
            # The lookup the collector does on every event before storing it.
            accounts.site_is_registered(session, site)
            session.add(
                Event(
                    site_id=site,
                    visitor_id=visitor_id(
                        salt=salt, site_id=site, ip=f"203.0.113.{index % 255}", user_agent="probe"
                    ),
                    timestamp=now,
                    name="pageview",
                    pathname="/ingest-probe",
                    source="Direct",
                    browser="Chrome",
                    os="Windows",
                    device="desktop",
                    country="DE",
                    screen="Laptop",
                )
            )
            session.commit()
        elapsed = time.perf_counter() - started

        session.query(Event).filter(Event.pathname == "/ingest-probe").delete()
        session.commit()

    print()
    print(
        f"ingest: {count / elapsed:,.0f} events/sec "
        f"({elapsed / count * 1000:.2f}ms each, committed individually)"
    )


def table_sizes() -> None:
    with SessionLocal() as session:
        print("  rows")
        for model in (Event, DailyStat, HourlyStat):
            count = session.scalar(select(func.count()).select_from(model))
            print(f"    {model.__tablename__:<16}{count or 0:>12,}")

    database = PROJECT_ROOT / str(engine.url).rsplit("/", 1)[-1]
    if database.is_file():
        print(f"\n  database file    {database.stat().st_size / 1024**2:>9.1f} MB")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", default="big.example")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument(
        "--rollup",
        action="store_true",
        help="time a single day's rollup rebuild as well",
    )
    parser.add_argument(
        "--ingest",
        type=int,
        default=0,
        help="measure collector throughput over this many events",
    )
    args = parser.parse_args()

    print(f"database  {engine.url}\n")
    table_sizes()

    print(f"\n{'query':<34}{'median':>10}   plan")
    print("-" * 96)

    scanned: list[str] = []
    for name, work in scenarios(args.site):
        result = measure(name, work, args.runs)
        plans = [line for captured in result.statements for line in explain(captured)]
        full_scans = [line for line in plans if "SCAN" in line and "USING" not in line]
        if full_scans:
            scanned.append(name)

        marker = "  <-- FULL SCAN" if full_scans else ""
        print(f"{name:<34}{result.median:>8.2f}ms   {plans[0] if plans else '(none)'}{marker}")
        for line in plans[1:]:
            print(f"{'':<44}{line}")

    if args.rollup:
        print()
        with SessionLocal() as session:
            yesterday = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)
            started = time.perf_counter()
            rows = rollups.rebuild_day(session, site_id=args.site, day=yesterday)
            elapsed = (time.perf_counter() - started) * 1000
        print(f"rollup rebuild of one day: {elapsed:.0f}ms for {rows} aggregate rows")

    if args.ingest:
        ingest_rate(args.site, args.ingest)

    if scanned:
        print(f"\nFULL TABLE SCANS in: {', '.join(scanned)}")
        return 1

    print("\nEvery hot query is index-backed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
