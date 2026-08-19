import asyncio
import contextlib
import datetime as dt
import time

from fastapi import FastAPI
from sqlalchemy import select

from app import background
from app.config import Settings
from app.models import DailyStat, Event


def _settings(monkeypatch, interval: int) -> None:
    monkeypatch.setattr(
        background, "get_settings", lambda: Settings(rollup_interval_seconds=interval)
    )


def test_no_loop_runs_when_the_interval_is_zero(monkeypatch):
    app = FastAPI()
    _settings(monkeypatch, 0)

    async def exercise():
        async with background.lifespan(app):
            pass

    asyncio.run(exercise())

    assert app.state.rollup_task is None


def test_the_loop_starts_and_is_cancelled_on_shutdown(monkeypatch):
    app = FastAPI()
    _settings(monkeypatch, 60)

    async def exercise():
        async with background.lifespan(app):
            assert not app.state.rollup_task.done()

    asyncio.run(exercise())

    assert app.state.rollup_task.cancelled()


def test_the_loop_keeps_going_after_a_failed_refresh(monkeypatch):
    """Stale numbers are not a reason to take the process down."""
    attempts = []

    def sometimes_broken():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("database went away")
        return 0

    monkeypatch.setattr(background, "refresh_rollups_once", sometimes_broken)

    async def exercise():
        task = asyncio.create_task(background._rollup_loop(0.001))
        # Wait for the condition rather than for a duration, so the test does
        # not depend on how fast the machine running it happens to be.
        deadline = time.monotonic() + 5
        while len(attempts) < 2 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(exercise())

    assert len(attempts) >= 2, "the loop stopped after the first failure"


def test_a_refresh_rebuilds_the_aggregates(db_session, monkeypatch):
    db_session.add(
        Event(
            site_id="blue-mug.example",
            visitor_id="a",
            timestamp=dt.datetime.now(dt.UTC),
            name="pageview",
            pathname="/",
            source="Direct",
            browser="Chrome",
            os="Windows",
            device="desktop",
            country="DE",
            screen_width=1920,
        )
    )
    db_session.commit()

    # The job opens its own session; hand it the test's instead.
    monkeypatch.setattr(
        background, "SessionLocal", lambda: contextlib.nullcontext(db_session)
    )

    assert background.refresh_rollups_once() > 0
    assert db_session.scalars(select(DailyStat)).all() != []
