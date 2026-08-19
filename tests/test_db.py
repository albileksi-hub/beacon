from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.db import Base, get_db, upgrade_database


def test_get_db_yields_a_session_and_closes_it():
    sessions = get_db()
    session = next(sessions)

    assert isinstance(session, Session)
    assert session.is_active

    # Exhausting the generator runs the cleanup half of the dependency.
    next(sessions, None)
    assert not session.in_transaction()


def test_upgrade_runs_migrations_rather_than_creating_tables(monkeypatch):
    """One way to build a schema, so dev and production cannot drift apart."""
    upgraded_to = []
    monkeypatch.setattr(
        "alembic.command.upgrade", lambda config, revision: upgraded_to.append(revision)
    )

    upgrade_database()

    assert upgraded_to == ["head"]


def test_every_model_has_a_table(db_session):
    tables = set(inspect(db_session.get_bind()).get_table_names())

    assert tables == set(Base.metadata.tables)
