from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.db
from app.db import get_db, init_db


def test_get_db_yields_a_session_and_closes_it():
    sessions = get_db()
    session = next(sessions)

    assert isinstance(session, Session)
    assert session.is_active

    # Exhausting the generator runs the cleanup half of the dependency.
    next(sessions, None)
    assert not session.in_transaction()


def test_init_db_creates_every_table(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    monkeypatch.setattr(app.db, "engine", engine)

    init_db()

    assert set(inspect(engine).get_table_names()) == {
        "events",
        "daily_salts",
        "users",
        "sites",
        "daily_stats",
        "hourly_stats",
        "login_attempts",
    }
