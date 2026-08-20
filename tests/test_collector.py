"""The buffered writer.

Its whole purpose is to take the database write off the request path, so the
tests are about what happens at the edges: a full buffer, a failing database,
and a shutdown with work still queued.
"""

import datetime as dt
import time

import pytest
from sqlalchemy import select

from app.models import Event
from app.services.collector import _STOP, EventWriter

SITE = "blue-mug.example"


def _values(**overrides):
    moment = dt.datetime.now(dt.UTC)
    payload = {
        "site_id": SITE,
        "visitor_id": "visitor-1",
        "timestamp": moment,
        "day": moment.date(),
        "hour": moment.hour,
        "name": "pageview",
        "pathname": "/",
        "source": "Direct",
        "browser": "Chrome",
        "os": "Windows",
        "device": "desktop",
        "country": "DE",
        "screen": "Laptop",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def sessions(db_session):
    """A factory handing every caller the test's own session."""

    class _Factory:
        def __call__(self):
            return self

        def __enter__(self):
            return db_session

        def __exit__(self, *_exc):
            return False

    return _Factory()


def _wait_for(condition, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_submitted_events_reach_the_database(sessions, db_session):
    writer = EventWriter(sessions, capacity=100, batch_size=10, flush_seconds=0.02)
    writer.start()
    try:
        for index in range(5):
            assert writer.submit(_values(pathname=f"/page-{index}"))

        assert _wait_for(lambda: len(db_session.scalars(select(Event)).all()) == 5)
    finally:
        writer.stop()

    assert writer.stats.written == 5


def test_events_are_written_in_batches_not_one_at_a_time(sessions, db_session):
    writer = EventWriter(sessions, capacity=500, batch_size=50, flush_seconds=0.05)
    writer.start()
    try:
        for _ in range(50):
            writer.submit(_values())

        assert _wait_for(lambda: writer.stats.written == 50)
    finally:
        writer.stop()

    assert len(db_session.scalars(select(Event)).all()) == 50


def test_a_full_buffer_drops_rather_than_growing(sessions):
    """Unbounded growth under a flood ends with the kernel killing the process."""
    writer = EventWriter(sessions, capacity=2, batch_size=10, flush_seconds=30)

    assert writer.submit(_values()) is True
    assert writer.submit(_values()) is True
    assert writer.submit(_values()) is False

    assert writer.stats.accepted == 2
    assert writer.stats.dropped == 1


def test_stopping_flushes_what_is_still_queued(sessions, db_session):
    """A clean shutdown must not lose accepted events."""
    writer = EventWriter(sessions, capacity=100, batch_size=100, flush_seconds=30)
    writer.start()
    for _ in range(7):
        writer.submit(_values())

    writer.stop()

    assert len(db_session.scalars(select(Event)).all()) == 7


def test_a_failing_write_does_not_kill_the_writer(db_session):
    """Losing one batch is bad; losing every batch after it is worse."""
    attempts = []

    class _Broken:
        def __call__(self):
            return self

        def __enter__(self):
            attempts.append(1)
            raise RuntimeError("database went away")

        def __exit__(self, *_exc):
            return False

    writer = EventWriter(_Broken(), capacity=100, batch_size=1, flush_seconds=0.02)
    writer.start()
    try:
        writer.submit(_values())
        writer.submit(_values())

        assert _wait_for(lambda: len(attempts) >= 2)
    finally:
        writer.stop()

    assert writer.stats.failed >= 1
    assert writer.stats.written == 0


def test_stopping_a_writer_that_never_started_is_harmless(sessions):
    EventWriter(sessions, capacity=10, batch_size=10, flush_seconds=1).stop()


def test_starting_twice_runs_one_thread(sessions, db_session):
    writer = EventWriter(sessions, capacity=100, batch_size=10, flush_seconds=0.02)
    writer.start()
    writer.start()
    try:
        writer.submit(_values())
        assert _wait_for(lambda: writer.stats.written == 1)
    finally:
        writer.stop()

    assert len(db_session.scalars(select(Event)).all()) == 1


def test_stats_report_what_is_waiting(sessions):
    writer = EventWriter(sessions, capacity=10, batch_size=10, flush_seconds=30)
    writer.submit(_values())
    writer.submit(_values())

    stats = writer.stats
    assert stats.queued == 2
    assert stats.accepted == 2
    assert stats.written == 0


def test_a_stop_arriving_mid_batch_still_writes_what_came_before(sessions):
    """Reached through _collect directly: as a race it would be untestable."""
    writer = EventWriter(sessions, capacity=10, batch_size=100, flush_seconds=0.01)
    writer.submit(_values())
    writer.submit(_values())
    writer._queue.put(_STOP)

    batch, stopping = writer._collect()

    assert len(batch) == 2
    assert stopping is True


def test_an_idle_writer_waits_rather_than_spinning(sessions):
    """Most of the time there is nothing to write; that path matters too."""
    writer = EventWriter(sessions, capacity=10, batch_size=10, flush_seconds=0.01)

    batch, stopping = writer._collect()

    assert batch == []
    assert stopping is False
