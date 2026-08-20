"""The site's own clock.

A day is the atomic unit of this system: it is the grain the aggregates are
built on, and the interval the visitor salt rotates with. Until now that day was
a UTC day, which means a site owner in Berlin was reading days that begin at
01:00 or 02:00 their time, and a site owner in Los Angeles was reading days that
begin at 16:00 the previous afternoon.

Two decisions follow from fixing that, and they are the whole design:

* **The bucket is computed once, at ingest, in the site's zone.** The event
  carries its own local day and hour, so no query ever has to truncate a
  timestamp. That removes the one place the database dialect leaked into the
  reporting SQL -- SQLite's strftime and Postgres's date_trunc have nothing in
  common -- and it means a day boundary is decided by Python's timezone
  database rather than by whatever the database server happens to think.

* **The salt rotates at the site's local midnight, not at UTC midnight.**
  Daily figures are summable into weeks and months precisely because a visitor
  cannot be recognised across a salt rotation. If the salt turned over at 02:00
  local, somebody browsing either side of that hour would count twice within a
  single local day, and the sum would quietly drift above the truth.
"""

import datetime as dt
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

DEFAULT = "UTC"

# Offered in the interface. The full database is some 600 entries, which is a
# list nobody scrolls; anything else can still be set through the API.
COMMON = (
    "UTC",
    "Europe/London",
    "Europe/Dublin",
    "Europe/Lisbon",
    "Europe/Madrid",
    "Europe/Paris",
    "Europe/Berlin",
    "Europe/Amsterdam",
    "Europe/Stockholm",
    "Europe/Warsaw",
    "Europe/Rome",
    "Europe/Athens",
    "Europe/Istanbul",
    "Europe/Moscow",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Sao_Paulo",
    "America/Toronto",
    "Asia/Dubai",
    "Asia/Kolkata",
    "Asia/Singapore",
    "Asia/Shanghai",
    "Asia/Tokyo",
    "Australia/Sydney",
    "Pacific/Auckland",
)


class UnknownTimezone(ValueError):
    pass


@lru_cache(maxsize=512)
def zone(name: str) -> ZoneInfo:
    """The named zone, or UTC if it is not one.

    Reached on every event, so the lookup is cached; ZoneInfo reads a file the
    first time it sees a name.
    """
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT)


def validate(name: str) -> str:
    """Normalise a timezone a person typed, refusing one that does not exist."""
    cleaned = (name or "").strip()
    if not cleaned:
        return DEFAULT
    if cleaned not in available_timezones():
        raise UnknownTimezone(f"{cleaned} is not a known timezone.")
    return cleaned


def local_parts(moment: dt.datetime, timezone: str) -> tuple[dt.date, int]:
    """The local day and hour an instant falls in, for the given zone.

    Both are stored on the event, which is what lets every later query group by
    a plain column instead of truncating a timestamp in dialect-specific SQL.
    """
    local = moment.astimezone(zone(timezone))
    return local.date(), local.hour
