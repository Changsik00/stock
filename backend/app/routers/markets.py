"""GET /api/markets/{market}/series — index OHLCV series from the DB (index_ohlcv),
merged with DB-cached investor flow (market_flow, PLAN.md §5.2/§5.3/§6 1-5).

Both series and flows are DB-only reads (PLAN.md §5.4 "DB 캐싱 우선") — the KRX
Open API dataset approval is currently rejected (403 as of 2026-07), so this
router no longer calls it live. index_ohlcv is populated by the daily
collectors/ohlcv.py batch (yfinance/네이버, see services.get_market_series_from_db
for the KRX->DB migration note). The legacy `/api/series?market=` path is kept as
an alias so the existing frontend keeps working until it's migrated — it returns
only the `series` list (no flows) for backward compatibility.

flows now also covers `market=futures`: market_flow stores K200 선물 투자자별
순매수 as `market='k200_futures'` (개인/외국인/기관계, collectors/futures_flow.py,
네이버 m.stock.naver.com 소스 — PLAN.md §4.5 4.5-2). `_build_flows` translates the
"futures" path param to that storage key via `services.DB_MARKET` (same mapping
`_build_prices`/index_ohlcv already uses).

Also owns the market_breadth endpoints (PLAN.md §3.5/§4.6 3.6-2):
- GET /api/markets/{market}/breadth — DB 일별 시계열(collectors/breadth.py가 적재).
- GET /api/markets/breadth/live — 장중 온디맨드. clients/naver_breadth.py를
  직접(DB 경유 없이) 호출하고 60초 메모리 캐시로 감싼다 — §3.5 원칙("장중 값은
  DB에 쌓지 않는다")을 지키기 위해 market_breadth 테이블에는 절대 쓰지 않는다.

Also owns GET /api/markets/flow/live (PLAN.md §6 Phase 3.7-3) — 장중 잠정 투자자별
순매수. breadth/live와 같은 60초 메모리 캐시 패턴이지만 소스가 다르다: 원래
PLAN.md가 가정한 ka10063(장중투자자별매매)은 실호출 검증 결과 종목별 배열이라
시장 합계를 얻으려면 비용이 크다(clients/kiwoom.py 모듈 docstring "ka10063/
ka10066 장중 잠정 수급 probe" 절 참고) — 대신 이미 검증된 ka10051(§6 1-4
일별 배치 소스)을 base_dt=오늘로 재사용한다(collectors/market_flow.py의
fetch_live_flow). 라이브 호출이 실패하면 market_flow DB의 최신 확정치로
폴백한다(provisional=False) — breadth/live와 달리 이 엔드포인트는 그 폴백을
위해 DB 세션이 필요하다.

Also owns GET /api/markets/attention — "실시간 관심 종목 TOP20" 카드. 키움
ka00198(실시간종목조회순위, qry_tp="1"=1분 — 2026-07-21 재실측으로 "4"=당일
누적에서 교체, 근거는 clients/kiwoom.py 모듈 docstring "ka00198 qry_tp
재실측" 절)를 온디맨드로 호출하고 breadth/live·flow/live와 같은 60초 메모리
캐시 패턴으로 감싼다. ka00198
응답에는 market(코스피/코스닥)·ETF 여부 필드가 없어 로컬 `stocks` 테이블과
`stk_cd` 기준으로 조인해서 채운다(순위·종목명·등락율은 TR 응답을 그대로
신뢰). breadth/live와 같은 이유로 **DB에는 절대 쓰지 않는다** — 실시간
성격이라 DB 저장 없음(§3.5 원칙과 동일).

2026-07-20 서버 측 능동 60초 갱신(PLAN.md): 위 3개 캐시는 원래 "요청이 들어와야
갱신"하는 수동적 캐시였다. 각각의 캐시 채우기 로직을 `_warm_breadth_live()`,
`_warm_flow_live(session)`, `_warm_attention(session)`으로 분리해, 이 라우터의
핸들러(HTTP 요청)뿐 아니라 `collectors/live_refresh.py`의 60초 인터벌 잡(장중에만
선제적으로 호출)도 같은 함수를 재사용한다 — 캐시/TTL/락은 그대로 모듈 전역이라
두 호출 경로가 안전하게 캐시를 공유한다.

Also owns GET /api/markets/futures-flow/live (PLAN.md §4.7 3단 갱신 주기, 2026-07-20
장중 실측) — K200 선물 투자자별(개인/외국인/기관계) 순매수를
clients/naver_futures_flow.py(m.stock.naver.com/api/index/FUT/trend)로 온디맨드
재조회한다. 장중 실측 결과 이 소스가 당일 누적치를 체결 진행에 맞춰 갱신함을
확인해 5~10분 캐시로 편입했다(모듈 docstring 상단과 동일한 이유로, EOD 배치
collectors/futures_flow.py는 그대로 하루 1회 market_flow에 확정 적재 — 이
엔드포인트는 DB에 쓰지 않는다, §3.5 원칙). breadth/live와 동일한 warm 함수 +
TTL + Lock 패턴이지만 DB 폴백이 없어(소스가 항상 최신 스냅샷을 주므로) 세션
의존이 없다.

Also owns GET /api/markets/fx/live (PLAN.md §5.5-3, 2026-07-21 장중 실측 편입) —
USD/KRW 환율. clients/naver_fx.py의 ``fetch_usdkrw_naver``(원래 일별 EOD 조회용,
[start, end] 구간을 받는다)를 오늘 하루([오늘, 오늘])로 좁혀 재사용한다. 실측
결과 이 소스의 "오늘" 행은 하루 1회 배치가 아니라 네이버 고시환율의
"고시회차"(장중 여러 번 재고시되는 매매기준율, ``finance.naver.com/marketindex/
exchangeDegreeCountQuote.naver``로 대조 확인)를 그대로 반영해 대략 1~2분
간격으로 갱신된다(60~90초 간격 3회 호출로 값이 실제로 바뀜을 확인) — 그래서
새 엔드포인트를 만들되 **새 소스는 필요 없다**. breadth/live와 동일한 60초
캐시 + 장 마감 게이트 패턴이지만, 장 마감 시 폴백은 macro_series DB의 usdkrw
최신 확정치(collectors/macro.py 일별 배치)를 쓴다 — macro_series 테이블에는
절대 쓰지 않는다(§3.5 원칙).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import naver_breadth, naver_futures_flow, naver_fx, naver_index
from ..clients.kiwoom import MINUTE_CHART_INTERVALS, KiwoomClient, parse_minute_chart_rows
from ..collectors import intraday_snapshot
from ..collectors.market_flow import fetch_live_flow
from ..db import get_session
from ..market_hours import KST, is_market_closed as _market_closed_kst, is_nxt_closed
from ..models import IndexOhlcv, MacroSeries, MarketBreadth, MarketFlow, Stock
from ..quant import regime_backtest
from ..services import DB_MARKET, get_market_series_from_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["markets"])

MARKETS = {"kospi", "kosdaq", "futures"}

# market_flow는 코스피/코스닥(kiwoom ka10051) + 선물(네이버, PLAN.md §4.5 4.5-2,
# collectors/futures_flow.py)까지 3개 다 적재된다. 라우터 경로 파라미터는
# "futures"지만 market_flow에는 "k200_futures"로 저장돼 있으므로(models.py 컨벤션 —
# index_ohlcv와 동일하게 DB_MARKET으로 변환), 아래 _build_flows에서 매핑한다.
FLOW_MARKETS = {"kospi", "kosdaq", "futures"}

# market_breadth도 코스피/코스닥만 있다 (선물은 개별 종목 등락 개념이 없음).
BREADTH_MARKETS = {"kospi", "kosdaq"}

# GET /api/markets/breadth/live 60초 메모리 캐시 — 프로세스 재기동 시 초기화되는
# 단순 캐시로 충분하다(다중 워커 배포는 아직 없음, PLAN.md §5.1). 동시 요청이
# 캐시 미스 때 소스를 중복 호출하지 않도록 asyncio.Lock으로 감싼다.
_LIVE_CACHE_TTL_SECONDS = 60
_live_cache: dict[str, object] = {"ts": 0.0, "data": None}
_live_cache_lock = asyncio.Lock()


async def _build_prices(market: str, days: int, session: AsyncSession) -> dict:
    if market not in MARKETS:
        raise HTTPException(400, f"market must be one of {sorted(MARKETS)}")

    data = await get_market_series_from_db(session, market, days)
    return {"market": market, "days": days, "series": data}


async def _build_flows(market: str, days: int, session: AsyncSession) -> dict[str, list[dict]]:
    """investor -> [{date, net_value, net_volume}, ...], DB에서만 조회 (§5.4 DB 캐싱 우선).

    market_flow가 0행(KRX 로그인 미설정)이면 빈 dict를 반환한다 — 에러 아님.
    """
    if market not in FLOW_MARKETS:
        return {}

    db_market = DB_MARKET.get(market, market)
    since = dt.date.today() - dt.timedelta(days=days)
    stmt = (
        select(MarketFlow)
        .where(MarketFlow.market == db_market, MarketFlow.date >= since)
        .order_by(MarketFlow.investor, MarketFlow.date)
    )
    rows = (await session.execute(stmt)).scalars().all()

    flows: dict[str, list[dict]] = {}
    for r in rows:
        flows.setdefault(r.investor, []).append(
            {
                "date": r.date.isoformat(),
                "net_value": r.net_value,
                "net_volume": r.net_volume,
            }
        )
    return flows


@router.get("/api/markets/{market}/series")
async def market_series(
    market: str,
    days: int = Query(90, ge=1, le=400),
    session: AsyncSession = Depends(get_session),
):
    result = await _build_prices(market, days, session)
    result["prices"] = result.pop("series")
    result["flows"] = await _build_flows(market, days, session)
    return result


@router.get("/api/series")
async def legacy_series(
    market: str = Query(...),
    days: int = Query(90, ge=1, le=400),
    session: AsyncSession = Depends(get_session),
):
    """Deprecated alias for /api/markets/{market}/series — kept for the current frontend.

    Returns only the price series (no flows) for backward compatibility.
    """
    return await _build_prices(market, days, session)


def _serialize_breadth_row(r: MarketBreadth) -> dict:
    return {
        "date": r.date.isoformat(),
        "adv": r.adv,
        "dec": r.dec,
        "flat": r.flat,
        "limit_up": r.limit_up,
        "limit_down": r.limit_down,
    }


@router.get("/api/markets/{market}/breadth")
async def market_breadth_series(
    market: str,
    days: int = Query(90, ge=1, le=400),
    session: AsyncSession = Depends(get_session),
):
    """market_breadth 일별 시계열(collectors/breadth.py가 장마감 후 적재한 확정치,
    DB 전용 읽기 — §5.4 "DB 캐싱 우선"). 장중 실시간 값은 /breadth/live를 쓴다."""
    if market not in BREADTH_MARKETS:
        raise HTTPException(400, f"market must be one of {sorted(BREADTH_MARKETS)}")

    since = dt.date.today() - dt.timedelta(days=days)
    stmt = (
        select(MarketBreadth)
        .where(MarketBreadth.market == market, MarketBreadth.date >= since)
        .order_by(MarketBreadth.date)
    )
    rows = (await session.execute(stmt)).scalars().all()

    return {
        "market": market,
        "days": days,
        "series": [_serialize_breadth_row(r) for r in rows],
    }


def _fetch_breadth_blocking(market: str) -> dict:
    """clients.naver_breadth.fetch_breadth의 블로킹 호출 래퍼 — asyncio.to_thread +
    monkeypatch 대상(collectors/breadth.py의 같은 이름 함수와 동일한 관례)."""
    return naver_breadth.fetch_breadth(market)


async def _fetch_breadth_confirmed_for_market(session: AsyncSession, market: str) -> dict | None:
    """market_breadth DB의 해당 시장 최신 날짜 확정치 — 장 마감 시 라이브 폴백
    (2026-07-20 버그 수정: 장 마감이면 네이버를 아예 호출하지 않고 이 DB 확정치로
    응답한다, `_fetch_flow_confirmed_for_market`과 동일한 패턴)."""
    latest_date = (
        await session.execute(select(func.max(MarketBreadth.date)).where(MarketBreadth.market == market))
    ).scalar_one_or_none()
    if latest_date is None:
        return None
    rows = (
        await session.execute(
            select(MarketBreadth).where(MarketBreadth.market == market, MarketBreadth.date == latest_date)
        )
    ).scalars().all()
    if not rows:
        return None
    r = rows[0]
    return {
        "date": r.date.isoformat(),
        "adv": r.adv,
        "dec": r.dec,
        "flat": r.flat,
        "limit_up": r.limit_up,
        "limit_down": r.limit_down,
    }


async def _warm_breadth_live(session: AsyncSession | None = None) -> dict:
    """breadth/live 캐시를 채우고 payload를 반환한다 — 라우트 핸들러(HTTP 요청)와
    collectors/live_refresh.py의 60초 인터벌 잡이 공유한다(모듈 docstring
    "서버 측 능동 60초 갱신" 절 참고).

    **2026-07-20 버그 수정**: 장 마감이면(``is_market_closed``) 네이버를 아예
    호출하지 않는다 — 예전에는 이 게이트가 없어 새벽에 탭을 열어둔 채로 폴링하면
    계속 네이버를 두드리는 낭비가 있었다. 장 마감 시엔 ``session``이 있으면
    market_breadth DB 확정치로 응답하고(``market_closed: true``), 세션이 없거나
    DB에도 아직 없으면(배치 미실행) 빈 값 + ``market_closed: true``로 응답한다
    (502가 아니다 — "소스 장애"와 "장 마감이라 아직 없음"은 다른 상태)."""
    now = time.monotonic()
    async with _live_cache_lock:
        cached = _live_cache["data"]
        if cached is not None and (now - _live_cache["ts"]) < _LIVE_CACHE_TTL_SECONDS:
            return cached

        now_kst = dt.datetime.now(KST)
        if _market_closed_kst(now_kst):
            result: dict[str, object] = {}
            if session is not None:
                for market in ("kospi", "kosdaq"):
                    result[market] = await _fetch_breadth_confirmed_for_market(session, market)
            payload = {
                "kospi": result.get("kospi"),
                "kosdaq": result.get("kosdaq"),
                "market_closed": True,
                "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            }
            _live_cache["data"] = payload
            _live_cache["ts"] = now
            return payload

        result: dict[str, object] = {}
        for market in ("kospi", "kosdaq"):
            try:
                result[market] = await asyncio.to_thread(_fetch_breadth_blocking, market)
            except Exception as e:  # noqa: BLE001 - 한 시장 실패가 다른 시장을 막지 않도록
                result[market] = None
                result.setdefault("_errors", {})[market] = str(e)[:200]  # type: ignore[union-attr]

        if result.get("kospi") is None and result.get("kosdaq") is None:
            raise HTTPException(502, f"breadth live fetch failed: {result.get('_errors')}")

        payload = {
            "kospi": result.get("kospi"),
            "kosdaq": result.get("kosdaq"),
            "market_closed": False,
            "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        _live_cache["data"] = payload
        _live_cache["ts"] = now
        return payload


@router.get("/api/markets/breadth/live")
async def market_breadth_live(session: AsyncSession = Depends(get_session)):
    """장중 온디맨드 등락 종목수 — 코스피/코스닥을 소스(네이버)에서 직접 조회하고
    60초 메모리 캐시로 감싼다. **market_breadth 테이블에는 절대 쓰지 않는다**
    (§3.5 "장중 값은 DB에 쌓지 않는다" 원칙 — 캐시는 프로세스 메모리에만 존재,
    읽기 전용 DB 폴백은 예외). 실제 캐시 채우기는 `_warm_breadth_live(session)`가
    담당한다(live_refresh 스케줄러와 공유) — 장 마감이면 DB 확정치로 폴백하고
    네이버는 호출하지 않는다(2026-07-20 버그 수정, 함수 docstring 참고).

    Returns ``{"kospi": {...} | None, "kosdaq": {...} | None, "market_closed": bool,
    "cached_at": iso8601}``. 한 시장 조회가 실패하면 그 시장만 None, 다른 시장은
    정상 반환(소스 일시 장애가 전체를 막지 않도록) — 장중에 둘 다 실패하면 502
    (장 마감 폴백 경로는 502를 던지지 않는다).
    """
    return await _warm_breadth_live(session)


# GET /api/markets/flow/live 60초 메모리 캐시 — breadth/live와 동일한 이유
# (프로세스 재기동 시 초기화되는 단순 캐시로 충분, 동시 요청은 asyncio.Lock으로 감쌈).
_FLOW_LIVE_CACHE_TTL_SECONDS = 60
_flow_live_cache: dict[str, object] = {"ts": 0.0, "data": None}
_flow_live_cache_lock = asyncio.Lock()


def _serialize_flow_investors(rows: list[dict]) -> dict[str, dict]:
    return {r["investor"]: {"net_value": r["net_value"], "net_volume": r["net_volume"]} for r in rows}


async def _fetch_flow_live_for_market(client: KiwoomClient, market: str, today_kst: dt.date) -> dict | None:
    """ka10051(sector_investor_net_buy, base_dt=오늘)을 "장중 잠정" 소스로 재사용한다
    — 이유는 clients/kiwoom.py 모듈 docstring "ka10063/ka10066 장중 잠정 수급 probe"
    절 참고. 종합 행을 못 찾으면(휴장 등) None."""
    flows = await fetch_live_flow(client, market, today_kst)
    if not flows:
        return None
    return {
        "date": today_kst.isoformat(),
        "investors": _serialize_flow_investors(flows),
        "provisional": True,
        "source": "kiwoom_live",
    }


async def _fetch_flow_confirmed_for_market(session: AsyncSession, market: str) -> dict | None:
    """market_flow DB의 해당 시장 최신 날짜 확정치 — 라이브 실패 시 폴백."""
    latest_date = (
        await session.execute(select(func.max(MarketFlow.date)).where(MarketFlow.market == market))
    ).scalar_one_or_none()
    if latest_date is None:
        return None
    rows = (
        await session.execute(
            select(MarketFlow).where(MarketFlow.market == market, MarketFlow.date == latest_date)
        )
    ).scalars().all()
    investors = {r.investor: {"net_value": r.net_value, "net_volume": r.net_volume} for r in rows}
    return {"date": latest_date.isoformat(), "investors": investors, "provisional": False, "source": "market_flow_db"}


async def _warm_flow_live(session: AsyncSession) -> dict:
    """flow/live 캐시를 채우고 payload를 반환한다 — 라우트 핸들러와
    collectors/live_refresh.py가 공유한다(모듈 docstring 참고). DB 폴백
    (`_fetch_flow_confirmed_for_market`)에 세션이 필요하므로 호출자가 세션을
    넘긴다 — 라우트는 요청 스코프 세션(Depends(get_session))을, live_refresh는
    자체적으로 연 세션을 전달한다.

    **2026-07-20 버그 수정**: 장 마감이면(``market_closed``) 키움 라이브 호출을
    아예 시도하지 않고 곧바로 DB 확정치 폴백으로 진행한다 — 예전에는
    ``market_closed``를 응답 메타데이터로만 쓰고 실제로는 장 마감 여부와 무관하게
    항상 키움을 먼저 호출했다(새벽에 탭을 열어두면 계속 키움을 두드리는 낭비/리스크).
    장중이면 기존 동작 그대로(회귀 없음)."""
    now = time.monotonic()
    async with _flow_live_cache_lock:
        cached = _flow_live_cache["data"]
        if cached is not None and (now - _flow_live_cache["ts"]) < _FLOW_LIVE_CACHE_TTL_SECONDS:
            return cached

        now_kst = dt.datetime.now(KST)
        today_kst = now_kst.date()
        market_closed = _market_closed_kst(now_kst)

        result: dict[str, dict | None] = {"kospi": None, "kosdaq": None}
        errors: dict[str, str] = {}
        if market_closed:
            logger.debug(
                "market_flow_live: 장 마감(%s KST) — 키움 라이브 호출 생략, DB 폴백으로 진행",
                now_kst.isoformat(),
            )
        else:
            try:
                async with KiwoomClient() as client:
                    for market in ("kospi", "kosdaq"):
                        try:
                            result[market] = await _fetch_flow_live_for_market(client, market, today_kst)
                        except Exception as e:  # noqa: BLE001 - 한 시장 실패가 다른 시장을 막지 않도록
                            errors[market] = str(e)[:200]
            except Exception as e:  # noqa: BLE001 - 클라이언트 생성/토큰 발급 자체 실패(앱키 미설정 등)
                errors["_client"] = str(e)[:200]
                logger.warning("market_flow_live: KiwoomClient 실패, DB 폴백으로 진행: %s", e)

        for market in ("kospi", "kosdaq"):
            if result.get(market) is None:
                result[market] = await _fetch_flow_confirmed_for_market(session, market)

        if result.get("kospi") is None and result.get("kosdaq") is None:
            if market_closed:
                # 장 마감 + DB도 아직 없음(배치 미실행)은 "소스 장애"가 아니라 "아직
                # 없음"이므로 502가 아니다(breadth/live와 동일한 정책, 2026-07-20).
                payload = {
                    "kospi": None,
                    "kosdaq": None,
                    "market_closed": True,
                    "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                }
                _flow_live_cache["data"] = payload
                _flow_live_cache["ts"] = now
                return payload
            raise HTTPException(502, f"market flow live fetch failed: {errors}")

        payload = {
            "kospi": result.get("kospi"),
            "kosdaq": result.get("kosdaq"),
            "market_closed": market_closed,
            "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        _flow_live_cache["data"] = payload
        _flow_live_cache["ts"] = now
        return payload


@router.get("/api/markets/flow/live")
async def market_flow_live(session: AsyncSession = Depends(get_session)):
    """장중 잠정 투자자별 순매수 — PLAN.md §6 Phase 3.7-3.

    코스피/코스닥 각각 ka10051(base_dt=오늘)을 온디맨드로 호출해 60초 메모리
    캐시로 감싼다(모듈 docstring 참고 — ka10063 대신 이 TR을 쓰는 이유). 시장별로
    독립 처리해 한쪽이 실패해도 다른 쪽은 정상 반환하고, 라이브 호출이 실패한
    시장은 market_flow DB의 최신 확정치로 폴백한다(``provisional: false``).
    두 시장 다 라이브·폴백 전부 실패하면 502. **장 마감이면 키움 호출 자체를
    생략하고 곧바로 DB 확정치로 응답한다**(2026-07-20 버그 수정,
    `_warm_flow_live` docstring 참고). 실제 캐시 채우기는
    `_warm_flow_live(session)`가 담당한다(live_refresh 스케줄러와 공유).

    Returns ``{"kospi": {...}|None, "kosdaq": {...}|None, "market_closed": bool,
    "cached_at": iso8601}`` — 각 시장 값은 ``{"date", "investors":
    {투자자명: {net_value, net_volume}}, "provisional", "source"}``.
    """
    return await _warm_flow_live(session)


# GET /api/markets/attention 60초 메모리 캐시 — breadth/live·flow/live와 동일한
# 패턴이지만 각 라이브 엔드포인트는 독립 캐시를 쓴다(모듈 docstring 참고).
_ATTENTION_CACHE_TTL_SECONDS = 60
_attention_cache: dict[str, object] = {"ts": 0.0, "data": None}
_attention_cache_lock = asyncio.Lock()


def _parse_attention_row(row: dict) -> dict | None:
    code = row.get("stk_cd")
    if not code:
        return None
    try:
        rank = int(row.get("bigd_rank"))
    except (TypeError, ValueError):
        rank = None
    try:
        change_rate = float(row.get("base_comp_chgr"))
    except (TypeError, ValueError):
        change_rate = None
    return {
        "rank": rank,
        "code": code,
        "name": row.get("stk_nm") or "",
        "change_rate": change_rate,
    }


# attention은 DB에 절대 저장하지 않는 실시간 전용 지표라(§3.5) market_flow/market_breadth
# 같은 DB 확정치 폴백이 없다. 대신 "마지막 성공 캐시"를 별도로 들고 있다가 장 마감
# 시 그 내용을 재사용한다(2026-07-20 버그 수정) — `_attention_cache`(TTL 캐시)는 장
# 마감 응답도 그대로 캐싱하므로, 장이 다시 열려도 최대 TTL만큼 갱신이 늦어질 수 있는
# 반면, 이 last-good 캐시는 오직 키움 라이브 호출이 실제로 성공했을 때만 갱신된다.
_attention_last_good: dict[str, object] = {"data": None}


async def _warm_attention(session: AsyncSession) -> dict:
    """attention 캐시를 채우고 payload를 반환한다 — 라우트 핸들러와
    collectors/live_refresh.py가 공유한다(모듈 docstring 참고). `stocks` 테이블
    조인에 세션이 필요하므로 호출자가 세션을 넘긴다(flow/live와 동일한 이유).

    **2026-07-20 버그 수정**: 장 마감이면 키움 호출을 아예 시도하지 않는다.
    attention은 DB 저장이 없는 실시간 전용 지표라(§3.5) market_flow/market_breadth
    같은 확정치 폴백이 없으므로, 대신 마지막으로 성공한 라이브 응답(`_attention_last_good`)을
    재사용해 ``market_closed: true``로 표시한다 — 그마저 없으면(기동 직후 장 마감)
    빈 rows + market_closed로 응답한다(502 아님).

    **2026-07-21 추가 수정(NXT)**: 개별 종목 관심순위(ka00198)라 장 마감 판정은
    ``is_market_closed``(KRX 정규장)가 아니라 ``is_nxt_closed``(NXT 확장세션
    08:00~20:00)를 쓴다 — 실측(18:36 KST)으로 정규장 마감 후에도 이 TR이 계속
    갱신됨을 확인했다. market_hours.py 모듈 docstring 참고."""
    now = time.monotonic()
    async with _attention_cache_lock:
        cached = _attention_cache["data"]
        if cached is not None and (now - _attention_cache["ts"]) < _ATTENTION_CACHE_TTL_SECONDS:
            return cached

        now_kst = dt.datetime.now(KST)
        if is_nxt_closed(now_kst):
            last_good = _attention_last_good["data"]
            if last_good is not None:
                payload = {**last_good, "market_closed": True}
            else:
                payload = {
                    "rows": [],
                    "qry_tp": "1",
                    "queried_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "market_closed": True,
                }
            _attention_cache["data"] = payload
            _attention_cache["ts"] = now
            return payload

        try:
            async with KiwoomClient() as client:
                data, _headers = await client.realtime_inquiry_rank(qry_tp="1")
        except Exception as e:  # noqa: BLE001 - 인증/네트워크/API 에러 전부 502로 변환
            raise HTTPException(502, f"attention live fetch failed: {str(e)[:200]}") from e

        parsed = [row for row in (_parse_attention_row(r) for r in data.get("item_inq_rank", [])) if row]

        codes = [row["code"] for row in parsed]
        stock_meta: dict[str, dict] = {}
        if codes:
            stmt = select(Stock.code, Stock.name, Stock.market, Stock.is_etf).where(Stock.code.in_(codes))
            for code, name, market, is_etf in (await session.execute(stmt)).all():
                stock_meta[code] = {"name": name, "market": market, "is_etf": is_etf}

        rows = []
        for row in parsed:
            meta = stock_meta.get(row["code"])
            name = row["name"] or (meta["name"] if meta else "") or row["code"]
            market = meta["market"].lower() if meta and meta.get("market") else None
            is_etf = bool(meta["is_etf"]) if meta else False
            rows.append(
                {
                    "rank": row["rank"],
                    "code": row["code"],
                    "name": name,
                    "change_rate": row["change_rate"],
                    "is_etf": is_etf,
                    "market": market,
                }
            )

        rows.sort(key=lambda r: (r["rank"] is None, r["rank"]))

        payload = {
            "rows": rows,
            "qry_tp": "1",
            "queried_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "market_closed": False,
        }
        _attention_cache["data"] = payload
        _attention_cache["ts"] = now
        _attention_last_good["data"] = payload
        return payload


@router.get("/api/markets/attention")
async def market_attention_live(session: AsyncSession = Depends(get_session)):
    """실시간 관심 종목 TOP20 — 키움 ka00198(qry_tp="1"=1분, 2026-07-21 재실측으로
    "4"=당일 누적에서 교체)을 온디맨드로 호출하고 60초 메모리 캐시로 감싼다.
    **market_attention류 테이블에는 절대 쓰지 않는다** — 실시간 성격이라 DB
    저장 없음(모듈 docstring 참고).

    ka00198 응답에는 market/ETF 여부 필드가 없어 `stocks` 테이블과 `stk_cd`
    기준으로 조인해 채운다. 종목명은 TR의 `stk_nm`을 우선 쓰고(실측상 신뢰
    가능), 비어 있으면 `stocks.name` -> 코드 순으로 폴백한다. 실제 캐시 채우기는
    `_warm_attention(session)`가 담당한다(live_refresh 스케줄러와 공유).

    Returns ``{"rows": [...], "qry_tp": "1", "queried_at": iso8601}`` — 각 행은
    ``{"rank", "code", "name", "change_rate", "is_etf", "market"}``
    (``market``은 ``"kospi"``/``"kosdaq"``/``None``, 소문자).
    """
    return await _warm_attention(session)


# GET /api/markets/futures-flow/live 1분 메모리 캐시 (PLAN.md §4.7, 모듈
# docstring 참고) — breadth/live·flow/live와 동일한 패턴이지만 독립 캐시를 쓴다.
# 2026-07-21(§5.5-2→§5.6 회귀 수정): 단일 요청 1회뿐이라 1분으로 당겨도 비용이
# 늘지 않는다고 판단해 프런트 폴링 주기만 먼저 옮겼는데, 이 TTL과 live_refresh.py
# 스케줄러 잡 배정을 함께 옮기는 걸 빠뜨려 실제로는 계속 7분 캐시로 응답하는
# 회귀가 있었다(§5.6 후속 사용자 지적으로 재발견). TTL도 맞춘다.
_FUTURES_FLOW_LIVE_TTL_SECONDS = 60
_futures_flow_live_cache: dict[str, object] = {"ts": 0.0, "data": None}
_futures_flow_live_cache_lock = asyncio.Lock()


def _fetch_futures_flow_blocking(target_date: dt.date) -> dict | None:
    return naver_futures_flow.fetch_futures_flow(target_date)


async def _warm_futures_flow_live() -> dict:
    """futures-flow/live 캐시를 채우고 payload를 반환한다 — 라우트 핸들러와
    collectors/live_refresh.py의 5~10분 인터벌 잡이 공유한다. 장 마감이면
    네이버 호출을 생략하고 마지막 캐시(있으면)를 ``market_closed: true``로
    재사용한다 — 신규 5~10분 티어 전체의 기본 원칙(2026-07-20, breadth/flow/attention
    라이브와 동일한 게이트)."""
    now = time.monotonic()
    async with _futures_flow_live_cache_lock:
        cached = _futures_flow_live_cache["data"]
        if cached is not None and (now - _futures_flow_live_cache["ts"]) < _FUTURES_FLOW_LIVE_TTL_SECONDS:
            return cached

        now_kst = dt.datetime.now(KST)
        if _market_closed_kst(now_kst):
            if cached is not None:
                payload = {**cached, "market_closed": True}
            else:
                payload = {
                    "date": now_kst.date().isoformat(),
                    "investors": {},
                    "market_closed": True,
                    "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                }
            _futures_flow_live_cache["data"] = payload
            _futures_flow_live_cache["ts"] = now
            return payload

        today_kst = now_kst.date()
        try:
            result = await asyncio.to_thread(_fetch_futures_flow_blocking, today_kst)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"futures-flow live fetch failed: {str(e)[:200]}") from e

        if result is None:
            investors: dict[str, dict] = {}
            date = today_kst.isoformat()
        else:
            investors = _serialize_flow_investors(result["flows"])
            date = result["date"].isoformat()

        payload = {
            "date": date,
            "investors": investors,
            "market_closed": False,
            "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        _futures_flow_live_cache["data"] = payload
        _futures_flow_live_cache["ts"] = now
        return payload


@router.get("/api/markets/futures-flow/live")
async def futures_flow_live():
    """K200 선물 투자자별 순매수 장중 라이브(PLAN.md §4.7, 2026-07-20 실측 편입).

    네이버 모바일 트렌드 API를 온디맨드로 재조회해 7분 메모리 캐시로 감싼다
    (market_flow DB에는 쓰지 않는다 — §3.5 원칙, EOD 확정치는 여전히
    collectors/futures_flow.py 일별 배치가 담당). 휴장/데이터 없음이면
    ``investors``가 빈 dict. **장 마감이면 네이버 호출을 생략**하고 마지막 캐시를
    ``market_closed: true``로 재사용한다(`_warm_futures_flow_live` docstring 참고).

    Returns ``{"date": iso8601, "investors": {투자자명: {net_value, net_volume}},
    "market_closed": bool, "cached_at": iso8601}``.
    """
    return await _warm_futures_flow_live()


# GET /api/markets/fx/live 60초 메모리 캐시 — breadth/live와 동일한 이유(모듈
# docstring "GET /api/markets/fx/live" 절 참고, PLAN.md §5.5-3).
_FX_LIVE_CACHE_TTL_SECONDS = 60
_fx_live_cache: dict[str, object] = {"ts": 0.0, "data": None}
_fx_live_cache_lock = asyncio.Lock()


def _fetch_fx_latest_blocking() -> dict | None:
    """clients.naver_fx.fetch_usdkrw_naver를 [오늘, 오늘] 구간(페이지 1회)으로
    호출해 "오늘" 행 하나만 뽑는다. 실측(§5.5-3, 모듈 docstring 참고) 결과 이
    행이 장중 고시회차 갱신을 그대로 반영하므로 별도 라이브 클라이언트를
    새로 만들지 않고 기존 EOD용 함수를 그대로 재사용한다."""
    today = dt.date.today()
    rows = naver_fx.fetch_usdkrw_naver(today, today)
    if not rows:
        return None
    return rows[-1]


async def _fetch_fx_confirmed(session: AsyncSession) -> dict | None:
    """macro_series(series='usdkrw') DB 최신 확정치 — 장 마감/라이브 실패 폴백
    (breadth/live의 `_fetch_breadth_confirmed_for_market`과 동일한 패턴)."""
    stmt = (
        select(MacroSeries)
        .where(MacroSeries.series == "usdkrw")
        .order_by(MacroSeries.date.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        return None
    return {"date": row.date.isoformat(), "value": float(row.value), "source": "macro_series_db"}


async def _warm_fx_live(session: AsyncSession | None = None) -> dict:
    """fx/live 캐시를 채우고 payload를 반환한다 — 라우트 핸들러와
    collectors/live_refresh.py의 60초 인터벌 잡이 공유한다(breadth/live와
    동일한 패턴). macro_series 테이블에는 절대 쓰지 않는다(§3.5 원칙 — EOD
    확정치는 collectors/macro.py 일별 배치가 그대로 담당).

    장 마감이면 네이버 호출을 생략하고(다른 1분 티어 warm 함수와 동일한
    원칙) macro_series DB 최신 확정치로 응답한다. 라이브 호출이 실패해도(네이버
    비공식 API라 언제든 형태가 바뀔 수 있음) 같은 DB 폴백으로 넘어간다 —
    breadth/live와 달리 두 경로 모두 세션이 필요하므로 세션이 없으면(호출자가
    안 넘긴 경우) 폴백 없이 usdkrw: None으로 응답한다."""
    now = time.monotonic()
    async with _fx_live_cache_lock:
        cached = _fx_live_cache["data"]
        if cached is not None and (now - _fx_live_cache["ts"]) < _FX_LIVE_CACHE_TTL_SECONDS:
            return cached

        now_kst = dt.datetime.now(KST)
        if _market_closed_kst(now_kst):
            usdkrw = await _fetch_fx_confirmed(session) if session is not None else None
            payload = {
                "usdkrw": usdkrw,
                "market_closed": True,
                "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            }
            _fx_live_cache["data"] = payload
            _fx_live_cache["ts"] = now
            return payload

        try:
            row = await asyncio.to_thread(_fetch_fx_latest_blocking)
        except Exception as e:  # noqa: BLE001 - 비공식 API, 실패해도 DB 폴백으로 진행
            row = None
            logger.warning("fx_live: naver 조회 실패, DB 폴백 시도: %s", e)

        if row is not None:
            usdkrw = {"date": row["date"].isoformat(), "value": row["value"], "source": "naver"}
        else:
            usdkrw = await _fetch_fx_confirmed(session) if session is not None else None

        payload = {
            "usdkrw": usdkrw,
            "market_closed": False,
            "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        _fx_live_cache["data"] = payload
        _fx_live_cache["ts"] = now
        return payload


@router.get("/api/markets/fx/live")
async def market_fx_live(session: AsyncSession = Depends(get_session)):
    """USD/KRW 환율 장중 라이브(PLAN.md §5.5-3, 2026-07-21 실측 편입).

    clients/naver_fx.py의 m.stock.naver.com front-api를 오늘 하루 구간으로
    온디맨드 재조회해 60초 메모리 캐시로 감싼다(macro_series DB에는 쓰지
    않는다 — §3.5 원칙, EOD 확정치는 collectors/macro.py 일별 배치가 그대로
    담당). 장 마감이면 네이버 호출을 생략하고 macro_series 최신 확정치로
    폴백한다(`_warm_fx_live` docstring 참고).

    Returns ``{"usdkrw": {"date", "value", "source"} | None, "market_closed":
    bool, "cached_at": iso8601}``.
    """
    return await _warm_fx_live(session)


# GET /api/markets/regime — "검증 기반 시장 우세 판정"(PLAN.md §5.15, 2026-07-23).
# app/quant/regime_backtest.py의 스트릭/버킷 계산 함수를 코스피/코스닥 ×
# 외국인/기관계 4개 조합 전부에 돌리지만, **종합 판정 근거는 코스닥·외국인 하나뿐**
# 이다(§5.15 실측 검증 결과 — 나머지 3개 조합은 버킷 간 부호가 들쭉날쭉해 신호로
# 채택하지 않는다). "코스피는 신호가 약하다"는 사실을 감추지 않고 응답에 그대로
# 노출한다(각 조합의 "reliable" 플래그). 새 외부 호출 없음 — 오늘의 스트릭은
# `_warm_flow_live`가 이미 반환한 잠정치를 재사용해 어제까지 확정된 스트릭에
# 반영한다. breadth/live 등과 동일한 60초 메모리 캐시 + Lock 패턴.
_REGIME_CACHE_TTL_SECONDS = 60
_regime_cache: dict[str, object] = {"ts": 0.0, "data": None}
_regime_cache_lock = asyncio.Lock()

REGIME_MARKETS = ("kospi", "kosdaq")
REGIME_INVESTORS = ("외국인", "기관계")


def _sign(value: float | int) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _bucket_stats_for(buckets: list[dict], label: str | None) -> dict | None:
    if label is None:
        return None
    for b in buckets:
        if b["bucket"] == label:
            return b
    return None


async def _compute_regime_combo(
    session: AsyncSession, market: str, investor: str, market_flow_live: dict | None
) -> dict:
    """market x investor 한 조합의 "오늘 반영한 스트릭" + 그 스트릭 구간의
    과거 통계. ``market_flow_live``는 `_warm_flow_live` 응답의 해당 시장 값
    (``{"date", "investors", "provisional", "source"}``) — provisional=True일
    때만(진짜 오늘 장중 라이브 값일 때만) 확정 스트릭에 반영한다. provisional=False면
    `_warm_flow_live`가 이미 market_flow DB 확정치로 폴백한 것이라(그 값은 확정
    스트릭 계산에 이미 포함돼 있음) 다시 반영하면 이중 계산이 된다."""
    confirmed_streak = await regime_backtest.compute_current_streak(session, market, investor)
    streak = confirmed_streak
    live_applied = False
    if market_flow_live and market_flow_live.get("provisional") is True:
        live_net_value = market_flow_live.get("investors", {}).get(investor, {}).get("net_value")
        # PLAN.md §5.15-2: "오늘 잠정 방향이 스트릭과 같은 방향이면" 반영한다 —
        # 스트릭이 아직 없거나(0) 방향이 반대면 확정 스트릭을 그대로 둔다(오늘
        # 하루치 잠정 데이터로 스트릭 리셋/반전을 판정하지 않는다, 보수적 처리).
        if (
            live_net_value is not None
            and confirmed_streak != 0
            and _sign(live_net_value) == _sign(confirmed_streak)
        ):
            streak = regime_backtest.next_streak(confirmed_streak, live_net_value)
            live_applied = True

    buckets = await regime_backtest.compute_streak_buckets(session, market, investor)
    label = regime_backtest.bucket_label(streak)
    stats = _bucket_stats_for(buckets, label)
    return {
        "streak": streak,
        "confirmed_streak": confirmed_streak,
        "live_applied": live_applied,
        "bucket": label,
        "bucket_stats": stats,
        "reliable": market == "kosdaq" and investor == "외국인",
    }


def _judge_regime(kosdaq_foreign: dict) -> tuple[str, str]:
    """코스닥·외국인 조합 하나만 근거로 종합 판정한다(§5.15 원칙 — 코스피는
    자체 스트릭으로 "코스피우세"를 절대 만들지 않는다, 나머지 3개 조합은 참고
    수치로만 응답에 노출됨). 스트릭이 짧으면(1일 이하) 그 방향과 무관하게
    "중립"(표본이 짧아 판정 근거로 못 씀), 매도 스트릭(2일+)도 "중립"(코스닥에
    불리하다는 관찰이지 코스피가 유리하다는 뜻은 아니므로), 2일+ 연속 매수만
    "코스닥우세". 문구는 항상 관찰+확률 서술이다(§5 원칙 — 명령형/추천형 금지)."""
    streak = kosdaq_foreign["streak"]
    stats = kosdaq_foreign["bucket_stats"]

    if streak == 0:
        return "중립", "코스닥 외국인 수급 연속 방향 없음(직전 순매수/매도 전환 직후) — 판정 근거 부족"

    if abs(streak) == 1:
        direction = "매수" if streak > 0 else "매도"
        return "중립", f"코스닥 외국인 {abs(streak)}일 연속 {direction} 중 — 표본이 짧아(1일) 판정 근거로 쓰지 않음"

    if stats is None or stats.get("n", 0) == 0:
        direction = "매수" if streak > 0 else "매도"
        return "중립", f"코스닥 외국인 {abs(streak)}일 연속 {direction} 중 — 이 구간 과거 표본 없음"

    pct = stats["positive_rate_pct"]
    n = stats["n"]
    if streak >= 2:
        return "코스닥우세", f"코스닥 외국인 {streak}일 연속 매수 중 — 과거 이 구간 다음날 상승확률 {pct}%(표본 {n}일)"

    return (
        "중립",
        f"코스닥 외국인 {abs(streak)}일 연속 매도 중 — 과거 이 구간 다음날 상승확률 {pct}%(표본 {n}일), "
        "코스닥에 불리한 신호일 뿐 코스피가 유리하다는 뜻은 아님",
    )


async def _warm_regime(session: AsyncSession) -> dict:
    """regime 캐시를 채우고 payload를 반환한다 — 이 파일의 다른 라이브
    엔드포인트와 동일한 warm 함수 + TTL + Lock 패턴. 계산 자체는 수백 행
    집계라 비싸지 않지만(모듈 상단 주석 참고) 매 요청마다 재계산은 낭비라
    60초 TTL로 감싼다."""
    now = time.monotonic()
    async with _regime_cache_lock:
        cached = _regime_cache["data"]
        if cached is not None and (now - _regime_cache["ts"]) < _REGIME_CACHE_TTL_SECONDS:
            return cached

        try:
            flow_live_payload = await _warm_flow_live(session)
        except Exception as e:  # noqa: BLE001 - 라이브 실패해도 확정 스트릭만으로 판정 가능
            logger.warning("regime: flow/live 조회 실패, 확정치 스트릭만 사용: %s", e)
            flow_live_payload = {
                "kospi": None,
                "kosdaq": None,
                "market_closed": _market_closed_kst(dt.datetime.now(KST)),
            }

        market_closed = bool(flow_live_payload.get("market_closed"))

        combos: dict[str, dict[str, dict]] = {}
        baselines: dict[str, dict] = {}
        for market in REGIME_MARKETS:
            baselines[market] = await regime_backtest.compute_baseline(session, market)
            market_flow_live = flow_live_payload.get(market)
            combos[market] = {}
            for investor in REGIME_INVESTORS:
                combos[market][investor] = await _compute_regime_combo(session, market, investor, market_flow_live)

        regime, reason = _judge_regime(combos["kosdaq"]["외국인"])

        payload = {
            "regime": regime,
            "reason": reason,
            "reliable_signal": "kosdaq_foreign",
            "market_closed": market_closed,
            "kospi": {**combos["kospi"], "baseline": baselines["kospi"]},
            "kosdaq": {**combos["kosdaq"], "baseline": baselines["kosdaq"]},
            "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        _regime_cache["data"] = payload
        _regime_cache["ts"] = now
        return payload


@router.get("/api/markets/regime")
async def markets_regime(session: AsyncSession = Depends(get_session)):
    """"지금 어느 시장이 유리한지" 검증 기반 판정(PLAN.md §5.15).

    코스피/코스닥 각각 외국인/기관계 연속 순매수·매도 스트릭 + 그 스트릭
    구간의 과거 다음날 수익률 통계(app/quant/regime_backtest.py)를 계산한다.
    **종합 판정(regime/reason)은 코스닥·외국인 하나만 근거로 쓴다** — 코스피
    자체 스트릭으로는 "코스피우세"를 절대 만들지 않는다(§5.15 실측 검증 결과,
    코스피/기관 조합은 버킷 간 부호가 들쭉날쭉해 신호로 채택하지 않음). 각
    조합에는 ``reliable``(신뢰 가능한 신호인지) 플래그가 있어 프런트가 참고용
    수치를 흐리게 구분해 표시할 수 있다.

    Returns ``{"regime": "코스닥우세"|"중립", "reason": str, "reliable_signal":
    "kosdaq_foreign", "market_closed": bool, "kospi": {"외국인": {...}, "기관계":
    {...}, "baseline": {...}}, "kosdaq": {...}, "cached_at": iso8601}`` — 각
    투자자 값은 ``{"streak", "confirmed_streak", "live_applied", "bucket",
    "bucket_stats", "reliable"}``(``bucket_stats``는 ``{"bucket", "n",
    "avg_return_pct", "positive_rate_pct"}`` | None). "코스피우세"는 설계상
    나오지 않는다(§5.15 원칙 — 코스피 신호가 약하다는 걸 감추지 않는다).
    """
    return await _warm_regime(session)


@router.get("/api/markets/flow/intraday-accumulated")
async def flow_intraday_accumulated(
    days: int = Query(1, ge=1, le=30),
    session: AsyncSession = Depends(get_session),
):
    """장중 개인·외국인·기관계 순매수 누적 스냅샷 시계열(PLAN.md §5.4-2/3, §5.14).

    새로 소스를 호출하지 않는다 — `collectors/live_refresh.py`의 60초 잡이
    `_warm_flow_live`를 호출할 때마다 그 반환값을 `collectors.intraday_snapshot.
    record_flow_snapshot`에 넘겨 이미 ``intraday_sample`` 테이블에 적립해 둔
    것을 그대로 읽어서 반환할 뿐이다(이 엔드포인트 자체는 키움/네이버를 전혀
    두드리지 않는다). ka10051에는 분단위 이력이 없어 "3M"(EOD 히스토리, `GET
    /api/markets/flow/history` 계열)과 별개로 "1D"(장중 누적)를 자체 생성하는
    절충안이다(모듈 상단 collectors/intraday_snapshot.py docstring 참고).

    ``days``(기본 1=오늘만, 최대 30)로 과거 구간까지 조회할 수 있다(§5.14 —
    DB 영속화 이전에는 재배포마다 그날 적립분이 사라져 과거 조회 자체가
    불가능했다). 최근 7일은 60초 원본, 8일 전부터는 15분 단위로 압축된 값이
    섞여 나온다(collectors/intraday_compaction.py 배치, 해상도가 달라도 값은
    그대로 표시). 스케줄러가 꺼져 있거나(``ENABLE_LIVE_REFRESH`` 미설정) 앱이
    막 기동해 아직 한 번도 워밍이 안 됐으면 각 시리즈가 빈 리스트로 온다 —
    프런트는 이를 "적립 중" 상태로 표시한다.

    Returns ``{"date": "YYYY-MM-DD", "series": {"kospi": {"개인": [...],
    "외국인": [...], "기관계": [...]}, "kosdaq": {...}}, "market_closed": bool}``
    (PLAN.md §5.10 — 코스피/코스닥 분리) — 각 시리즈 원소는 ``{"time": "HH:MM",
    "value": float}``(``days>1``이면 ``"MM/DD HH:MM"``, net_value, 백만원 단위 —
    다른 flow 엔드포인트와 동일한 단위, 프런트에서 억원 변환). 코스피+코스닥
    "합계"는 백엔드가 미리 계산해 얹지 않는다. ``market_closed``는 저장된 값이
    아니라 호출 시점 기준으로 새로 계산한다.
    """
    return await intraday_snapshot.get_flow_series(session, days)


@router.get("/api/markets/foreign-position/intraday-accumulated")
async def foreign_position_intraday_accumulated(
    days: int = Query(1, ge=1, le=30),
    session: AsyncSession = Depends(get_session),
):
    """장중 외인 현물·선물 순매수 누적 스냅샷 시계열(PLAN.md §5.4-2/3, §5.14).

    "외인 양손" 상세 모달의 1D 탭 전용 — `flow_intraday_accumulated`와 같은
    테이블을 공유한다(현물 쪽은 ``flow_kospi_외국인``/``flow_kosdaq_외국인``을
    시간 키로 매칭해 합산한 값 — §5.10로 두 시장이 분리된 뒤에도 이 모달은 회귀
    없이 코스피+코스닥 합산 그대로 유지된다, collectors/intraday_snapshot.py
    `get_foreign_position_series` 참고). 선물 쪽("외인선물")은
    `_run_live_refresh`(60초 잡, §5.6 회귀 수정으로 이 잡에 합류)가
    `_warm_futures_flow_live` 반환값을 적립한 것이라 현물과 실제 갱신 간격이
    미묘하게 다를 수 있다 — 억지로 맞추지 않는다. 이 엔드포인트도 새 외부
    호출이 전혀 없다.

    ``days``(기본 1, 최대 30) — 위 flow_intraday_accumulated와 동일한 의미.

    Returns ``{"date": "YYYY-MM-DD", "spot": [...], "futures": [...],
    "market_closed": bool}`` — ``spot``/``futures`` 원소는 각각
    ``{"time": "HH:MM"|"MM/DD HH:MM", "value": float}``(net_value, 백만원 단위).
    """
    return await intraday_snapshot.get_foreign_position_series(session, days)


@router.get("/api/markets/breadth/intraday-accumulated")
async def breadth_intraday_accumulated(
    days: int = Query(1, ge=1, le=30),
    session: AsyncSession = Depends(get_session),
):
    """장중 등락비율(상승 대 하락 비율, %) 누적 스냅샷 시계열(PLAN.md §5.13, §5.14).

    "등락 종목수" 타일은 순간 스냅샷(현재 상승/하락 개수)만 보여줘 시간 흐름을
    놓친다는 사용자 지적으로 추가됐다 — `flow_intraday_accumulated`와 완전히
    같은 패턴이다: 새로 소스를 호출하지 않고, `collectors/live_refresh.py`의
    60초 잡이 `_warm_breadth_live`를 호출할 때마다 그 반환값을
    `collectors.intraday_snapshot.record_breadth_snapshot`에 넘겨 이미
    ``intraday_sample`` 테이블에 적립해 둔 것을 그대로 읽어서 반환할 뿐이다.

    ``days``(기본 1, 최대 30) — 위 flow_intraday_accumulated와 동일한 의미
    (과거 조회, 7일 초과 시 15분 압축본 포함). 스케줄러가 꺼져 있거나 앱이 막
    기동해 아직 한 번도 워밍이 안 됐으면 빈 리스트로 온다.

    Returns ``{"date": "YYYY-MM-DD", "series": [{"time": "HH:MM", "value": float}, ...],
    "market_closed": bool}`` — ``value``는 상승비율(%, 0~100), 코스피+코스닥
    합산 상승/하락 종목수만으로 계산하고 보합은 분모에서 제외한다(50%가 중립
    기준선). ``market_closed``는 저장된 값이 아니라 호출 시점 기준으로 새로
    계산한다.
    """
    return await intraday_snapshot.get_breadth_series(session, days)


# GET /api/markets/{market}/intraday — 지수 분봉(PLAN.md §5.1). kospi/kosdaq은
# 키움 ka20005(업종분봉차트요청)를 온디맨드로 호출해 "오늘"(최신 거래일) 하루치만
# 반환한다(DB 미저장 — §5 원칙, stocks.py의 종목 분봉과 동일한 캐시 패턴이지만
# market별 독립 캐시). **futures(K200 선물)는 501** — 네이버(m.stock.naver.com,
# api.stock.naver.com, siseJson.naver 전부)에서 선물 분봉 소스를 탐색했으나
# 찾지 못했다(2026-07-21 실측):
#   - `m.stock.naver.com/api/chart/domestic/index/FUT?periodType=minute` 및
#     `minuteN`/`min`/숫자 등 변형 전부 빈 응답(반면 dayCandle/weekCandle/
#     monthCandle은 정상 동작) — 엔드포인트 자체는 살아있지만 분 단위 미지원.
#   - `.../index/FUT/minute` 서브 리소스는 `[]`(빈 배열, 404 아님)를 반환 —
#     대조군으로 일반 종목(`item/005930/minute`)도 동일하게 `[]`라 이 리소스
#     자체가 공개 웹에 비활성화된 것으로 판단.
#   - 레거시 `fchart.stock.naver.com/siseJson.naver?timeframe=minute`도 헤더
#     행만 오고 데이터 행 없음.
#   - `polling.finance.naver.com`은 실시간 스냅샷 1건만 주는 시세 API라 시계열
#     차트에 쓸 수 없음.
# 키움 REST에는 애초에 선물 도메인이 없다(PLAN.md §1). 억지로 채우지 않고 501 +
# 이 근거를 응답에 남긴다.
INTRADAY_INDEX_CD = {"kospi": "001", "kosdaq": "101"}

_intraday_cache: dict[tuple[str, int], dict] = {}
_intraday_cache_lock = asyncio.Lock()


def _intraday_ttl_seconds(interval: int) -> int:
    """stocks.py의 동일 이름 함수와 같은 정책(1분봉 60초, 그 외 interval*60초)."""
    return 60 if interval == 1 else interval * 60


async def _warm_market_intraday(market: str, interval: int) -> dict:
    inds_cd = INTRADAY_INDEX_CD[market]
    cache_key = (market, interval)
    ttl = _intraday_ttl_seconds(interval)
    now = time.monotonic()
    async with _intraday_cache_lock:
        cached = _intraday_cache.get(cache_key)
        if cached is not None and (now - cached["ts"]) < ttl:
            return cached["data"]

        try:
            async with KiwoomClient() as client:
                data, _headers = await client.sector_minute_chart(inds_cd, str(interval))
        except Exception as e:  # noqa: BLE001 - 인증/네트워크/API 에러 전부 502로 변환
            raise HTTPException(
                502, detail={"source": "kiwoom_ka20005", "detail": str(e)[:300]}
            ) from e

        bars = parse_minute_chart_rows(data, "ka20005")
        payload = {
            "market": market,
            "interval": interval,
            "date": bars[-1]["date"] if bars else None,
            "bars": bars,
            "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        _intraday_cache[cache_key] = {"ts": now, "data": payload}
        return payload


@router.get("/api/markets/{market}/intraday")
async def market_intraday(market: str, interval: int = Query(..., description="분봉 간격(분)")):
    """지수 분봉 — kospi/kosdaq은 키움 ka20005를 온디맨드로 호출해 "오늘"(최신
    거래일) 하루치만 반환(DB 미저장, §5 원칙). futures는 501(위 모듈 주석의
    탐색 근거 참고). `interval`은 `MINUTE_CHART_INTERVALS`(1/3/5/10/15/30/45/60)만
    허용, 그 외는 400.

    Returns ``{"market", "interval", "date": "YYYYMMDD"|None, "bars": [...],
    "cached_at": iso8601}`` — bars 스키마는 `/api/stocks/{code}/intraday`와 동일.
    """
    if interval not in MINUTE_CHART_INTERVALS:
        raise HTTPException(400, f"interval must be one of {sorted(MINUTE_CHART_INTERVALS)}")
    if market == "futures":
        raise HTTPException(
            501,
            detail={
                "market": "futures",
                "detail": (
                    "K200 선물 분봉 데이터 소스 없음 — 키움 REST에 선물 도메인이 없고, "
                    "네이버(m.stock.naver.com/api.stock.naver.com/siseJson.naver) 실측 결과 "
                    "분 단위 차트를 제공하지 않음(PLAN.md §5.1 참고)"
                ),
            },
        )
    if market not in INTRADAY_INDEX_CD:
        raise HTTPException(400, f"market must be one of {sorted(INTRADAY_INDEX_CD)} + ['futures'(501)]")
    return await _warm_market_intraday(market, interval)


# GET /api/markets/index-tiles/live (2026-07-21, 대시보드 지수 타일 1D 실시간화) — 대시보드
# 상단 "지수" 타일(코스피/코스닥/선물) 전용, 60초 메모리 캐시. 기존 문제: DashboardPage의
# 지수 타일이 fetchMarketSeries(3M EOD, index_ohlcv 일별 배치)의 마지막 봉을 값으로 썼다
# — 갱신 자체가 없었고(setInterval 없음), 갱신되더라도 하루 1회 확정치 기준이었다.
#
# 코스피/코스닥은 위 `_warm_market_intraday(market, 1)`(ka20005 1분봉)을 그대로
# 재사용한다 — 별도 키움 호출을 새로 만들지 않고 캐시도 공유한다. 마지막 봉의 종가를
# 현재가로 쓴다. 선물(K200)은 분봉 소스가 없어(위 모듈 주석 "futures는 501" 절)
# routers/basis.py의 basis/live와 동일한 방식으로 clients/naver_index.py의 "오늘" 일봉을
# 온디맨드 재조회한다 — 2026-07-20 실측(basis.py 모듈 docstring)에서 이 봉이 체결마다
# 갱신되는 진짜 장중 캔들임을 이미 확인했다.
#
# 전일종가 대비 등락률은 세 시장 모두 `get_market_series_from_db(session, market, 1)`
# (index_ohlcv 확정치, DB_MARKET 매핑 재사용)의 최신 확정 종가를 prev_close로 써서
# 계산한다 — 장중에는 아직 오늘자 배치가 안 돌았으므로 이 값이 정확히 "어제 종가"다.
#
# 장 마감 게이트(breadth/live·flow/live·attention과 동일한 2026-07-20 원칙): 장
# 마감이면 키움/네이버를 아예 호출하지 않고, 세 시장 모두 `get_market_series_from_db`의
# 최신 확정치(EOD close+changeRate)로 즉시 응답한다.
_INDEX_TILES_CACHE_TTL_SECONDS = 60
_index_tiles_cache: dict[str, object] = {"ts": 0.0, "data": None}
_index_tiles_cache_lock = asyncio.Lock()


# ka20005(업종분봉차트요청) 가격 필드 스케일 버그(2026-07-21 실측, 이 작업 중 발견,
# clients/kiwoom.py의 "가격 필드 부호 인코딩 주의" 절에는 없던 사실) — cur_prc 등
# 가격 필드가 index_ohlcv(및 네이버 fchart) 대비 **100배** 스케일이다(예: 09:00
# 첫 봉 open_pric="+655388" vs 같은 순간 index_ohlcv 오늘 시가 6553.88). 개별
# 종목(ka10080)은 이 배율이 없고 지수(ka20005)만 해당한다.
#
# **2026-07-21 수정**: 처음엔 이 라우터 안에서만(÷100) 국소적으로 보정했는데,
# 같은 원본 파서(`parse_minute_chart_rows`)를 쓰는 시장 탭의 기존 분봉 차트
# (`/api/markets/{market}/intraday`, MarketPage.jsx)는 그대로 100배 값을 내려주고
# 있던 걸 뒤늦게 발견했다 — 캔들 "모양"은 스케일과 무관해 안 보였지만, Y축 눈금·
# 크로스헤어 범례(시/고/저/종 절대값)에는 655,388.00처럼 그대로 노출되는 실제
# 버그였다. `clients/kiwoom.py`의 `parse_minute_chart_rows`(공용 파서)로 보정을
# 옮겨 소비처가 몇 곳이든 한 곳만 고치면 되게 했다 — 이 아래 함수는 이미 보정된
# 값을 받으므로 여기서 다시 나누면 안 된다(이중 보정 버그 방지).


async def _index_tile_confirmed(session: AsyncSession, market: str) -> dict | None:
    """index_ohlcv 최신 확정치(EOD) — 장 마감 폴백 전용(그날 배치가 끝난 뒤엔 오늘
    날짜가 "확정치"이므로 날짜 제한 없이 최신 행을 그대로 쓴다)."""
    rows = await get_market_series_from_db(session, market, 1)
    if not rows:
        return None
    row = rows[-1]
    if row["close"] is None:
        return None
    return {
        "close": row["close"],
        "change_rate": row["changeRate"],
        "date": row["date"],
        "prev_close": None,
        "source": "index_ohlcv_confirmed",
    }


async def _index_tile_prev_close(session: AsyncSession, market: str) -> float | None:
    """라이브 등락률 계산용 "어제" 확정 종가 — index_ohlcv에서 **오늘 날짜보다 이전**
    최신 행만 본다. `_index_tile_confirmed`(get_market_series_from_db)는 오늘 날짜
    행이 이미 있으면(배치가 당겨 돌았거나 개발/시드 데이터 등) 그 행을 그대로
    반환해 버려 "오늘 대 오늘"을 비교하게 되는 함정이 있다 — 여기서는 명시적으로
    오늘을 제외해 반드시 전일 종가만 prev_close로 쓴다."""
    db_market = DB_MARKET.get(market, market)
    today = dt.date.today()
    stmt = (
        select(IndexOhlcv)
        .where(IndexOhlcv.market == db_market, IndexOhlcv.date < today)
        .order_by(IndexOhlcv.date.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None or row.close is None:
        return None
    return float(row.close)


async def _index_tile_from_intraday(session: AsyncSession, market: str) -> dict | None:
    """kospi/kosdaq 지수 타일 라이브 값 — ka20005 1분봉(캐시 공유) 마지막 봉 종가
    (parse_minute_chart_rows가 이미 100배 스케일을 보정해 반환함, 위 주석 참고) +
    index_ohlcv 전일 확정 종가 대비 등락률. 실패하면 None(호출자가 DB 확정치로 폴백)."""
    try:
        payload = await _warm_market_intraday(market, 1)
    except Exception as e:  # noqa: BLE001 - 라이브 실패가 다른 시장/폴백을 막지 않도록
        logger.warning("index-tiles live: %s intraday fetch failed: %s", market, e)
        return None
    bars = payload.get("bars") or []
    if not bars:
        return None
    close = bars[-1].get("close")
    if close is None:
        return None
    prev_close = await _index_tile_prev_close(session, market)
    change_rate = round((close - prev_close) / prev_close * 100, 4) if prev_close else None
    return {
        "close": close,
        "change_rate": change_rate,
        "date": payload.get("date"),
        "time": bars[-1].get("time"),
        "prev_close": prev_close,
        "source": "kiwoom_ka20005_1m",
    }


def _fetch_futures_today_blocking(start: dt.date, end: dt.date) -> list[dict]:
    return naver_index.fetch_index_series("k200_futures", start, end)


async def _index_tile_futures_live(session: AsyncSession) -> dict | None:
    """선물(K200) 지수 타일 라이브 값 — clients/naver_index.py "오늘" 일봉(basis/live와
    같은 소스, 스케일 문제 없음) 마지막 행 종가 + index_ohlcv 전일 확정 종가 대비
    등락률."""
    today = dt.date.today()
    start = today - dt.timedelta(days=5)
    try:
        rows = await asyncio.to_thread(_fetch_futures_today_blocking, start, today)
    except Exception as e:  # noqa: BLE001
        logger.warning("index-tiles live: futures fetch failed: %s", e)
        return None
    if not rows:
        return None
    last = rows[-1]
    prev_close = await _index_tile_prev_close(session, "futures")
    close = last["close"]
    change_rate = round((close - prev_close) / prev_close * 100, 4) if prev_close else None
    return {
        "close": close,
        "change_rate": change_rate,
        "date": last["date"].isoformat(),
        "time": None,
        "prev_close": prev_close,
        "source": "naver_fchart_today_bar",
    }


async def _warm_index_tiles_live(session: AsyncSession) -> dict:
    """index-tiles/live 캐시를 채우고 payload를 반환한다 — 라우트 핸들러와
    collectors/live_refresh.py의 60초 인터벌 잡이 공유한다(이 파일의 다른 라이브
    엔드포인트와 동일한 warm 함수 + TTL + Lock 패턴)."""
    now = time.monotonic()
    async with _index_tiles_cache_lock:
        cached = _index_tiles_cache["data"]
        if cached is not None and (now - _index_tiles_cache["ts"]) < _INDEX_TILES_CACHE_TTL_SECONDS:
            return cached

        now_kst = dt.datetime.now(KST)
        market_closed = _market_closed_kst(now_kst)

        result: dict[str, dict | None] = {"kospi": None, "kosdaq": None, "futures": None}
        if not market_closed:
            for market in ("kospi", "kosdaq"):
                result[market] = await _index_tile_from_intraday(session, market)
            result["futures"] = await _index_tile_futures_live(session)

        # 장 마감이거나 라이브 호출이 실패한 시장만 DB 확정치로 채운다.
        for market in ("kospi", "kosdaq", "futures"):
            if result.get(market) is None:
                result[market] = await _index_tile_confirmed(session, market)

        payload = {
            "kospi": result.get("kospi"),
            "kosdaq": result.get("kosdaq"),
            "futures": result.get("futures"),
            "market_closed": market_closed,
            "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        _index_tiles_cache["data"] = payload
        _index_tiles_cache["ts"] = now
        return payload


@router.get("/api/markets/index-tiles/live")
async def index_tiles_live(session: AsyncSession = Depends(get_session)):
    """대시보드 상단 "지수" 타일(코스피/코스닥/선물) 전용 라이브 — 60초 메모리 캐시
    (모듈 주석 "대시보드 지수 타일 1D 실시간화" 절 참고).

    코스피/코스닥은 `/api/markets/{market}/intraday?interval=1`과 캐시를 공유하는
    ka20005 1분봉의 마지막 종가, 선물은 clients/naver_index.py의 "오늘" 일봉(체결마다
    갱신, basis/live와 동일 소스) 마지막 종가를 쓴다. 등락률은 세 시장 모두
    index_ohlcv 최신 확정 종가(prev_close) 대비. **장 마감이면 키움/네이버 호출을
    생략**하고 세 시장 모두 index_ohlcv 확정치(EOD)로 즉시 응답한다.

    Returns ``{"kospi": {close, change_rate, date, time, prev_close, source}|None,
    "kosdaq": {...}|None, "futures": {...}|None, "market_closed": bool,
    "cached_at": iso8601}``.
    """
    return await _warm_index_tiles_live(session)
