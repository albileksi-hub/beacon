"""Resolve an IP address to a country code.

Together with the visitor hash, this is the only use an address is put to, and
it happens entirely in memory during the request. The address is never written
anywhere.
"""

from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.config import get_settings


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

        try:
            return self._reader.country(ip).country.iso_code
        except (geoip2.errors.AddressNotFoundError, ValueError):
            # Private ranges, unlisted blocks and malformed addresses all just
            # mean "country unknown".
            return None


@lru_cache
def get_country_resolver() -> CountryResolver:
    configured = get_settings().geoip_db_path
    if configured and Path(configured).is_file():
        return MaxMindCountryResolver(Path(configured))
    return NullCountryResolver()
