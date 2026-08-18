"""Small operational entry point for local development."""

import sys

from app.db import init_db


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python manage.py initdb")
        return 1

    command = sys.argv[1]
    if command == "initdb":
        init_db()
        print("database initialised")
        return 0

    print(f"unknown command: {command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
