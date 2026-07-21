"""서버 측 능동 갱신 스케줄러 — 두 개의 독립 인터벌 잡을 돌린다.

1. ``live_refresh``(60초): routers/markets.py의 breadth/live, flow/live,
   attention, index-tiles/live(2026-07-21 추가 — 대시보드 지수 3종 타일),
   fx/live(2026-07-21 추가, PLAN.md §5.5-3 — USD/KRW 환율, 실측으로 장중 고시회차
   갱신을 확인해 편입), basis/live·groups/live(업종+테마)·futures-flow/live
   (2026-07-21 §5.6 회귀 수정으로 이 잡에 합류 — 아래 "§5.6 회귀" 문단 참고)
   8개 라이브 캐시를 요청 없이도 선제적으로 채운다.
   **2026-07-21 추가(PLAN.md §5.4-2)**:
   flow/live를 워밍한 직후, 그 반환값을 그대로 `collectors/intraday_snapshot.
   record_flow_snapshot`에 넘겨 개인/외국인/기관계 순매수를 그날의 장중 누적
   스냅샷 버퍼에 append한다 — 새 외부 호출은 없다(이미 fetch한 값 재사용),
   실패해도 flow 워밍 자체의 try/except 안에 있어 캐시 워밍을 막지 않는다.
   futures-flow/live도 같은 이유로 워밍 직후
   `collectors/intraday_snapshot.record_futures_flow_snapshot`에 적립한다.
   **2026-07-21 추가(PLAN.md §5.7)**: 위 워밍(또는 NXT 마감으로 워밍 스킵)이
   끝난 뒤 항상 `collectors/scalp_tracker.track_scalp_picks`를 호출해 스켈핑
   후보 추적 기록(신규 진입 + 호라이즌/EOD change_rate 채우기)을 DB(scalp_pick
   테이블)에 남긴다 — 새 외부 호출 없이 이미 워밍된 attention/value-rank
   캐시만 재사용한다(collectors/scalp_tracker.py 모듈 docstring 참고).
2. ``live_refresh_extra``(7분, PLAN.md §4.7 3단 갱신 주기): value-rank/live
   1개만 채운다 — 코스피+코스닥 전 종목 페이지네이션(~44콜, 15~30초 소요)이라
   진짜로 비싼 유일한 소스다(수급 상위/flow-rank는 장중 실측 결과 소스 자체가
   2영업일 이상 지연돼 있어 제외 — routers/flow_rank.py 모듈 docstring
   "flow-rank/live는 만들지 않는다" 절 참고).

   **§5.6 회귀(2026-07-21)**: 원래 이 7분 잡에 basis/groups/futures-flow도
   같이 있었다. §5.5-2에서 "이 셋은 단일/가벼운 호출이라 1분으로 당겨도 비용이
   안 는다"고 판단해 **프런트 폴링 주기만** 1분 티어로 옮겼는데, 그 판단을
   실제로 반영하려면 여기(스케줄러 잡 배정)와 각 라우터의 TTL 상수도 같이
   옮겼어야 했다 — 둘 다 빠뜨려 백엔드는 계속 7분에 한 번만 실제로 새로 조회하고
   있었다(프런트만 60초마다 헛요청). 사용자가 "업종·테마 강약이 갱신 안 된다"고
   재차 지적해 90초 간격 재호출로 byte-for-byte 동일 응답을 실측 확인, 그제서야
   발견했다. 지금 이 셋을 실제로 60초 잡으로 옮기고 각 TTL도 60초로 맞춘다.

`collectors/intraday_snapshot.py`는 위 두 잡이 이미 끝낸 fetch 결과를 그대로
받아 메모리 리스트에 적립만 하는 순수 저장소다 — "오늘 장중 수급 추이" 1D
차트(PLAN.md §5.4-3/4, `GET /api/markets/flow/intraday-accumulated` 및
`GET /api/markets/foreign-position/intraday-accumulated`)의 데이터 소스가
된다. 이 스케줄러가 없으면(``ENABLE_LIVE_REFRESH`` 꺼짐) 그 버퍼도 전혀
쌓이지 않는다 — 라우트 핸들러 쪽 온디맨드 호출은 warm 함수만 부르고
intraday_snapshot 기록은 하지 않으므로(routers/markets.py 참고), 1D 누적은
전적으로 이 스케줄러가 살아있어야 동작하는 기능이다.

기존 60초 메모리 캐시(routers/markets.py)는 "요청이 들어와야 갱신"하는 수동적
캐시였다 — 아무도 요청하지 않으면 대시보드 값이 마지막 요청 시점에 멈춰 있었다.
이 모듈은 그 캐시를 채우는 warm 함수(routers.markets._warm_breadth_live /
_warm_flow_live / _warm_attention / _warm_index_tiles_live / _warm_fx_live /
_warm_futures_flow_live, routers.basis._warm_basis_live,
routers.groups._warm_groups_live — 이상 7개는 60초 잡, routers.flow_rank.
_warm_value_rank_live 1개만 7분 잡)를 IntervalTrigger로 순차 호출해, 프런트가
폴링하기 전에 이미 캐시가 신선하도록 만든다. 캐시 딕셔너리·TTL·락은 각 라우터
모듈 전역 그대로라 HTTP 요청 경로와 이 스케줄러 경로가 안전하게 캐시를 공유한다.

``ENABLE_SCHEDULER``(collectors/scheduler.py, 평일 18:00 일별 배치)와는 독립적인
토글이다 — main.py의 lifespan이 ``ENABLE_LIVE_REFRESH=1``일 때만 이 스케줄러를
켠다. 둘 다 켜도 무해하다(서로 다른 캐시/테이블을 건드림).

장중에만 실제로 소스를 호출한다 — 장 마감/주말에 불필요한 키움·네이버 API 호출을
막기 위해서다. **2026-07-21(NXT) 수정**: "장중"이 더 이상 단일 창이 아니다 —
지수/집계 통계(breadth/flow/index-tiles/fx/basis/groups/futures-flow)는 KRX
정규장(평일 09:00~15:30 KST, ``market_hours.is_market_closed``)에서 그대로
고정되지만, 개별 종목 시세(attention·value-rank)는 NXT 확장세션(08:00~20:00,
``market_hours.is_nxt_closed``)까지 계속 움직인다(실측 근거는
market_hours.py 모듈 docstring 참고). 두 잡의 **잡 레벨** 게이트는 더 넓은
NXT 창을 써서 15:30~20:00에도 잡 자체는 계속 돌게 하고, 정규장 전용 소스는
각자 내부에서 다시 좁은 창을 확인해 스스로 건너뛴다. **이 스케줄러 잡 레벨
게이트와 별개로, 각 warm 함수 자체도 내부에서 다시 (자신에게 맞는) 장 마감을
확인해 외부 API 호출을 막는다**(2026-07-20 버그 수정 — 예전에는 routers.markets의
`GET /api/markets/flow/live` 라우트가 이 스케줄러를 거치지 않고 직접 호출돼도
게이트가 없어, 새벽에 프런트 탭을 열어 둔 채로 폴링하면 계속 키움/네이버를
두드리는 낭비/리스크가 있었다 — 지금은 warm 함수 자체가 이중으로 막는다).

호출 예산: 60초 잡은 매 호출마다 키움 2콜(ka10051 flow) + 1콜(ka00198 attention) +
2콜(ka20005 지수분봉 kospi/kosdaq, index-tiles가 재사용) + 네이버 4콜(breadth
kospi/kosdaq + index-tiles 선물 fchart + fx 환율 1콜) + basis 2콜 + groups 2콜 +
futures-flow 1콜 = 매분 네이버 9콜/키움 3콜 — KiwoomClient 자체 리미터(1req/s)가
있어 문제없고 네이버 쪽도 단일/소수 요청뿐이라 여유 있다. 7분 잡은 매 호출마다
네이버 ~44콜(value-rank 코스피+코스닥 전량 페이지네이션) — 7분 창 안에 15~30초
소요라 여유 있다.
"""

