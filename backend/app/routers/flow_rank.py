"""GET /api/markets/flow-rank — 투자자별 순매수 상위 종목 스냅샷 (PLAN.md §4.5).

DB 전용 조회다(§5.4 "DB 캐싱 우선") — collectors/flow_rank.py가 미리 적재해 둔
flow_rank를 그대로 읽어 반환할 뿐, 이 라우터에서 네이버를 직접 호출하지 않는다.

날짜별로 묶어 반환한다(최근 날짜 먼저) — flow_rank는 소스 제약상(naver_rank.py
docstring 참고) 하루 배치당 최근 2거래일만 채워지므로, days로 조회 가능한 날짜
수는 실제로는 배치를 며칠 반복 실행한 누적分만큼이다.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import FlowRank

router = APIRouter(tags=["markets"])

INVESTORS = {"foreign", "institution"}


@router.get("/api/markets/flow-rank")
async def flow_rank_series(
    investor: str = Query("foreign", description="foreign 또는 institution"),
    days: int = Query(1, ge=1, le=30),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if investor not in INVESTORS:
        raise HTTPException(400, f"investor must be one of {sorted(INVESTORS)}")

    since = dt.date.today() - dt.timedelta(days=days)
    stmt = (
        select(FlowRank)
        .where(FlowRank.investor == investor, FlowRank.date >= since)
        .order_by(FlowRank.date.desc(), FlowRank.rank.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()

    dates: dict[str, list[dict]] = {}
    for r in rows:
        iso = r.date.isoformat()
        dates.setdefault(iso, []).append(
            {
                "rank": r.rank,
                "code": r.code,
                "name": r.name,
                "net_value": r.net_value,
                "is_etf": r.is_etf,
            }
        )

    return {
        "investor": investor,
        "days": days,
        "dates": [{"date": iso, "rows": entries} for iso, entries in dates.items()],
    }
