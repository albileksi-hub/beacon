from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, overridable by BEACON_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="BEACON_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./beacon.db"
    debug: bool = False

    # Signs the session cookie. Anyone holding this value can forge a login
    # for any account, so it must be replaced in production.
    session_secret: str = "dev-only-insecure-session-secret"

    # Restricts the session cookie to HTTPS. Off by default so that
    # http://localhost works during development; on wherever it is deployed.
    session_https_only: bool = False

    # Seconds between in-process rollup refreshes. 0 disables the loop, which
    # is the default so that tests and one-off scripts never start one; the
    # dev server and any deployment turn it on explicitly.
    rollup_interval_seconds: int = 0

    # Path to a MaxMind GeoLite2-Country.mmdb file. Without it, country
    # resolution degrades to "unknown" rather than failing.
    geoip_db_path: str | None = None

    # Only enable behind a proxy that overwrites X-Forwarded-For. A client can
    # set that header themselves, so trusting it while directly exposed lets
    # anyone spoof their address.
    trust_proxy_headers: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
