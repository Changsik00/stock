"""서버 측 능동 갱신 스케줄러 — 두 개의 독립 인터벌 잡을 돌린다.

1. ``live_refresh``(60초, 기존): routers/markets.py의 breadth/live, flow/live,
   attention, index-tiles/live(2026-07-21 추가 — 대시보드 지수 3종 타일) 4개 라이브
   캐시를 요청 없이도 선제적으로 채운다.
2. ``live_refresh_extra``(7분, PLAN.md §4.7 3단 갱신 주기 신규): value-rank/live·
   basis/live·groups/live(업종+테마)·futures-flow/live 4개 5~10분 캐시를 채운다
   (수급 상위/flow-rank는 장중 실측 결과 소스 자체가 2영업일 이상 지연돼 있어
   제외 — routers/flow_rank.py 모듈 docstring "flow-rank/live는 만들지 않는다"
   절 참고).

기존 60초 메모리 캐시(routers/markets.py)는 "요청이 들어와야 갱신"하는 수동적
캐시였다 — 아무도 요청하지 않으면 대시보드 값이 마지막 요청 시점에 멈춰 있었다.
이 모듈은 그 캐시를 채우는 warm 함수(routers.markets._warm_breadth_live /
_warm_flow_live / _warm_attention, 그리고 신규 5~10분 티어의
routers.flow_rank._warm_value_rank_live / routers.basis._warm_basis_live /
routers.groups._warm_groups_live / routers.markets._warm_futures_flow_live)를
IntervalTrigger로 순차 호출해, 프런트가 폴링하기 전에 이미 캐시가 신선하도록
만든다. 캐시 딕셔너리·TTL·락은 각 라우터 모듈 전역 그대로라 HTTP 요청 경로와 이
스케줄러 경로가 안전하게 캐시를 공유한다.

``ENABLE_SCHEDULER``(collectors/scheduler.py, 평일 18:00 일별 배치)와는 독립적인
토글이다 — main.py의 lifespan이 ``ENABLE_LIVE_REFRESH=1``일 때만 이 스케줄러를
켠다. 둘 다 켜도 무해하다(서로 다른 캐시/테이블을 건드림).

장중(평일 09:00~15:30 KST)에만 실제로 소스를 호출한다 — 장 마감/주말에 불필요한
키움·네이버 API 호출을 막기 위해서다(market_hours.is_market_closed 재사용,
routers/markets.py의 옛 ``_market_closed_kst``와 동일 로직을 공유 위치로 추출한
것 — PLAN.md 서버 측 능동 60초 갱신 작업, 2026-07-20). **이 스케줄러 잡 레벨
게이트와 별개로, 각 warm 함수 자체도 내부에서 다시 market_closed를 확인해
외부 API 호출을 막는다**(2026-07-20 버그 수정 — 예전에는 routers.markets의
`GET /api/markets/flow/live` 라우트가 이 스케줄러를 거치지 않고 직접 호출돼도
게이트가 없어, 새벽에 프런트 탭을 열어 둔 채로 폴링하면 계속 키움/네이버를
두드리는 낭비/리스크가 있었다 — 지금은 warm 함수 자체가 이중으로 막는다).

호출 예산: 60초 잡은 매 호출마다 키움 2콜(ka10051 flow) + 1콜(ka00198 attention) +
2콜(ka20005 지수분봉 kospi/kosdaq, index-tiles가 재사용) + 네이버 3콜(breadth
kospi/kosdaq + index-tiles 선물 fchart) — KiwoomClient 자체 리미터(1req/s)가 있어
문제없다. 7분 잡은 매 호출마다 네이버 ~50콜(value-rank 전량 순회 44콜 내외 +
basis 2콜 + groups 2콜 + futures-flow 1콜) — 7분 창 안에 15~30초 소요라 여유
있다.
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

# 5~10분 티어 인터벌 — PLAN.md §4.7 실측(2026-07-20, basis/futures-flow/groups/
# value-rank 모두 7분 이내에 값 변화 확인) 근거로 7분을 채택. routers/basis.py·
# routers/groups.py·routers/flow_rank.py·routers/markets.py의 각 LIVE_TTL_SECONDS/
# _FUTURES_FLOW_LIVE_TTL_SECONDS(420초)와 반드시 맞춘다.
EXTRA_REFRESH_INTERVAL_SECONDS = 420


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

    async with async_session_factory() as session:
        # breadth도 2026-07-20부터 장 마감 시 DB 폴백을 위해 세션이 필요해졌다(버그
        # 수정 — routers/markets.py `_warm_breadth_live` docstring 참고) — flow/attention과
        # 같은 세션 블록 안으로 옮겼다(예전엔 세션 없이 별도로 호출했다).
        try:
            await markets._warm_breadth_live(session)
        except Exception as e:  # noqa: BLE001 - 한 캐시 실패가 나머지 워밍을 막지 않도록
            logger.warning("live-refresh: breadth 워밍 실패: %s", e)

        try:
            await markets._warm_flow_live(session)
        except Exception as e:  # noqa: BLE001
            logger.warning("live-refresh: flow 워밍 실패: %s", e)

        try:
            await markets._warm_attention(session)
        except Exception as e:  # noqa: BLE001
            logger.warning("live-refresh: attention 워밍 실패: %s", e)

        try:
            await markets._warm_index_tiles_live(session)
        except Exception as e:  # noqa: BLE001
            logger.warning("live-refresh: index-tiles 워밍 실패: %s", e)

    logger.info("live-refresh: cache warmed at %s KST", now_kst.isoformat())


async def _run_live_refresh_extra() -> None:
    """5~10분 티어(PLAN.md §4.7-2) — value-rank/live·basis/live·groups/live(업종+
    테마)·futures-flow/live 4개 캐시를 선제적으로 채운다. 장 마감이면(잡 레벨
    게이트) 아예 아무 것도 호출하지 않는다 — 각 warm 함수도 내부에서 다시
    market_closed를 확인하므로(모듈 docstring 참고) 이 잡이 죽어 있어도 라우트
    핸들러 쪽에서 이중으로 안전하다."""
    now_kst = dt.datetime.now(KST)
    if is_market_closed(now_kst):
        logger.debug("live-refresh-extra: market closed (%s KST), skipping", now_kst.isoformat())
        return

    from ..routers import basis as basis_router
    from ..routers import flow_rank as flow_rank_router
    from ..routers import groups as groups_router
    from ..routers import markets

    try:
        await flow_rank_router._warm_value_rank_live()
    except Exception as e:  # noqa: BLE001
        logger.warning("live-refresh-extra: value-rank 워밍 실패: %s", e)

    try:
        await basis_router._warm_basis_live()
    except Exception as e:  # noqa: BLE001
        logger.warning("live-refresh-extra: basis 워밍 실패: %s", e)

    for group_type in ("upjong", "theme"):
        try:
            await groups_router._warm_groups_live(group_type)
        except Exception as e:  # noqa: BLE001
            logger.warning("live-refresh-extra: groups(%s) 워밍 실패: %s", group_type, e)

    try:
        await markets._warm_futures_flow_live()
    except Exception as e:  # noqa: BLE001
        logger.warning("live-refresh-extra: futures-flow 워밍 실패: %s", e)

    logger.info("live-refresh-extra: 5~10분 캐시 warmed at %s KST", now_kst.isoformat())


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
    scheduler.add_job(
        _run_live_refresh_extra,
        IntervalTrigger(seconds=EXTRA_REFRESH_INTERVAL_SECONDS),
        id="live_refresh_extra",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        # 이 잡도 기동 즉시 한 번 워밍한다(위 60초 잡과 동일한 이유).
        next_run_time=dt.datetime.now(),
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "live-refresh scheduler started: 60s + %ds interval, weekday 09:00-15:30 KST only",
        EXTRA_REFRESH_INTERVAL_SECONDS,
    )
    return scheduler


def shutdown_live_refresh_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("live-refresh scheduler stopped")
