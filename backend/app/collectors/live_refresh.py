"""서버 측 능동 갱신 스케줄러 — 세 개의 독립 인터벌 잡을 돌린다.

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
   **2026-08-03 추가(PLAN.md §5.52)**: 바로 이어서 `collectors/positioning_
   snapshot.track_positioning_snapshot`도 호출해 §5.50 포지셔닝 프레임(하이닉스
   중심)의 하루 1회 사후 검증 스냅샷을 DB(positioning_snapshot 테이블)에
   남긴다 — 마찬가지로 새 외부 호출 없이 이미 워밍된 §5.50/§5.15 warm 함수만
   재사용한다(collectors/positioning_snapshot.py 모듈 docstring 참고).
   **2026-08-04 추가(PLAN.md §5.54)**: 바로 이어서 `collectors/auto_trader.
   run_auto_trade`도 호출한다 — 이 프로젝트 최초의 완전자동매매 실행 엔진
   (0167A0 트레일링 스탑). 킬스위치(`AutoTradeState.enabled`) 기본값이 False라
   (마이그레이션 시드) 사용자가 전용 탭에서 켜기 전까지는 이 호출이 매
   폴링마다 즉시 반환되기만 하고 `place_buy_order`/`place_sell_order`를 전혀
   호출하지 않는다(collectors/auto_trader.py 모듈 docstring 참고).
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
3. ``stock_flow_scan``(10분, PLAN.md §5.20, 2026-07-23 추가): 종목별 당일
   수급(외국인+기관 순매수) 스크리닝. value-rank/live가 이미 캐시해 둔 후보군
   (코스피+코스닥 거래대금 상위, 최대 200개, ETF 제외 — 위 2번과 동일 소스)
   코드를 순회하며 키움 ka10059(종목별투자자기관별요청, routers/stocks.py가
   종목상세에서 이미 온디맨드로 쓰는 것과 같은 TR)를 종목마다 1콜씩 호출해
   ``stock_flow`` 테이블에 upsert한다(스키마 변경 없음). 시장 전체를 한 번에
   보여주는 "외국인/기관 순매수 상위 랭킹" TR(ka10065/ka90009, 네이버 대안
   포함)은 이미 조사해 장중 실시간 갱신이 안 된다고 결론 났다(routers/
   flow_rank.py 모듈 docstring "flow-rank/live는 만들지 않는다" 절 참고,
   재조사 불필요) — 그래서 랭킹 TR 대신 이미 존재하는 종목별 개별 조회 TR을
   후보군에 대해 순회하는 이 방식을 쓴다. **왜 10분(다른 두 잡보다 훨씬
   느림)인가**: 키움 rate limiter(`clients/kiwoom.py`의 `_bucket`)가 1req/s로
   보수적이고, 이 잡은 종목마다 1콜씩 최대 200콜을 순차로 쳐야 해서 최악
   ~200초(3.3분)가 걸린다 — 60초/7분 티어에 끼워 넣기엔 너무 느려 전용 10분
   티어를 새로 둔다(10분 창 안에 여유 있게 끝남). ``routers/scalp.py``의
   ``_stock_flow_lookup``이 이 테이블을 읽어 ``compute_scalp_scores``의 5번째
   요소(flow)로 스코어에 반영한다(quant/screener.py 모듈 docstring 참고).

`collectors/intraday_snapshot.py`는 위 두 잡이 이미 끝낸 fetch 결과를 그대로
받아 ``intraday_sample`` 테이블에 INSERT만 하는 저장소다(PLAN.md §5.14, 2026-07-22
DB 영속화 — 예전엔 순수 메모리 리스트였으나 재배포마다 소실되는 §5.6 사고 원인이라
DB로 옮겼다) — "오늘 장중 수급 추이" 1D 차트(PLAN.md §5.4-3/4, `GET
/api/markets/flow/intraday-accumulated` 및
`GET /api/markets/foreign-position/intraday-accumulated`)의 데이터 소스가
된다. 이 스케줄러가 없으면(``ENABLE_LIVE_REFRESH`` 꺼짐) 그 적립도 전혀
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
소요라 여유 있다. 10분 잡(stock_flow_scan)은 매 호출마다 키움 ka10059를 후보군
크기만큼(최대 ~200콜) 호출 — 1req/s 리미터 기준 최악 ~200초, 10분(600초) 창
안에 충분히 여유 있다(후보군이 200개보다 적으면 그만큼 더 여유롭다).
"""

from __future__ import annotations

import datetime as dt
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from ..db import async_session_factory
from ..market_hours import KST, is_nxt_closed

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

