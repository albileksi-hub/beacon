import logging
from pathlib import Path
from types import SimpleNamespace

import geoip2.database
import geoip2.errors
import pytest

from app.config import Settings
from app.services.geo import (
    MaxMindCountryResolver,
    NullCountryResolver,
    get_country_resolver,
)


class _StubReader:
    """Stands in for a MaxMind database so tests need no 60MB binary blob."""

    def __init__(self, result):
        self._result = result

    def country(self, ip):
        if isinstance(self._result, Exception):
            raise self._result
        return SimpleNamespace(country=SimpleNamespace(iso_code=self._result))


@pytest.fixture
def clear_resolver_cache():
    get_country_resolver.cache_clear()
    yield
    get_country_resolver.cache_clear()


def _resolver_with(monkeypatch, result) -> MaxMindCountryResolver:
    monkeypatch.setattr(geoip2.database, "Reader", lambda path: _StubReader(result))
    return MaxMindCountryResolver(Path("does-not-need-to-exist.mmdb"))


def test_resolves_a_country_code(monkeypatch):
    resolver = _resolver_with(monkeypatch, "DE")

    assert resolver.country_code("203.0.113.7") == "DE"


def test_unlisted_addresses_resolve_to_unknown(monkeypatch):
    resolver = _resolver_with(monkeypatch, geoip2.errors.AddressNotFoundError("not in database"))

    assert resolver.country_code("10.0.0.1") is None


def test_malformed_addresses_resolve_to_unknown(monkeypatch):
    resolver = _resolver_with(monkeypatch, ValueError("not an address"))

    assert resolver.country_code("definitely-not-an-ip") is None


def test_falls_back_to_null_resolver_without_a_database(monkeypatch, clear_resolver_cache):
    monkeypatch.setattr("app.services.geo.get_settings", lambda: Settings(geoip_db_path=None))

    assert isinstance(get_country_resolver(), NullCountryResolver)
    assert get_country_resolver().country_code("203.0.113.7") is None


def test_falls_back_when_the_configured_database_is_missing(monkeypatch, clear_resolver_cache):
    """A bad path must degrade the reports, never break ingestion."""
    monkeypatch.setattr(
        "app.services.geo.get_settings",
        lambda: Settings(geoip_db_path="/nowhere/GeoLite2-Country.mmdb"),
    )

    assert isinstance(get_country_resolver(), NullCountryResolver)


def test_uses_the_maxmind_resolver_when_a_database_is_present(
    monkeypatch, tmp_path, clear_resolver_cache
):
    database = tmp_path / "GeoLite2-Country.mmdb"
    database.write_bytes(b"")
    monkeypatch.setattr(geoip2.database, "Reader", lambda path: _StubReader("FR"))
    monkeypatch.setattr(
        "app.services.geo.get_settings",
        lambda: Settings(geoip_db_path=str(database)),
    )

    resolver = get_country_resolver()

    assert isinstance(resolver, MaxMindCountryResolver)
    assert resolver.country_code("203.0.113.7") == "FR"


def test_a_corrupt_database_degrades_instead_of_failing_every_request(
    monkeypatch, tmp_path, caplog, clear_resolver_cache
):
    """The real Reader, against a real file that is not a real database.

    Every other test here stubs geoip2.database.Reader, so the open path was
    never exercised and this went unnoticed: a truncated download or an update
    interrupted halfway raises InvalidDatabaseError out of get_country_resolver,
    which the collector calls on every event. lru_cache does not cache
    exceptions, so it would not fail once -- it would fail every request, for
    the least important column on the row.
    """
    database = tmp_path / "GeoLite2-Country.mmdb"
    database.write_bytes(b"not a maxmind database" * 50)
    monkeypatch.setattr(
        "app.services.geo.get_settings", lambda: Settings(geoip_db_path=str(database))
    )

    with caplog.at_level(logging.ERROR, logger="app.services.geo"):
        resolver = get_country_resolver()

    assert isinstance(resolver, NullCountryResolver)
    assert resolver.country_code("203.0.113.7") is None
    assert "could not open the GeoIP database" in caplog.text, "the operator has to be told"


def test_a_misconfigured_path_says_so(monkeypatch, caplog, clear_resolver_cache):
    """It already degraded, but silently -- which is a month of unknown countries."""
    monkeypatch.setattr(
        "app.services.geo.get_settings",
        lambda: Settings(geoip_db_path="/nowhere/GeoLite2-Country.mmdb"),
    )

    with caplog.at_level(logging.WARNING, logger="app.services.geo"):
        assert isinstance(get_country_resolver(), NullCountryResolver)

    assert "is not a file" in caplog.text


def test_a_lookup_that_fails_on_a_working_database_is_unknown(monkeypatch):
    """A database can open and still be unable to answer a particular query."""
    import maxminddb

    resolver = _resolver_with(monkeypatch, maxminddb.InvalidDatabaseError("bad record"))

    assert resolver.country_code("203.0.113.7") is None
