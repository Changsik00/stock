"""GET /api/markets/{market}/series — index OHLCV series from the DB (index_ohlcv),
merged with DB-cached investor flow (market_flow, PLAN.md §5.2/§5.3/§6 1-5).

Both series and flows are DB-only reads (PLAN.md §5.4 "DB 캐싱 우선") — the KRX
Open API dataset approval is currently rejected (403 as of 2026-07), so this
router no longer calls it live. index_ohlcv is populated by the daily
collectors/ohlcv.py batch (yfinance/네이버, see services.get_market_series_from_db
for the KRX->DB migration note). The legacy `/api/series?market=` path is kept as
an alias so the existing frontend keeps working until it's migrated — it returns
only the `series` list (no flows) for backward compatibility.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import MarketFlow
from ..services import get_market_series_from_db

router = APIRouter(tags=["markets"])

MARKETS = {"kospi", "kosdaq", "futures"}

# market_flow는 코스피/코스닥만 적재된다 (선물 투자자별 수급은 PLAN.md §6 Phase 4 대상).
FLOW_MARKETS = {"kospi", "kosdaq"}


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