# 7분 티어 인터벌 — value-rank/live 전용(§5.6 회귀 수정으로 basis/groups/
# futures-flow는 60초 잡으로 이동, 모듈 docstring 참고). routers/flow_rank.py의
# LIVE_TTL_SECONDS(420초)와 반드시 맞춘다.
EXTRA_REFRESH_INTERVAL_SECONDS = 420

# 10분 티어 — 종목별 당일 수급(stock_flow) 스윕 전용(PLAN.md §5.20). 후보군
# 최대 200개를 키움 rate limiter(1req/s) 아래에서 순차 호출하면 최악 ~200초가
# 걸려 60초/7분 티어보다 훨씬 느린 창이 필요하다(모듈 docstring "3." 문단 참고).
STOCK_FLOW_SCAN_INTERVAL_SECONDS = 600

# 30초 티어 — 자동매매 손절 전용 고빈도 감시(PLAN.md §5.54-6, 2026-08-05). 다른
# 티어보다 훨씬 짧은 이유는 collectors/auto_trader.py::watch_stop_loss 모듈
# docstring 참고 — 1분봉 기반 판정(60초 잡)과 달리 캐시 없는 실시간 호가를 써서
# 실제로 더 빨리 반응하게 하는 게 목적이다.
AUTO_TRADE_WATCH_INTERVAL_SECONDS = 30


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
    from . import auto_trader, intraday_snapshot, positioning_snapshot, scalp_tracker

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
                breadth_payload = await markets._warm_breadth_live(session)
                # 2026-07-22(PLAN.md §5.13, §5.14): 방금 fetch한 값을 그대로 장중
                # 등락비율 누적 스냅샷에 적립한다 — 새 외부 호출 없음(flow와 동일한
                # 패턴, 같은 try 블록 안에 둬서 적립 실패가 breadth 워밍 자체의 성공을
                # 되돌리지 않는다). §5.14부터 DB 영속화라 세션을 넘긴다(즉시 자체
                # commit — intraday_snapshot.py 모듈 docstring 참고, 이 세션 블록의
                # 다른 warm 호출들과 커밋 경계가 섞이지 않는다).
                await intraday_snapshot.record_breadth_snapshot(session, breadth_payload)
            except Exception as e:  # noqa: BLE001 - 한 캐시 실패가 나머지 워밍을 막지 않도록
                logger.warning("live-refresh: breadth 워밍/스냅샷 적립 실패: %s", e)

            try:
                flow_payload = await markets._warm_flow_live(session)
                # 2026-07-21(PLAN.md §5.4-2), 2026-07-22(§5.14 DB 영속화): 방금
                # fetch한 값을 그대로 장중 누적 스냅샷에 적립한다 — 새 외부 호출 없음.
                # 같은 try 블록 안에 둬서 적립 실패가 flow 워밍 자체의 성공을 되돌리지
                # 않는다(이미 캐시에는 반영된 뒤이므로 여기서 예외가 나도 무해하게
                # 로깅만 하면 된다).
                await intraday_snapshot.record_flow_snapshot(session, flow_payload)
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
            # §5.14 DB 영속화 — 이 warm 호출 자체는 세션이 필요 없지만(위 주석
            # 참고), 적립은 이제 DB에 쓰므로 여기서 새로 세션을 연다. warm 함수
            # 성공 이후에만 세션을 여는 게 낭비처럼 보일 수 있지만, futures-flow는
            # 7분이 아니라 이 60초 잡 안에서 이미 한 번 도는 잡이라(§5.6 회귀 수정)
            # 매 분 호출 비용이 크지 않다.
            async with async_session_factory() as futures_session:
                await intraday_snapshot.record_futures_flow_snapshot(futures_session, futures_flow_payload)
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

        # PLAN.md §5.52 — §5.50 포지셔닝 프레임 사후 검증 스냅샷(하루 1회 기록 +
        # same_day/next_day 2단계 채우기). 위 scalp-tracker와 완전히 동일한 이유로
        # nxt_closed 분기 밖에서 호출한다 — 새 외부 API 호출이 전혀 없고(이미
        # 워밍된 §5.50/§5.15 warm 함수만 재사용), "NXT 마감 직후 첫 폴링에
        # same_day를 채운다"는 요구가 오히려 이 게이트 밖에서 실행돼야 충족된다
        # (collectors/positioning_snapshot.py 모듈 docstring "스케줄링 배선" 참고).
        try:
            positioning_result = await positioning_snapshot.track_positioning_snapshot(session, now_kst)
            logger.debug("live-refresh: positioning-snapshot %s", positioning_result)
        except Exception as e:  # noqa: BLE001
            logger.warning("live-refresh: positioning-snapshot 실패: %s", e)

        # PLAN.md §5.54 — 완전자동매매 엔진(0167A0 트레일링 스탑) 1회 평가·실행.
        # 이 프로젝트 최초로 실제 돈이 자동으로 움직이는 호출이다. `enabled`
        # 킬스위치 기본값이 False라(마이그레이션 시드) 사용자가 전용 탭에서
        # 명시적으로 켜기 전까지는 이 호출이 매 폴링마다 즉시 반환되기만 하고
        # place_buy_order/place_sell_order를 전혀 호출하지 않는다.
        #
        # **2026-08-07 수정(PLAN.md §5.59)**: 예전엔 위 scalp-tracker/
        # positioning-snapshot과 "같은 이유"로 nxt_closed 게이트 밖에서
        # 불렀는데, 그 둘과 달리 이 함수는 **새 외부 호출이 있다** —
        # `_warm_stock_intraday(code, 1)`가 매번 키움 ka10080(분봉)을 직접
        # 호출한다(이미 이번 폴링에서 워밍된 지수 캐시를 재사용하는 게
        # 아니라 0167A0 전용 별도 조회). 그래서 킬스위치가 켜져 있으면
        # NXT 마감(20:00 KST)~다음날 개장(08:00 KST) 사이에도 60초마다
        # 키움을 계속 두드리고 있었다 — 그 시간대엔 주문도 체결될 수 없어
        # 완전히 낭비였다(사용자 지적으로 재검토 중 발견, `watch_stop_loss`
        # 30초 잡은 처음부터 `is_nxt_closed`로 이미 게이트돼 있었는데 이
        # 60초 잡만 빠져 있었다). `is_nxt_closed`로 감싸 나머지 NXT 연동
        # 소스들과 동일한 기준을 맞춘다 — 이 시간대엔 매매 자체가 불가능하므로
        # 안전성 손실은 없다(손절 등 청산 로직도 §5.55부터 NXT 마감 전
        # 15:20~15:30에 이미 강제 처리되도록 설계돼 있다).
        if not nxt_closed:
            try:
                auto_trade_result = await auto_trader.run_auto_trade(session)
                logger.debug("live-refresh: auto-trader %s", auto_trade_result)
            except Exception as e:  # noqa: BLE001
                logger.warning("live-refresh: auto-trader 실패: %s", e)


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


