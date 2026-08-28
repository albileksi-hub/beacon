"""Resolve an IP address to a country code.

Together with the visitor hash, this is the only use an address is put to, and
it happens entirely in memory during the request. The address is never written
anywhere.
"""

import logging
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.config import get_settings

logger = logging.getLogger(__name__)


class CountryResolver(Protocol):
    def country_code(self, ip: str) -> str | None: ...


class NullCountryResolver:
    """Fallback when no GeoIP database is configured, as in tests and local dev.

    Missing geo data degrades the reports; it must never fail a request.
    """

    def country_code(self, ip: str) -> str | None:
        return None


class MaxMindCountryResolver:
    def __init__(self, database_path: Path) -> None:
        import geoip2.database

        # The reader mmaps the database once and is safe to share across threads.
        self._reader = geoip2.database.Reader(str(database_path))

    def country_code(self, ip: str) -> str | None:
        import geoip2.errors
        import maxminddb

        try:
            return self._reader.country(ip).country.iso_code
        except (geoip2.errors.GeoIP2Error, maxminddb.InvalidDatabaseError, ValueError):
            # Private ranges, unlisted blocks, malformed addresses, and a
            # database that opened but cannot answer this query: all of them
            # just mean "country unknown". AddressNotFoundError is a
            # GeoIP2Error, so the broader class covers what was caught before.
            return None


@lru_cache
def get_country_resolver() -> CountryResolver:
    """The resolver this process will use, decided once.

    Anything that stops a real one being built degrades to the null resolver
    instead of propagating. The collector asks for this on every event, and
    lru_cache does not cache an exception -- so a database that cannot be
    opened would not fail once, it would fail every single request, for the
    least important column on the row.

    Both fallbacks are logged. Silently reporting every visitor as unknown is
    the kind of thing somebody notices a month later.
    """
    configured = get_settings().geoip_db_path
    if not configured:
        return NullCountryResolver()

    path = Path(configured)
    if not path.is_file():
        logger.warning(
            "BEACON_GEOIP_DB_PATH is set to %s, which is not a file; "
            "country will be unknown for every visitor",
            path,
        )
        return NullCountryResolver()

    try:
        return MaxMindCountryResolver(path)
    except Exception:
        # A truncated download or an update interrupted halfway leaves a file
        # that exists and cannot be read.
        logger.exception(
            "could not open the GeoIP database at %s; country will be unknown "
            "for every visitor",
            path,
        )
        return NullCountryResolver()
