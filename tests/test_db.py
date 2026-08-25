import pytest
from sqlalchemy import create_engine, delete, insert, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import Base, _engine_options, configure_sqlite, get_db, upgrade_database
from app.models import Event, Site, User


def test_get_db_yields_a_session_and_closes_it():
    sessions = get_db()
    session = next(sessions)

    assert isinstance(session, Session)
    assert session.is_active

    # Exhausting the generator runs the cleanup half of the dependency.
    next(sessions, None)
    assert not session.in_transaction()


def test_a_failed_request_does_not_hand_back_a_dirty_session(monkeypatch):
    sessions = get_db()
    session = next(sessions)

    rolled_back = []
    monkeypatch.setattr(session, "rollback", lambda: rolled_back.append(True))

    with pytest.raises(RuntimeError):
        sessions.throw(RuntimeError("the request blew up"))

    assert rolled_back == [True]


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


def test_foreign_keys_are_enforced(db_session):
    """SQLite ignores them unless asked, which would make CASCADE decorative."""
    with pytest.raises(IntegrityError):
        db_session.execute(
            insert(Site).values(domain="orphan.example", owner_id=999_999, public=False)
        )
        db_session.commit()


def test_deleting_an_account_takes_its_sites_with_it(db_session, account, site):
    # A Core delete, so this exercises the database's ON DELETE CASCADE rather
    # than the ORM's own cascade rules.
    db_session.execute(delete(User).where(User.id == account.id))
    db_session.commit()

    assert db_session.scalars(select(Site)).all() == []


def test_a_file_backed_database_uses_write_ahead_logging(tmp_path):
    """Readers stop blocking the writer, and commits stop rewriting a journal."""
    engine = create_engine(f"sqlite:///{tmp_path / 'wal.db'}")
    configure_sqlite(engine)

    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert connection.exec_driver_sql("PRAGMA synchronous").scalar() == 1  # NORMAL
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1

    engine.dispose()


def test_configuring_a_non_sqlite_engine_does_nothing(tmp_path):
    """The pragmas are SQLite-specific; Postgres must not see them."""
    engine = create_engine("postgresql+psycopg://user:pass@localhost/nowhere")

    configure_sqlite(engine)  # must not raise, and must not connect

    assert engine.dialect.name == "postgresql"


def test_postgres_connections_are_checked_before_reuse():
    """An idle pooled connection can be closed by the database or a proxy."""
    options = _engine_options("postgresql+psycopg://user:pass@localhost/beacon")

    assert options["pool_pre_ping"] is True
    assert options["pool_recycle"] > 0


def test_sqlite_gets_no_pool_tuning():
    assert "pool_size" not in _engine_options("sqlite:///./beacon.db")


def test_events_carries_only_the_indexes_that_earn_their_keep():
    """One for the live counter's window, one for the rollup builder's day.

    Neither is a prefix of the other, so neither is dead weight -- which the
    old (site_id, timestamp) index was, at a measured 40% of write throughput.
    """
    assert {index.name for index in Event.__table__.indexes} == {
        "ix_events_site_visitor",
        "ix_events_site_day",
    }


def test_the_session_factory_dependency_hands_back_the_real_one():
    """Overridden in tests, so its default needs saying out loud somewhere."""
    from app.db import SessionLocal
    from app.dependencies import get_session_factory

    assert get_session_factory() is SessionLocal


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        # What Render, Fly and Heroku actually inject.
        ("postgres://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        # Valid for SQLAlchemy, but selects psycopg2, which is not installed.
        ("postgresql://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        # Already explicit, and left alone.
        ("postgresql+psycopg://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("sqlite:///./beacon.db", "sqlite:///./beacon.db"),
    ],
)
def test_the_database_url_scheme_is_normalised(given: str, expected: str) -> None:
    """A managed host's own connection string has to work unedited.

    Both rejected shapes fail at import time, on boot, in the one environment
    where nobody can attach a debugger.
    """
    assert Settings(database_url=given).database_url == expected