async def _run_stock_flow_scan() -> None:
    """10분 티어(PLAN.md §5.20) — value-rank/live 후보군(코스피+코스닥 거래대금
    상위, 최대 200개, ETF 제외)을 순회하며 키움 ka10059(종목별투자자기관별요청)로
    오늘 외국인+기관 순매수를 조회해 ``stock_flow``에 upsert한다. 파싱/upsert는
    ``routers/stocks.py``가 종목상세 온디맨드 조회에서 이미 쓰고 검증한
    ``_parse_ka10059_rows``/``_upsert_flow_rows``를 그대로 재사용한다(중복 구현
    금지 — 이 함수는 "누구를, 언제 순회할지"만 새로 정한다).

    개별 종목 시세라 위 ``_run_live_refresh_extra``와 동일하게 NXT 확장세션
    (08:00~20:00, ``is_nxt_closed``)을 잡 레벨 게이트로 쓴다 — attention/
    value-rank와 같은 기준(market_hours.py 모듈 docstring 참고).

    **단일 KiwoomClient 인스턴스 필수(실제 외부 API 안전장치)**: 키움 클라이언트의
    rate limiter(``clients/kiwoom.py``의 ``_bucket``, 1req/s)는 **인스턴스
    속성**이라 종목마다 새 ``KiwoomClient()``를 열면 매번 토큰 버킷이 새로
    리셋돼 사실상 무제한 버스트가 되어 버린다(리미터가 있으나 마나 해짐) —
    실제 키움 서버에 초당 제한을 넘는 버스트를 쳐서 429/차단 리스크가 생긴다.
    그래서 이 스윕 전체를 위해 ``KiwoomClient()`` 인스턴스를 **딱 하나만** 열고
    (아래 ``async with``), 그 안에서 후보군 전체를 순차 호출한다 — 최악
    ~200종목 × 1초 ≈ 200초, 10분(600초) 창 안에 충분히 끝난다.

    종목 하나 조회가 실패해도(일시적 네트워크 오류, 그 종목 데이터 없음 등)
    나머지 종목 스윕을 막지 않는다 — 이 파일의 다른 잡들과 동일한 "부분 실패
    허용" 철학(모듈 docstring 참고), per-code try/except로 처리한다. 스윕
    전체(예: 키움 인증 실패)가 죽는 경우도 바깥 try/except로 흡수해 스케줄러
    자체는 계속 살아있게 한다.
    """
    now_kst = dt.datetime.now(KST)
    if is_nxt_closed(now_kst):
        logger.debug("stock-flow-scan: NXT closed (%s KST), skipping", now_kst.isoformat())
        return

    from ..clients.kiwoom import KiwoomClient
    from ..routers import flow_rank as flow_rank_router
    from ..routers import stocks as stocks_router

    value_payload = await flow_rank_router._warm_value_rank_live()
    codes = [
        row["code"]
        for row in (value_payload.get("rows") or [])
        if row.get("code") and not row.get("is_etf")
    ]
    if not codes:
        logger.debug("stock-flow-scan: 후보군이 비어 있어 건너뜀 (%s KST)", now_kst.isoformat())
        return

    # code에 의존하지 않는 값이라 루프 밖에서 한 번만 계산한다(_ensure_flows_cached와
    # 동일한 컷오프 표현 — routers/stocks.py 참고, 별도로 새로 만들지 않는다).
    target_end = stocks_router._latest_trading_day()
    cutoff = target_end - dt.timedelta(days=stocks_router.FLOW_BACKFILL_DAYS)

    success = 0
    try:
        async with KiwoomClient() as client:
            async with async_session_factory() as session:
                for code in codes:
                    try:
                        data, _headers = await client.stock_investor_daily(code)
                        rows = stocks_router._parse_ka10059_rows(data)
                        rows = [r for r in rows if r["date"] >= cutoff]
                        await stocks_router._upsert_flow_rows(session, code, rows)
                        success += 1
                    except Exception as e:  # noqa: BLE001 - 종목 하나 실패가 나머지를 막지 않도록
                        logger.warning("stock-flow-scan: %s 조회/upsert 실패: %s", code, e)
                await session.commit()
    except Exception as e:  # noqa: BLE001 - 인증 실패 등 스윕 전체 실패는 로깅만 하고 스케줄러를 죽이지 않는다
        logger.warning("stock-flow-scan: 스윕 전체 실패: %s", e)
        return

    logger.info(
        "stock-flow-scan: %d/%d 종목 수급 갱신 완료 at %s KST",
        success,
        len(codes),
        now_kst.isoformat(),
    )


