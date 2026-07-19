"""서버 측 능동 60초 갱신 스케줄러 — routers/markets.py의 breadth/live, flow/live,
attention 3개 라이브 캐시를 요청 없이도 선제적으로 채운다.

기존 60초 메모리 캐시(routers/markets.py)는 "요청이 들어와야 갱신"하는 수동적
캐시였다 — 아무도 요청하지 않으면 대시보드 값이 마지막 요청 시점에 멈춰 있었다.
이 모듈은 그 캐시를 채우는 warm 함수(routers.markets._warm_breadth_live /
_warm_flow_live / _warm_attention)를 IntervalTrigger(60s)로 순차 호출해, 프런트가
폴링하기 전에 이미 캐시가 신선하도록 만든다. 캐시 딕셔너리·TTL·락은 routers/markets.py
쪽 모듈 전역 그대로라 HTTP 요청 경로와 이 스케줄러 경로가 안전하게 캐시를 공유한다.

``ENABLE_SCHEDULER``(collectors/scheduler.py, 평일 18:00 일별 배치)와는 독립적인
토글이다 — main.py의 lifespan이 ``ENABLE_LIVE_REFRESH=1``일 때만 이 스케줄러를
켠다. 둘 다 켜도 무해하다(서로 다른 캐시/테이블을 건드림).

장중(평일 09:00~15:30 KST)에만 실제로 소스를 호출한다 — 장 마감/주말에 불필요한
키움·네이버 API 호출을 막기 위해서다(market_hours.is_market_closed 재사용,
routers/markets.py의 옛 ``_market_closed_kst``와 동일 로직을 공유 위치로 추출한
것 — PLAN.md 서버 측 능동 60초 갱신 작업, 2026-07-20).

호출 예산: 매 60초마다 키움 2콜(ka10051 flow) + 1콜(ka00198 attention) + 네이버
2콜(breadth kospi/kosdaq) — KiwoomClient 자체 리미터(1req/s)가 있어 문제없다.
"""

from __future__ import annotations

import datetime as dt
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..db import async_session_factory
from ..market_hours import KST, is_market_closed

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _run_live_refresh() -> None:
    now_kst = dt.datetime.now(KST)
    if is_market_closed(now_kst):
        logger.debug("live-refresh: market closed (%s KST), skipping", now_kst.isoformat())
        return

    # 지연 임포트 — routers.markets는 FastAPI 라우터 모듈이라 main.py의 다른 라우터들과
    # 함께 임포트 순서에 얽히기 쉽다. 이 모듈은 collectors 패키지라 main.py의 lifespan이
    # 스케줄러를 켤 때(앱이 이미 완전히 초기화된 뒤)만 routers.markets를 끌어오도록
    # 함수 내부에서 임포트한다(collectors/scheduler.py는 이런 사정이 없어 최상단 임포트).
    from ..routers import markets

    try:
        await markets._warm_breadth_live()
    except Exception as e:  # noqa: BLE001 - 한 캐시 실패가 나머지 워밍을 막지 않도록
        logger.warning("live-refresh: breadth 워밍 실패: %s", e)

    async with async_session_factory() as session:
        try:
            await markets._warm_flow_live(session)
        except Exception as e:  # noqa: BLE001
            logger.warning("live-refresh: flow 워밍 실패: %s", e)

        try:
            await markets._warm_attention(session)
        except Exception as e:  # noqa: BLE001
            logger.warning("live-refresh: attention 워밍 실패: %s", e)

    logger.info("live-refresh: cache warmed at %s KST", now_kst.isoformat())


def start_live_refresh_scheduler() -> AsyncIOScheduler:
    """Create, start, and return the module-level scheduler (idempotent)."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
    scheduler.add_job(
        _run_live_refresh,
        IntervalTrigger(seconds=60),
        id="live_refresh",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        # 앱 기동 즉시 한 번 워밍한다 — 안 그러면 첫 60초 동안 캐시가 비어 있어
        # 프런트의 첫 폴링이 온디맨드 경로(라우트 핸들러)로 채워질 때까지 기다려야 한다.
        next_run_time=dt.datetime.now(),
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("live-refresh scheduler started: 60s interval, weekday 09:00-15:30 KST only")
    return scheduler


def shutdown_live_refresh_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("live-refresh scheduler stopped")
