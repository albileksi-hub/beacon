"""Small operational entry point for local development."""

import argparse

from app.db import SessionLocal, init_db
from app.services import rollups


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("initdb", help="create tables from the models")

    rollup = commands.add_parser("rollup", help="rebuild the pre-aggregated tables")
    rollup.add_argument(
        "--days",
        type=int,
        default=rollups.RECENT_DAYS,
        help="how many days back to rebuild (use a large number to backfill)",
    )

    args = parser.parse_args()

    if args.command == "initdb":
        init_db()
        print("database initialised")
        return 0

    with SessionLocal() as session:
        rebuilt = rollups.refresh(session, days_back=args.days)
    print(f"rebuilt {rebuilt:,} site-days")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