async def _run_auto_trade_watch() -> None:
    """30초 티어(PLAN.md §5.54-6, 2026-08-05) — 자동매매 손절 전용 고빈도 감시.
    `_run_live_refresh`(60초)의 `auto_trader.run_auto_trade`와 별개 잡이다 —
    자세한 이유는 `collectors/auto_trader.py::watch_stop_loss` 모듈 docstring
    참고. 개별 종목 실시간 호가(ka10004)를 쓰므로 `_run_live_refresh_extra`/
    `_run_stock_flow_scan`과 동일하게 NXT 확장세션(08:00~20:00,
    `is_nxt_closed`) 밖에서는 아예 호출하지 않는다 — `watch_stop_loss` 자체도
    킬스위치/상태(holding·trailing)로 스스로 게이트하므로(모듈 docstring
    참고) 이중 안전이다."""
    now_kst = dt.datetime.now(KST)
    if is_nxt_closed(now_kst):
        return

    from . import auto_trader

    async with async_session_factory() as session:
        try:
            result = await auto_trader.watch_stop_loss(session)
            if result.get("action") not in ("none",):
                logger.info("auto-trade-watch(30s): %s", result)
        except Exception as e:  # noqa: BLE001 - 이 잡이 죽어도 60초 run_auto_trade가 손절 안전망으로 남는다
            logger.warning("auto-trade-watch(30s) 실패: %s", e)


