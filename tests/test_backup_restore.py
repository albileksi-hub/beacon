"""The backup procedure in the runbook, actually performed.

docs/OPERATIONS.md ends its restore section with "a restore you have not
performed is a procedure you do not have". That was true of this one: the
commands were written down and had never been run, which is the same standing
as a comment claiming what the code does.

This rehearses the documented SQLite path end to end -- back up a live
database through SQLite, destroy the original, restore, and read the data back
out -- so the procedure is exercised on every push rather than on the morning
somebody needs it. The Postgres path still needs a real server and is not
covered here; that gap is named in the runbook rather than papered over.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import DailyStat, Event, Site, User
from app.services import accounts, rollups

RUNBOOK = Path(__file__).resolve().parent.parent / "docs" / "OPERATIONS.md"


def _populate(session) -> dict[str, int]:
    """A database with something in it worth losing."""
    owner = accounts.register(session, email="keeper@example.com", password="a-long-enough-pass")
    site = accounts.add_site(session, owner=owner, domain="kept.example")
    import datetime as dt

    today = dt.datetime.now(dt.UTC)
    for i in range(12):
        session.add(
            Event(
                site_id=site.domain,
                name="pageview",
                pathname=f"/page-{i}",
                day=today.date(),
                hour=today.hour,
                visitor_id=f"visitor{i:02d}",
                source="Direct",
                browser="Chrome",
                os="Mac OS X",
                device="desktop",
                screen="Desktop",
                timestamp=today,
            )
        )
    session.commit()
    rollups.rebuild_day(session, site_id=site.domain, day=today.date())
    session.commit()

    return {
        "users": session.scalar(select(func.count()).select_from(User)),
        "sites": session.scalar(select(func.count()).select_from(Site)),
        "events": session.scalar(select(func.count()).select_from(Event)),
        "daily_stats": session.scalar(select(func.count()).select_from(DailyStat)),
    }


def _counts(session) -> dict[str, int]:
    return {
        "users": session.scalar(select(func.count()).select_from(User)),
        "sites": session.scalar(select(func.count()).select_from(Site)),
        "events": session.scalar(select(func.count()).select_from(Event)),
        "daily_stats": session.scalar(select(func.count()).select_from(DailyStat)),
    }


@pytest.mark.skipif(shutil.which("sqlite3") is None, reason="needs the sqlite3 CLI")
def test_the_documented_backup_survives_losing_the_database(tmp_path):
    """Back up, destroy, restore, and read it back."""
    live = tmp_path / "beacon.db"
    engine = create_engine(f"sqlite:///{live}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as session:
        before = _populate(session)
    assert before["events"] == 12 and before["daily_stats"] > 0, "nothing to lose"
    engine.dispose()

    # The command the runbook gives: through SQLite, not `cp`.
    backup = tmp_path / "beacon-backup.db"
    subprocess.run(
        ["sqlite3", str(live), f".backup '{backup}'"], check=True, capture_output=True
    )
    assert backup.exists() and backup.stat().st_size > 0

    live.unlink()
    assert not live.exists(), "the disaster this is rehearsing did not happen"

    shutil.copy(backup, live)
    restored = create_engine(f"sqlite:///{live}")
    with sessionmaker(bind=restored)() as session:
        after = _counts(session)
        pages = session.scalars(select(Event.pathname).order_by(Event.pathname)).all()
    restored.dispose()

    assert after == before, f"the restore lost data: {before} -> {after}"
    assert pages == [f"/page-{i}" for i in sorted(range(12), key=str)], "rows came back wrong"


@pytest.mark.skipif(shutil.which("sqlite3") is None, reason="needs the sqlite3 CLI")
def test_the_rehearsal_uses_the_command_the_runbook_prints(tmp_path):
    """If the runbook changes, this must stop matching and be looked at.

    A rehearsal of a procedure other than the documented one proves nothing
    about the documented one.
    """
    documented = re.search(r"sqlite3 \S+ \"\.backup '([^']+)'\"", RUNBOOK.read_text())

    assert documented is not None, (
        "the runbook no longer shows a `sqlite3 ... \".backup '...'\"` command; "
        "this rehearsal is testing something the documentation does not say"
    )
