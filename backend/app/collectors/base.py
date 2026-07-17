"""Common collector framework (PLAN.md §5.1/§5.4).

- ``REGISTRY``: job name -> collect_fn. Each collector module registers itself by
  assigning ``REGISTRY["job_name"] = collect_fn`` at import time (see collectors/macro.py).
  Other collectors (e.g. market_flow, stock_flow, ohlcv) register into the same dict —
  this module intentionally has zero knowledge of what jobs exist.
- ``run_job``: runs one collect_fn with retry (3 attempts, exponential backoff),
  then records ok/fail into ``collect_log`` (upsert on the (job, target_date) PK).

collect_fn contract: ``async def collect_fn(session: AsyncSession, target_date: date) -> int``
  — it performs its own upserts against `session` and returns the number of rows written.
  It must NOT commit/rollback the session itself; run_job owns the transaction so that a
  failed attempt can be rolled back cleanly before retrying.
  A collect_fn may instead return ``(rows: int, message: str | None)`` when it wants to
  leave a note in ``collect_log.message`` even on success (e.g. collectors/ohlcv.py uses
  this to record which market fell back to its secondary source). Plain ``int`` returns
  keep working unchanged (message stays NULL) — see run_job below.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import async_session_factory
from ..models import CollectLog

logger = logging.getLogger(__name__)

CollectResult = int | tuple[int, str | None]
CollectFn = Callable[[AsyncSession, dt.date], Awaitable[CollectResult]]

# Job registry shared by every collector module. Populated via side-effecting imports
# (see routers/admin.py, which imports each collector module for this reason).
REGISTRY: dict[str, CollectFn] = {}

MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 1.0


async def _run_with_retry(
    collect_fn: CollectFn, session: AsyncSession, target_date: dt.date
) -> int:
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return await collect_fn(session, target_date)
        except Exception as e:  # noqa: BLE001 - deliberately broad, retried below
            last_exc = e
            await session.rollback()
            if attempt == MAX_RETRIES:
                break
            backoff = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "job attempt %d/%d failed: %s — retrying in %.1fs",
                attempt,
                MAX_RETRIES,
                e,
                backoff,
            )
            await asyncio.sleep(backoff)
    assert last_exc is not None
    raise last_exc


async def _upsert_log(
    session: AsyncSession,
    job_name: str,
    target_date: dt.date,
    status: str,
    rows: int | None,
    message: str | None,
) -> None:
    stmt = pg_insert(CollectLog).values(
        job=job_name,
        target_date=target_date,
        status=status,
        rows=rows,
        message=message,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[CollectLog.job, CollectLog.target_date],
        set_={
            "status": stmt.excluded.status,
            "rows": stmt.excluded.rows,
            "message": stmt.excluded.message,
            "ran_at": func.now(),
        },
    )
    await session.execute(stmt)


async def run_job(job_name: str, target_date: dt.date, collect_fn: CollectFn) -> dict:
    """Run collect_fn with retry, upsert the outcome into collect_log, return a summary."""
    async with async_session_factory() as session:
        try:
            result = await _run_with_retry(collect_fn, session, target_date)
            rows, message = result if isinstance(result, tuple) else (result, None)
            await _upsert_log(session, job_name, target_date, "ok", rows, message)
            await session.commit()
            if message:
                logger.info("job %s ok: %d rows (%s) — %s", job_name, rows, target_date, message)
            else:
                logger.info("job %s ok: %d rows (%s)", job_name, rows, target_date)
            summary = {
                "job": job_name,
                "target_date": target_date.isoformat(),
                "status": "ok",
                "rows": rows,
            }
            if message:
                summary["message"] = message
            return summary
        except Exception as e:  # noqa: BLE001 - final failure after retries exhausted
            await session.rollback()
            message = str(e)[:500]
            await _upsert_log(session, job_name, target_date, "fail", None, message)
            await session.commit()
            logger.error("job %s failed: %s", job_name, message)
            return {
                "job": job_name,
                "target_date": target_date.isoformat(),
                "status": "fail",
                "message": message,
            }


async def run_all(target_date: dt.date) -> list[dict]:
    """Run every job currently in REGISTRY sequentially (used by the scheduler)."""
    results = []
    for job_name, collect_fn in REGISTRY.items():
        results.append(await run_job(job_name, target_date, collect_fn))
    return results
