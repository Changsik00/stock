"""GET /api/markets/{market}/series — index OHLCV series from the KRX Open API,
merged with DB-cached investor flow (market_flow, PLAN.md §5.2/§5.3/§6 1-5).

This is a lift-and-shift of the original single-endpoint app/main.py logic
(PLAN.md §5.3). The legacy `/api/series?market=` path is kept as an alias so
the existing frontend keeps working until it's migrated — it returns only the
`series` list (no flows) for backward compatibility.
"""

from __future__ import annotations

import asyncio
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..krx_client import KRXAuthError, KRXClient
from ..models import MarketFlow
from ..services import get_futures_series, get_index_series

router = APIRouter(tags=["markets"])

MARKETS = {"kospi", "kosdaq", "futures"}

# market_flow는 코스피/코스닥만 적재된다 (선물 투자자별 수급은 PLAN.md §6 Phase 4 대상).
FLOW_MARKETS = {"kospi", "kosdaq"}


def _build_prices(market: str, days: int) -> dict:
    if market not in MARKETS:
        raise HTTPException(400, f"market must be one of {sorted(MARKETS)}")

    try:
        client = KRXClient()
    except KRXAuthError as e:
        raise HTTPException(500, str(e)) from e

    try:
        if market == "futures":
            data = get_futures_series(client, days)
        else:
            data = get_index_series(client, market, days)
    except KRXAuthError as e:
        raise HTTPException(
            502,
            "KRX Open API 인증/승인 오류입니다. openapi.krx.co.kr 마이페이지에서 "
            f"해당 데이터셋 이용 승인 상태를 확인하세요. ({e})",
        ) from e

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
    # _build_prices makes up to `days` sequential blocking `requests` calls to the KRX
    # Open API. This endpoint must stay `async def` (it awaits the DB flow query below),
    # so the blocking call is offloaded via to_thread — otherwise it would run directly
    # on the single event loop and serialize every other in-flight request (including
    # unrelated ones) for the full duration of the KRX fetch.
    result = await asyncio.to_thread(_build_prices, market, days)
    result["prices"] = result.pop("series")
    result["flows"] = await _build_flows(market, days, session)
    return result


@router.get("/api/series")
def legacy_series(market: str = Query(...), days: int = Query(90, ge=1, le=400)):
    """Deprecated alias for /api/markets/{market}/series — kept for the current frontend.

    Returns only the price series (no flows) for backward compatibility. Stays a plain
    `def` so Starlette threadpools it automatically (same blocking-call rationale as
    market_series above).
    """
    return _build_prices(market, days)
