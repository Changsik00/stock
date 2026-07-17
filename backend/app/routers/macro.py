"""GET /api/macro/series — 매크로(환율/유가) 시계열 묶음 (PLAN.md §5.3).

프런트는 DB만 조회한다 (설계 원칙: DB 캐싱 우선, §5.4) — 배치가 미리 macro_series에
적재해둔 값을 그대로 반환할 뿐, 이 라우터에서 직접 외부 API를 호출하지 않는다.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import MacroSeries

router = APIRouter(prefix="/api/macro", tags=["macro"])


@router.get("/series")
async def macro_series(
    ids: str = Query(..., description="comma-separated series ids, e.g. usdkrw,wti,brent"),
    days: int = Query(365, ge=1, le=3650),
    session: AsyncSession = Depends(get_session),
) -> dict:
    series_ids = [s.strip() for s in ids.split(",") if s.strip()]
    since = dt.date.today() - dt.timedelta(days=days)

    series_map: dict[str, list[dict]] = {sid: [] for sid in series_ids}
    if not series_ids:
        return {"days": days, "series": series_map}

    stmt = (
        select(MacroSeries)
        .where(MacroSeries.series.in_(series_ids), MacroSeries.date >= since)
        .order_by(MacroSeries.series, MacroSeries.date)
    )
    rows = (await session.execute(stmt)).scalars().all()

    for r in rows:
        series_map.setdefault(r.series, []).append(
            {
                "date": r.date.isoformat(),
                "value": float(r.value) if r.value is not None else None,
                "source": r.source,
            }
        )

    return {"days": days, "series": series_map}
