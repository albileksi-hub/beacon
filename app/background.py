"""The in-process rollup loop.

An alternative to wiring up cron for a single job. Anything larger belongs in a
real scheduler, but one periodic aggregation does not need one.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.config import get_settings
from app.db import SessionLocal
from app.services import rollups

logger = logging.getLogger(__name__)


def refresh_rollups_once() -> int:
    with SessionLocal() as session:
        return rollups.refresh(session)


async def _rollup_loop(interval_seconds: int) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            # The ORM is synchronous; running it inline would stall every
            # request being served on this worker for the duration.
            rebuilt = await asyncio.to_thread(refresh_rollups_once)
            logger.debug("rollup refresh rebuilt %s site-days", rebuilt)
        except Exception:
            # A failed refresh means slightly stale numbers, which is not a
            # reason to take the loop -- or the process -- down.
            logger.exception("rollup refresh failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    interval = get_settings().rollup_interval_seconds
    task = asyncio.create_task(_rollup_loop(interval)) if interval > 0 else None
    # Kept on app.state so the loop can be inspected rather than merely assumed.
    app.state.rollup_task = task

    yield

    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