async def _run_nasdaq_futures_morning_job() -> None:
    """평일 07:50 KST 1회 — PLAN.md §5.54-7/§5.56(2026-08-05/06 사용자 지적:
    "나스닥 선물은 자주 확인할 필요 없다, NXT 개장(08:00 KST) 10분 전에
    준비되면 되고 한번 처리됐으면 그 이후는 계속 볼 필요 없다"). **이 잡이
    하루 중 `routers/markets.py::_fetch_and_cache_nasdaq_futures_live`(실제
    yfinance 호출)를 부르는 유일한 경로다** — 온디맨드 경로(라우트 핸들러/
    `positioning_snapshot`)는 `_warm_nasdaq_futures_live`(캐시 읽기 전용)만
    쓰고 절대 직접 조회하지 않는다(`routers/markets.py` 모듈 docstring
    "2026-08-06 추가 변경" 절 참고 — TTL만으로는 `--reload` 재시작 직후
    콜드 캐시 상태에서의 온디맨드 조회를 막지 못해, yfinance가 남기는
    고아 서브프로세스가 CPU를 계속 갉아먹는 사고가 실제로 재현됐다).

    **collectors/scheduler.py(18:00/07:30/19:30 cron)가 아니라 여기(60초 잡과
    같은 파일)에 두는 이유**: 이 캐시는 DB가 아니라 이 프로세스(backend,
    `--reload`)의 인메모리 딕셔너리다 — `collectors/scheduler.py`는 별도
    컨테이너(`worker`)에서 도는 완전히 다른 프로세스라, 거기서 이 함수를
    불러도 backend 프로세스의 캐시는 전혀 채워지지 않는다(worker/scheduler.py
    모듈 docstring "왜 backend가 아니라 worker인가" 참고 — 그건 DB에 쓰는
    배치라 프로세스가 어디든 상관없지만, 이건 인메모리 캐시라 반드시 이
    프로세스여야 한다).

    DB에 쓰는 REGISTRY/collect_log 기반 수집기가 아니라 그냥 캐시 워밍이라
    `run_job`을 쓰지 않는다 — 실패해도 이 잡이 스케줄러 전체를 죽이지 않는다.
    **이 잡이 실패하면 그날은 그냥 나스닥 선물 데이터가 없는 채로 남는다**
    (2026-08-06부터 온디맨드 폴백이 없다 — 있으면 하루 1회 제한이 깨지므로
    의도적으로 없앴다, 어차피 참고용 보조 타일이라 하루 결측이 문제되지
    않는다)."""
    from ..routers import markets

    try:
        await markets._fetch_and_cache_nasdaq_futures_live()
        logger.info("nasdaq-futures morning warm(07:50 KST): 완료")
    except Exception as e:  # noqa: BLE001 - 실패해도 스케줄러 자체는 계속 돌아야 한다
        logger.warning("nasdaq-futures morning warm 실패(그날은 데이터 없이 넘어감): %s", e)


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
    scheduler.add_job(
        _run_stock_flow_scan,
        IntervalTrigger(seconds=STOCK_FLOW_SCAN_INTERVAL_SECONDS),
        id="stock_flow_scan",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        # 의도적으로 next_run_time을 지정하지 않는다 — 위 두 잡(60초/7분)은
        # 둘 다 기동 즉시 1회 워밍하지만(각 add_job 주석 참고), 이 잡은 최대
        # ~200초짜리 순회 스윕이라 앱 기동/리로드(--reload 개발 환경 포함)마다
        # 즉시 실행하면 그때마다 키움에 최대 200콜을 몰아 치는 부담이 생긴다 —
        # 첫 자연 tick(600초 뒤)부터 느긋하게 시작한다.
    )
    scheduler.add_job(
        _run_auto_trade_watch,
        IntervalTrigger(seconds=AUTO_TRADE_WATCH_INTERVAL_SECONDS),
        id="auto_trade_watch",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        # 킬스위치 기본 OFF + watch_stop_loss 자체가 즉시 반환하므로(모듈
        # docstring 참고) 기동 즉시 실행해도 무해하다 — 위 60초/7분 잡과
        # 동일하게 첫 tick을 기다리지 않는다.
        next_run_time=dt.datetime.now(),
    )
    scheduler.add_job(
        _run_nasdaq_futures_morning_job,
        CronTrigger(day_of_week="mon-fri", hour=7, minute=50, timezone="Asia/Seoul"),
        id="nasdaq_futures_morning",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        # 의도적으로 next_run_time을 지정하지 않는다 — 위 stock_flow_scan과
        # 동일한 이유: 기동/리로드(--reload 개발 환경)마다 즉시 실행되면
        # "하루 한 번"이라는 목적 자체가 무너진다(리로드가 잦은 개발 중에는
        # 하루에도 여러 번 재실행될 것). 07:50 정시 tick만 기다린다.
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "live-refresh scheduler started: 60s + %ds + %ds + %ds interval, weekday 09:00-15:30 KST only",
        EXTRA_REFRESH_INTERVAL_SECONDS,
        STOCK_FLOW_SCAN_INTERVAL_SECONDS,
        AUTO_TRADE_WATCH_INTERVAL_SECONDS,
    )
    return scheduler


def shutdown_live_refresh_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("live-refresh scheduler stopped")
