from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, overridable by BEACON_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="BEACON_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./beacon.db"
    debug: bool = False

    # Signs the session cookie. Anyone holding this value can forge a login
    # for any account -- and the default below is a constant in a public
    # repository, so "anyone" means anyone. The application refuses to start
    # with it unless allow_insecure_sessions says otherwise.
    session_secret: str = "dev-only-insecure-session-secret"

    # The escape hatch for that refusal, named so it cannot be set by accident
    # while meaning something else. run.py sets it, because a developer running
    # the dev entrypoint has said all they need to say.
    allow_insecure_sessions: bool = False

    # Restricts the session cookie to HTTPS. Off by default so that
    # http://localhost works during development; on wherever it is deployed.
    session_https_only: bool = False

    # Seconds between in-process rollup refreshes. 0 disables the loop, which
    # is the default so that tests and one-off scripts never start one; the
    # dev server and any deployment turn it on explicitly.
    rollup_interval_seconds: int = 0

    # Days of raw events to keep. 0 keeps them forever, which is the safe
    # default: deleting them is irreversible, and only the operator knows
    # whether they will ever want to re-aggregate at a finer grain.
    raw_event_retention_days: int = 0

    # Events to buffer before writing them in one batch. 0 writes each event in
    # its own transaction, which is slower under load but means a 202 promises
    # the event is committed rather than merely accepted. See
    # app.services.collector for the measurements behind the trade.
    ingest_buffer_size: int = 0
    ingest_batch_size: int = 500
    ingest_flush_seconds: float = 0.25

    # Largest request body the service will read. An analytics event is a
    # few hundred bytes; nothing legitimate here comes close to this.
    max_request_bytes: int = 64 * 1024

    # Where this instance is reachable, used to build the reset link. A link
    # pointing at 127.0.0.1 is no use in an inbox.
    base_url: str = "http://127.0.0.1:8000"

    # Mail. With no host set, a reset link is written to the log instead --
    # see app.services.mail for why that is a decision rather than a stub.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True
    mail_from: str = "beacon@localhost"

    log_level: str = "INFO"
    # One JSON object per line, for anywhere logs are shipped and searched
    # rather than read by a person.
    log_json: bool = False

    # Path to a MaxMind GeoLite2-Country.mmdb file. Without it, country
    # resolution degrades to "unknown" rather than failing.
    geoip_db_path: str | None = None

    # Only enable behind a proxy that overwrites X-Forwarded-For. A client can
    # set that header themselves, so trusting it while directly exposed lets
    # anyone spoof their address.
    trust_proxy_headers: bool = False


    @field_validator("database_url")
    @classmethod
    def _normalise_database_url(cls, url: str) -> str:
        """Accept the URL shape managed Postgres hosts actually hand out.

        Render, Fly and Heroku all inject "postgres://", which SQLAlchemy 2.0
        refuses outright; and a bare "postgresql://" selects psycopg2, which is
        not a dependency -- this project uses psycopg 3. Both fail at import
        time, on boot, in the one environment nobody can attach a debugger to.
        Rewriting the scheme here means a deployment can pass the host's own
        connection string through untouched.
        """
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix) :]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
