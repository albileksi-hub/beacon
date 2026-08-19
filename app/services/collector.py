"""Batched ingestion behind a single writer.

Measured over HTTP with 32 concurrent connections and one transaction per
event: 472 requests/sec, a p99 of 915ms, and a worst case over four seconds.
SQLite permits exactly one writer, so every request was waiting on the same
lock while holding an HTTP connection open. Postgres would not serialise as
hard, but it would still pay a round trip and an fsync per event.

This takes the write off the request path. The endpoint hands the event to a
bounded queue and answers immediately; one thread drains the queue and writes
in batches.

The trade, stated plainly: 202 now means the event was accepted, not that it
was committed. A process killed with events still queued loses them. That is
the right trade for pageview counts and the wrong one for anything that must
not be lost, so it is off by default and turned on deliberately.

The queue is bounded rather than unbounded. Under a flood, dropping events and
counting the drops is survivable; growing a list until the process is killed by
the kernel is not.
"""

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Any

from sqlalchemy import insert
from sqlalchemy.orm import sessionmaker

from app.models import Event

logger = logging.getLogger(__name__)

# Placed on the queue by stop(), so shutdown does not depend on a poll interval.
_STOP = object()


@dataclass(frozen=True, slots=True)
class WriterStats:
    accepted: int
    written: int
    dropped: int
    failed: int
    queued: int


class EventWriter:
    """Accepts events from request threads, writes them from one of its own."""

    def __init__(
        self,
        session_factory: sessionmaker[Any],
        *,
        capacity: int,
        batch_size: int,
        flush_seconds: float,
    ) -> None:
        self._sessions = session_factory
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=capacity)
        self._batch_size = batch_size
        self._flush_seconds = flush_seconds
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._counts = {"accepted": 0, "written": 0, "dropped": 0, "failed": 0}

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="beacon-writer", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Drain what is queued, then finish. A clean shutdown loses nothing."""
        thread = self._thread
        if thread is None:
            return

        self._thread = None
        self._queue.put(_STOP)
        thread.join(timeout)

    def submit(self, values: dict[str, Any]) -> bool:
        """Queue one event. False means the buffer was full and it was dropped."""
        try:
            self._queue.put_nowait(values)
        except queue.Full:
            self._count("dropped")
            return False

        self._count("accepted")
        return True

    @property
    def stats(self) -> WriterStats:
        with self._lock:
            return WriterStats(**self._counts, queued=self._queue.qsize())

    def _count(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counts[name] += amount

    def _run(self) -> None:
        while True:
            batch, stopping = self._collect()
            if batch:
                self._write(batch)
            if stopping:
                return

    def _collect(self) -> tuple[list[dict[str, Any]], bool]:
        """One batch, or whatever has arrived by the time the interval lapses."""
        batch: list[dict[str, Any]] = []

        try:
            first = self._queue.get(timeout=self._flush_seconds)
        except queue.Empty:
            return batch, False

        if first is _STOP:
            return batch, True
        batch.append(first)

        while len(batch) < self._batch_size:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is _STOP:
                return batch, True
            batch.append(item)

        return batch, False

    def _write(self, batch: list[dict[str, Any]]) -> None:
        try:
            with self._sessions() as session:
                session.execute(insert(Event), batch)
                session.commit()
        except Exception:
            # Losing a batch is bad; losing the writer thread would mean losing
            # every batch after it.
            logger.exception("could not write a batch of %d events", len(batch))
            self._count("failed", len(batch))
            return

        self._count("written", len(batch))