from __future__ import annotations

import datetime as dt
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..db import async_session_factory
from ..market_hours import KST, is_nxt_closed

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

# 7분 티어 인터벌 — value-rank/live 전용(§5.6 회귀 수정으로 basis/groups/
# futures-flow는 60초 잡으로 이동, 모듈 docstring 참고). routers/flow_rank.py의
# LIVE_TTL_SECONDS(420초)와 반드시 맞춘다.
EXTRA_REFRESH_INTERVAL_SECONDS = 420


async def _run_live_refresh() -> None:
    # 2026-07-21(NXT) — 이 잡은 attention(개별 종목, NXT 08:00~20:00 필요)과
    # breadth/flow/index-tiles/fx(지수·집계, KRX 정규장 09:00~15:30) 워밍이 섞여
    # 있다. 잡 레벨 게이트는 더 넓은 쪽(NXT)을 써서 15:30~20:00에도 잡 자체는
    # 계속 돌게 하고, 정규장 전용 소스는 각자 내부의 is_market_closed로 알아서
    # 스스로 건너뛴다(모듈 docstring "장 마감 게이트" 문단 참고). market_hours.py
    # 모듈 docstring도 참고.
    now_kst = dt.datetime.now(KST)
    nxt_closed = is_nxt_closed(now_kst)

    # 지연 임포트 — routers.markets는 FastAPI 라우터 모듈이라 main.py의 다른 라우터들과
    # 함께 임포트 순서에 얽히기 쉽다. 이 모듈은 collectors 패키지라 main.py의 lifespan이
    # 스케줄러를 켤 때(앱이 이미 완전히 초기화된 뒤)만 routers.markets를 끌어오도록
    # 함수 내부에서 임포트한다(collectors/scheduler.py는 이런 사정이 없어 최상단 임포트).
    from ..routers import basis as basis_router
    from ..routers import groups as groups_router
    from ..routers import markets
    from . import intraday_snapshot, scalp_tracker

    if nxt_closed:
        logger.debug(
            "live-refresh: NXT closed (%s KST), skipping external warms (scalp-tracker 제외)",
            now_kst.isoformat(),
        )
    else:
        async with async_session_factory() as session:
            # breadth도 2026-07-20부터 장 마감 시 DB 폴백을 위해 세션이 필요해졌다(버그
            # 수정 — routers/markets.py `_warm_breadth_live` docstring 참고) — flow/attention과
            # 같은 세션 블록 안으로 옮겼다(예전엔 세션 없이 별도로 호출했다).
            try:
                await markets._warm_breadth_live(session)
            except Exception as e:  # noqa: BLE001 - 한 캐시 실패가 나머지 워밍을 막지 않도록
                logger.warning("live-refresh: breadth 워밍 실패: %s", e)

            try:
                flow_payload = await markets._warm_flow_live(session)
                # 2026-07-21(PLAN.md §5.4-2): 방금 fetch한 값을 그대로 장중 누적
                # 스냅샷 버퍼에 적립한다 — 새 외부 호출 없음. 같은 try 블록 안에 둬서
                # 적립 실패가 flow 워밍 자체의 성공을 되돌리지 않는다(이미 캐시에는
                # 반영된 뒤이므로 여기서 예외가 나도 무해하게 로깅만 하면 된다).
                intraday_snapshot.record_flow_snapshot(flow_payload)
            except Exception as e:  # noqa: BLE001
                logger.warning("live-refresh: flow 워밍/스냅샷 적립 실패: %s", e)

            try:
                await markets._warm_attention(session)
            except Exception as e:  # noqa: BLE001
                logger.warning("live-refresh: attention 워밍 실패: %s", e)

            try:
                await markets._warm_index_tiles_live(session)
            except Exception as e:  # noqa: BLE001
                logger.warning("live-refresh: index-tiles 워밍 실패: %s", e)

            try:
                await markets._warm_fx_live(session)
            except Exception as e:  # noqa: BLE001
                logger.warning("live-refresh: fx 워밍 실패: %s", e)

        # §5.6 회귀 수정으로 7분 잡에서 옮겨왔다 — DB 세션이 필요 없는 3개라
        # 위 session 블록 밖에서 호출한다(basis/groups/futures-flow 모두 세션 미사용).
        try:
            await basis_router._warm_basis_live()
        except Exception as e:  # noqa: BLE001
            logger.warning("live-refresh: basis 워밍 실패: %s", e)

        for group_type in ("upjong", "theme"):
            try:
                await groups_router._warm_groups_live(group_type)
            except Exception as e:  # noqa: BLE001
                logger.warning("live-refresh: groups(%s) 워밍 실패: %s", group_type, e)

        try:
            futures_flow_payload = await markets._warm_futures_flow_live()
            intraday_snapshot.record_futures_flow_snapshot(futures_flow_payload)
        except Exception as e:  # noqa: BLE001
            logger.warning("live-refresh: futures-flow 워밍/스냅샷 적립 실패: %s", e)

        logger.info("live-refresh: cache warmed at %s KST", now_kst.isoformat())

    # PLAN.md §5.7 — 스켈핑 후보 추적 기록(신규 진입 기록 + 호라이즌/EOD 채우기).
    # 위 nxt_closed 분기 **밖에서** 호출한다 — 이 함수는 새 외부 API 호출이
    # 전혀 없어(이미 워밍된 attention/value-rank 캐시만 재사용) 마감 중에
    # 호출해도 비용이 없고, "당일 마감 이후 첫 폴링"에 EOD를 채우려면 오히려
    # 마감 게이트 밖에서 실행돼야 한다(collectors/scalp_tracker.py 모듈 docstring
    # "스케줄링 배선" 참고). 이 잡의 다른 try/except들과 동일한 패턴 — 실패해도
    # 나머지를 막지 않는다(이미 위에서 다 끝난 뒤라 막을 "나머지"도 없지만
    # 일관성을 위해 유지).
    async with async_session_factory() as session:
        try:
            tracker_result = await scalp_tracker.track_scalp_picks(session, now_kst)
            logger.debug("live-refresh: scalp-tracker %s", tracker_result)
        except Exception as e:  # noqa: BLE001
            logger.warning("live-refresh: scalp-tracker 실패: %s", e)


