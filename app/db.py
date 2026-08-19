from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _connect_args(url: str) -> dict[str, Any]:
    # SQLite's default thread check trips on FastAPI's threadpool; Postgres needs nothing.
    return {"check_same_thread": False} if url.startswith("sqlite") else {}


_settings = get_settings()
engine = create_engine(_settings.database_url, connect_args=_connect_args(_settings.database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
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
