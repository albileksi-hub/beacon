"""Streaming CSV export of a site's aggregates.

The rows are the daily grain the dashboard is built on, which is everything the
service knows about a site once the raw events have aged out. Exporting that
rather than raw events means the file is bounded by days times dimensions
rather than by traffic, and it is the same data whether or not retention has
run.

Streamed a chunk at a time. Building the whole file in memory would make a busy
site's export a way to run the process out of it.
"""

import csv
import io
from collections.abc import Iterator
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models import DailyStat
from app.services.timeranges import TimeRange

COLUMNS = ("day", "dimension", "value", "visitors", "pageviews")

# Rows fetched from the database per round trip.
CHUNK = 1_000


class _Line:
    """csv.writer needs a file; this hands back one row at a time instead.

    Going through the csv module rather than joining with commas is not
    ceremony: a pathname can contain a comma or a quote, and hand-rolled CSV is
    how an export quietly corrupts itself.
    """

    def __init__(self) -> None:
        self._buffer = io.StringIO()
        self._writer = csv.writer(self._buffer, lineterminator="\n")

    def of(self, values: Any) -> str:
        self._writer.writerow(values)
        line = self._buffer.getvalue()
        self._buffer.seek(0)
        self._buffer.truncate(0)
        return line


def filename_for(site_id: str, time_range: TimeRange) -> str:
    start = time_range.start.date().isoformat()
    end = time_range.end.date().isoformat()
    return f"beacon-{site_id}-{start}-to-{end}.csv"


def daily_stats_csv(
    sessions: sessionmaker[Any], *, site_id: str, time_range: TimeRange
) -> Iterator[str]:
    """Yield the export one line at a time.

    Opens its own session rather than borrowing the request's: the generator
    outlives the handler that returned it, and a session torn down mid-stream
    would fail somewhere no one is looking.
    """
    line = _Line()
    yield line.of(COLUMNS)

    statement = (
        select(
            DailyStat.day,
            DailyStat.dimension,
            DailyStat.value,
            DailyStat.visitors,
            DailyStat.pageviews,
        )
        .where(
            DailyStat.site_id == site_id,
            DailyStat.day >= time_range.start.date(),
            DailyStat.day <= time_range.end.date(),
        )
        .order_by(DailyStat.day, DailyStat.dimension, DailyStat.value)
    )

    with sessions() as session:
        for row in session.execute(statement).yield_per(CHUNK):
            yield line.of((row.day.isoformat(), *row[1:]))
