"""APScheduler AsyncIOScheduler — 평일 18:00 Asia/Seoul에 REGISTRY의 전 잡 실행.

기본적으로 꺼져 있다. main.py의 lifespan이 ``ENABLE_SCHEDULER=1`` 환경변수가 설정된
경우에만 ``start_scheduler()``를 호출한다 (개발 중 의도치 않은 배치/외부 API 호출을
막기 위함, PLAN.md §5.1).

**2026-07-22 심각한 버그 수정(misfire_grace_time)**: 사용자가 "개인 방향성(파생ETF)
차트가 이틀치(07-15/07-20)뿐이라 이상하다"고 지적해 추적하다가, 지난 일주일 중
평일 18:00 배치가 **실제로 실행된 날이 거의 없다**는 걸 발견했다 — collect_log를
전수 조회하니 07-17·07-21 두 날짜만 전체 11개 잡이 다 찍혀 있었는데, 그 두 번 다
타임스탬프가 18:00대가 아니라 오후 3시대(사람이 수동으로 `_run_all_jobs`를
직접 실행한 시각, §5.6-4 참고)였다. 정작 정규 스케줄이 실제로 발동한 유일한
증거(worker 로그, 07-21 18:03:56 KST)는 "Run time of job ... was missed by
0:03:56" 경고만 남기고 **``_run_all_jobs`` 자체가 호출된 흔적(로그 첫 줄인
"scheduled batch starting..." INFO조차)이 전혀 없었다** — 즉 트리거는 맞았는데
실행은 스킵됐다.

원인: 이 ``add_job``에 ``misfire_grace_time``을 지정하지 않았다 — APScheduler
기본값은 매우 짧아서(사실상 초 단위), 이벤트 루프가 정확히 그 순간 한가하지
않으면(도커 컨테이너 CPU 경합 등으로 몇 분만 늦어도) "너무 늦었다"고 판단해
**아예 실행하지 않고 조용히 건너뛴다** — 예외도, 로그도 없이. 결과: 여러
날의 EOD 배치(수급 상위/거래대금 상위/ETF/등락종목수/지수일봉/매크로 등
11개 전부)가 통째로 비어 있었고, 이번 세션 내내 반복된 "어제/그제 걸로
멈춰있다" 지적들(§5.6-4 등) 중 다수가 사실 이 버그가 근본 원인이었다.

수정: ``misfire_grace_time``을 넉넉히(1시간) 줘서 이벤트 루프가 잠깐 늦어도
"1시간 이내"면 그냥 늦게라도 실행하게 한다 — 하루 한 번뿐인 배치라 정시가
아니라 "그날 안에 한 번은 확실히 도는 것"이 훨씬 중요하다."""

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
        # 2026-07-22 버그 수정(모듈 docstring 참고) — 기본값(사실상 초 단위)이면
        # 이벤트 루프가 몇 분만 늦어도 그날 배치를 통째로 조용히 건너뛴다. 하루
        # 1회뿐인 배치라 "늦게라도 반드시 돈다"가 "정시"보다 훨씬 중요하다.
        misfire_grace_time=3600,
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
