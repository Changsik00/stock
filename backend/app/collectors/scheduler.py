"""APScheduler AsyncIOScheduler — 평일 18:00 Asia/Seoul에 REGISTRY의 전 잡 실행.

기본적으로 꺼져 있다. main.py의 lifespan이 ``ENABLE_SCHEDULER=1`` 환경변수가 설정된
경우에만 ``start_scheduler()``를 호출한다 (개발 중 의도치 않은 배치/외부 API 호출을
막기 위함, PLAN.md §5.1).
"""

from __future__ import annotations

import datetime as dt
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .base import REGISTRY, run_job

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _run_all_jobs() -> None:
    target_date = dt.date.today()
    logger.info("scheduled batch starting for %s (%d jobs)", target_date, len(REGISTRY))
    for job_name, collect_fn in REGISTRY.items():
        await run_job(job_name, target_date, collect_fn)


def start_scheduler() -> AsyncIOScheduler:
    """Create, start, and return the module-level scheduler (idempotent)."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
    scheduler.add_job(
        _run_all_jobs,
        CronTrigger(day_of_week="mon-fri", hour=18, minute=0, timezone="Asia/Seoul"),
        id="daily_batch",
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "scheduler started: weekday 18:00 Asia/Seoul daily batch (%d jobs registered: %s)",
        len(REGISTRY),
        sorted(REGISTRY),
    )
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("scheduler stopped")
