from collections.abc import Iterator
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


def init_db() -> None:
    """Create tables directly from the models.

    Fine for local development; production schema changes go through Alembic.
    """
    from app import models  # noqa: F401  (import registers the tables on Base)

    Base.metadata.create_all(bind=engine)
