"""GET /api/etf-pair/samples — 코스닥150 레버리지/인버스 ETF 쌍 괴리 표본 조회
(PLAN.md §5.88-1). **관찰 전용** — 괴리율(premium_pct)/본주 이론가 대비 시차
(lag_pct)의 일자별 행 목록과 종목별 분포 요약만 돌려주며, 어떤 판단·추천 문구도
넣지 않는다.

`collectors/etf_pair_sampler.py`가 쌓은 `etf_pair_sample`을 DB에서만 읽는다.
응답에는 항상 조회 일자(`date`)와 종목별 표본 수(`count`)를 함께 내려 표본이
적을 때 분포가 과장돼 읽히지 않게 한다(§5.40 관례). `date`를 생략하면 데이터가
있는 가장 최근 KST 일자를 쓰고, 데이터가 전혀 없으면 빈 목록을 돌려준다.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..market_hours import KST
from ..models import EtfPairSample
from ..quant.etf_pair_metrics import PAIR_ETFS, summarize_distribution

router = APIRouter(prefix="/api/etf-pair", tags=["etf-pair"])


def _f(x) -> float | None:
    return None if x is None else float(x)


@router.get("/samples")
async def etf_pair_samples(
    date: dt.date | None = Query(None, description="KST 기준 일자(YYYY-MM-DD). 생략 시 최신 적재일"),
    code: str | None = Query(None, description="종목코드 필터"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if date is None:
        latest_stmt = select(func.max(EtfPairSample.time)).where(
            EtfPairSample.code == code if code else EtfPairSample.code.in_(list(PAIR_ETFS))
        )
        latest = (await session.execute(latest_stmt)).scalar()
        if latest is None:
            return {"date": None, "code": code, "rows": [], "summary": {}}
        date = latest.astimezone(KST).date()

    start = dt.datetime.combine(date, dt.time.min, tzinfo=KST)
    stmt = (
        select(EtfPairSample)
        .where(EtfPairSample.time >= start, EtfPairSample.time < start + dt.timedelta(days=1))
        .order_by(EtfPairSample.time, EtfPairSample.code)
    )
    # 기본은 현재 쌍(PAIR_ETFS)만. 옛 코드(0193T0 등)는 code 명시 시에만 조회된다.
    if code:
        stmt = stmt.where(EtfPairSample.code == code)
    else:
        stmt = stmt.where(EtfPairSample.code.in_(list(PAIR_ETFS)))
    samples = (await session.execute(stmt)).scalars().all()

    rows = [
        {
            "time": r.time.astimezone(KST).isoformat(),
            "code": r.code,
            "multiple": r.multiple,
            "price": _f(r.price),
            "base_price": _f(r.base_price),
            "nav": _f(r.nav),
            "underlying_price": _f(r.underlying_price),
            "underlying_change_rate": _f(r.underlying_change_rate),
            "theory_price": _f(r.theory_price),
            "premium_pct": _f(r.premium_pct),
            "lag_pct": _f(r.lag_pct),
        }
        for r in samples
    ]

    by_code: dict[str, list[dict]] = {}
    for row in rows:
        by_code.setdefault(row["code"], []).append(row)
    summary = {
        c: {
            "count": len(items),
            "premium_pct": summarize_distribution([i["premium_pct"] for i in items]),
            "lag_pct": summarize_distribution([i["lag_pct"] for i in items]),
        }
        for c, items in by_code.items()
    }
    return {"date": date.isoformat(), "code": code, "rows": rows, "summary": summary}
