"""Periodic housekeeping.

An alternative to wiring up cron for a handful of jobs. Anything larger belongs
in a real scheduler, but this does not need one.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.config import get_settings
from app.db import SessionLocal
from app.services import rollups, throttle, visitors

logger = logging.getLogger(__name__)


def run_maintenance() -> int:
    """Refresh the aggregates, then expire everything past its retention.

    The purges have to happen on a timer rather than only as a side effect of
    traffic. A site with no visitors for a week creates no salt for a week, and
    the old salts would sit there re-derivable the whole time -- which is
    exactly the promise the salt rotation exists to keep. The same applies to
    login attempts on a quiet instance.
    """
    with SessionLocal() as session:
        rebuilt = rollups.refresh(session)
        visitors.purge_expired_salts(session)
        throttle.purge_expired(session)
        return rebuilt


async def _maintenance_loop(interval_seconds: float) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            # The ORM is synchronous; running it inline would stall every
            # request being served on this worker for the duration.
            rebuilt = await asyncio.to_thread(run_maintenance)
            logger.debug("maintenance rebuilt %s site-days", rebuilt)
        except Exception:
            # Failing once means slightly stale numbers, which is not a reason
            # to take the loop -- or the process -- down.
            logger.exception("maintenance run failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    interval = get_settings().rollup_interval_seconds
    task = asyncio.create_task(_maintenance_loop(interval)) if interval > 0 else None
    # Kept on app.state so the loop can be inspected rather than merely assumed.
    app.state.maintenance_task = task

    yield

    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
