from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _engine_options(url: str) -> dict[str, Any]:
    if url.startswith("sqlite"):
        # SQLite's default thread check trips on FastAPI's threadpool.
        return {"connect_args": {"check_same_thread": False}}

    return {
        # A pooled connection can be closed while it sits idle -- by the
        # database's own timeout, by a proxy, or by a deploy. Without this the
        # next request to pick that connection up fails, rather than quietly
        # reconnecting.
        "pool_pre_ping": True,
        "pool_size": 10,
        "max_overflow": 20,
        # Retire connections before anything upstream decides to.
        "pool_recycle": 1800,
    }


def configure_sqlite(target: Engine) -> None:
    """Apply the connection settings this workload needs from SQLite.

    Its defaults are tuned for a single-user file, not for a service taking
    concurrent writes, and two of them are actively wrong here.
    """
    if target.dialect.name != "sqlite":
        return

    @event.listens_for(target, "connect")
    def _apply(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()

        # Readers stop blocking the writer, and a commit no longer rewrites a
        # rollback journal. Worth roughly 4x on the collector's insert rate.
        cursor.execute("PRAGMA journal_mode=WAL")

        # With WAL this is still durable across a process crash; only an
        # operating-system crash can lose the last few transactions. For
        # pageview counts that is the right trade, and it is the difference
        # between 800 and 8,000 events per second.
        cursor.execute("PRAGMA synchronous=NORMAL")

        # SQLite ignores foreign keys unless asked, which silently makes every
        # ON DELETE CASCADE in the schema decorative.
        cursor.execute("PRAGMA foreign_keys=ON")

        # Wait for a competing writer instead of failing immediately.
        cursor.execute("PRAGMA busy_timeout=5000")

        cursor.close()


_settings = get_settings()
engine = create_engine(_settings.database_url, **_engine_options(_settings.database_url))
configure_sqlite(engine)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    except Exception:
        # A request that raised mid-transaction must not hand a dirty session
        # back to the pool.
        db.rollback()
        raise
    finally:
        db.close()


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def upgrade_database() -> None:
    """Bring the schema up to date.

    The only way the schema is ever built outside the test suite. Creating
    tables straight from the models in development and migrating in production
    means a model change with no migration works locally and fails on deploy.
    """
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
