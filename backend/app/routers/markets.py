"""GET /api/markets/{market}/series — index OHLCV series from the DB (index_ohlcv),
merged with DB-cached investor flow (market_flow, PLAN.md §5.2/§5.3/§6 1-5).

Both series and flows are DB-only reads (PLAN.md §5.4 "DB 캐싱 우선") — the KRX
Open API dataset approval is currently rejected (403 as of 2026-07), so this
router no longer calls it live. index_ohlcv is populated by the daily
collectors/ohlcv.py batch (yfinance/네이버, see services.get_market_series_from_db
for the KRX->DB migration note). The legacy `/api/series?market=` path is kept as
an alias so the existing frontend keeps working until it's migrated — it returns
only the `series` list (no flows) for backward compatibility.

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
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import naver_breadth
from ..clients.kiwoom import KiwoomClient
from ..collectors.market_flow import fetch_live_flow
from ..db import get_session
from ..models import MarketBreadth, MarketFlow
from ..services import get_market_series_from_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["markets"])

MARKETS = {"kospi", "kosdaq", "futures"}

# market_flow는 코스피/코스닥만 적재된다 (선물 투자자별 수급은 PLAN.md §6 Phase 4 대상).
FLOW_MARKETS = {"kospi", "kosdaq"}

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

    since = dt.date.today() - dt.timedelta(days=days)
    stmt = (
        select(MarketFlow)
        .where(MarketFlow.market == market, MarketFlow.date >= since)
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


@router.get("/api/markets/breadth/live")
async def market_breadth_live():
    """장중 온디맨드 등락 종목수 — 코스피/코스닥을 소스(네이버)에서 직접 조회하고
    60초 메모리 캐시로 감싼다. **market_breadth 테이블에는 절대 쓰지 않는다**
    (§3.5 "장중 값은 DB에 쌓지 않는다" 원칙 — 캐시는 프로세스 메모리에만 존재).

    Returns ``{"kospi": {...} | None, "kosdaq": {...} | None, "cached_at": iso8601}``.
    한 시장 조회가 실패하면 그 시장만 None, 다른 시장은 정상 반환(소스 일시 장애가
    전체를 막지 않도록) — 둘 다 실패하면 502.
    """
    now = time.monotonic()
    async with _live_cache_lock:
        cached = _live_cache["data"]
        if cached is not None and (now - _live_cache["ts"]) < _LIVE_CACHE_TTL_SECONDS:
            return cached

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
            "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        _live_cache["data"] = payload
        _live_cache["ts"] = now
        return payload


# GET /api/markets/flow/live 60초 메모리 캐시 — breadth/live와 동일한 이유
# (프로세스 재기동 시 초기화되는 단순 캐시로 충분, 동시 요청은 asyncio.Lock으로 감쌈).
_FLOW_LIVE_CACHE_TTL_SECONDS = 60
_flow_live_cache: dict[str, object] = {"ts": 0.0, "data": None}
_flow_live_cache_lock = asyncio.Lock()

KST = dt.timezone(dt.timedelta(hours=9))
# 정규장 여부 추정 플래그 (정밀 개장일력 아님 — 공휴일은 구분 못 함).
# 주말이거나 정규장 시간(09:00~15:30 KST) 밖이면 closed. 원래는 "15:30 이후"만
# 봤는데 일요일 새벽에도 open으로 판정돼 대시보드 기준일이 오늘 날짜로 부풀던
# 왜곡이 있었다(2026-07-19 수정). 공휴일 오검은 남지만, 그 경우에도 프런트가
# 잠정치 라벨을 붙일 뿐 데이터 자체는 마지막 거래일 값이라 치명적이지 않다.
_MARKET_OPEN_TIME_KST = dt.time(9, 0)
_MARKET_CLOSE_TIME_KST = dt.time(15, 30)


def _market_closed_kst(now_kst: dt.datetime) -> bool:
    if now_kst.weekday() >= 5:  # 토(5)/일(6)
        return True
    return not (_MARKET_OPEN_TIME_KST <= now_kst.time() < _MARKET_CLOSE_TIME_KST)


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


@router.get("/api/markets/flow/live")
async def market_flow_live(session: AsyncSession = Depends(get_session)):
    """장중 잠정 투자자별 순매수 — PLAN.md §6 Phase 3.7-3.

    코스피/코스닥 각각 ka10051(base_dt=오늘)을 온디맨드로 호출해 60초 메모리
    캐시로 감싼다(모듈 docstring 참고 — ka10063 대신 이 TR을 쓰는 이유). 시장별로
    독립 처리해 한쪽이 실패해도 다른 쪽은 정상 반환하고, 라이브 호출이 실패한
    시장은 market_flow DB의 최신 확정치로 폴백한다(``provisional: false``).
    두 시장 다 라이브·폴백 전부 실패하면 502.

    Returns ``{"kospi": {...}|None, "kosdaq": {...}|None, "market_closed": bool,
    "cached_at": iso8601}`` — 각 시장 값은 ``{"date", "investors":
    {투자자명: {net_value, net_volume}}, "provisional", "source"}``.
    """
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
