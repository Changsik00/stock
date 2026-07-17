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
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import naver_breadth
from ..db import get_session
from ..models import MarketBreadth, MarketFlow
from ..services import get_market_series_from_db

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