async def _run_live_refresh_extra() -> None:
    """7분 티어(PLAN.md §4.7-2) — value-rank/live 1개 캐시만 선제적으로 채운다
    (§5.6 회귀 수정으로 basis/groups/futures-flow는 위 60초 잡으로 옮겼다 —
    모듈 docstring "§5.6 회귀" 문단 참고). value-rank는 개별 종목 거래대금
    목록이라 2026-07-21(NXT)부터 잡 레벨 게이트도 ``is_nxt_closed``(NXT
    확장세션 08:00~20:00)를 쓴다 — market_hours.py 모듈 docstring 참고. 그
    시간대까지도 마감이면 아예 아무 것도 호출하지 않는다 — warm 함수도 내부에서
    다시 확인하므로(모듈 docstring 참고) 이 잡이 죽어 있어도 라우트 핸들러
    쪽에서 이중으로 안전하다."""
    now_kst = dt.datetime.now(KST)
    if is_nxt_closed(now_kst):
        logger.debug("live-refresh-extra: NXT closed (%s KST), skipping", now_kst.isoformat())
        return

    from ..routers import flow_rank as flow_rank_router

    try:
        await flow_rank_router._warm_value_rank_live()
    except Exception as e:  # noqa: BLE001
        logger.warning("live-refresh-extra: value-rank 워밍 실패: %s", e)

    logger.info("live-refresh-extra: 7분 캐시 warmed at %s KST", now_kst.isoformat())


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
