"""GET /api/etf/derivative-flow — 파생형(레버리지/인버스) ETF 방향성 게이지
(PLAN.md §4.5/§6 4.5-1). GET /api/etf/list — ETF 목록 스냅샷(etf_stats+stocks).

## 개념 (PLAN.md §4.5 배경 — "중립적 상태 계기판", 함정 탐지기 아님)

개인 투자자는 선물을 직접 거래하기 어려워 레버리지/인버스 ETF로 방향성 베팅을
대신한다 — 파생형 ETF 자금 흐름은 "개인의 간접 선물 포지션" 프록시로 볼 수 있다.

- **방향성 순베팅(net_bet)** = Σ(파생형 ETF 순유입 × 방향부호) — 노출 기준 가중
  (레버리지=+2, 인버스=-1, 인버스2X=-2 — ``clients/naver_etf.classify_derivative``
  가 반환하는 배수를 그대로 곱한다). 예: 인버스2X에 순유입 100억 = -200억 노출.
  값이 크게 음수면 개인이 인버스에 몰려 하락 베팅 중, 크게 양수면 레버리지로
  상승 베팅 중이라는 뜻 — 부호 자체에 좋고 나쁨 의미를 두지 않는다(중립 계기판).
- **LP 헤지 수요 추정(lp_hedge_est)** = Σ(AUM 일간 변화 × 배수의 **부호만**(±1)).
  net_bet과 달리 배수 **크기**(2X)는 곱하지 않는다 — AUM(=좌수×NAV) 자체가 이미
  레버리지 상품의 경우 기초자산 등락의 2배로 움직이므로, 배수를 한 번 더 곱하면
  가격 변동분을 이중으로 반영하게 된다(AUM 일간 변화 자체에 가격 변동 성분이
  섞여 있어 자금 유출입만 순수 분리 불가 — 그래서 "참고치"로만 병기).
  전날 AUM 관측치가 없는 코드/날짜는 그 날의 lp_hedge_est 합산에서 제외된다.

## 데이터 소스

DB 전용 조회다(§5.4 "DB 캐싱 우선") — collectors/etf_master.py가 미리 적재해 둔
``etf_stats``(nav/aum/net_inflow, 백만 원)를 그대로 읽는다. 파생형 분류는
``stocks.is_etf=true`` 전체에 대해 요청마다 ``classify_derivative(name)``을
파이썬에서 계산한다(이름 목록이 자주 안 바뀌므로 DB 컬럼화 없이 매 요청 재계산 —
300개 규모라 비용 무시할 만함).

net_inflow는 2026-07-15부터 적재를 시작해 그날치 스냅샷만 있는 초기 단계다
(clients/naver_etf.py 모듈독스트링 "일별화 방법" 참고 — 매일 배치를 돌리며
누적되는 구조라 과거 소급은 불가능). 그래서 초기에는 series가 하루짜리일 수
있고, lp_hedge_est도 "전날 대비"가 없어 첫날은 대부분 None이다 — 정상 동작.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients.naver_etf import classify_derivative
from ..db import get_session
from ..models import EtfStat, Stock

router = APIRouter(prefix="/api/etf", tags=["etf"])


async def _derivative_universe(session: AsyncSession) -> dict[str, int]:
    """{code: multiplier} — is_etf=true 종목 중 classify_derivative가 None이 아닌 것만."""
    rows = (await session.execute(select(Stock.code, Stock.name).where(Stock.is_etf.is_(True)))).all()
    universe: dict[str, int] = {}
    for code, name in rows:
        multiplier = classify_derivative(name)
        if multiplier is not None:
            universe[code] = multiplier
    return universe


def _sign(multiplier: int) -> int:
    return 1 if multiplier > 0 else -1


@router.get("/derivative-flow")
async def derivative_flow(
    days: int = Query(30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
) -> dict:
    universe = await _derivative_universe(session)
    leverage_codes = {c for c, m in universe.items() if m > 0}
    inverse_codes = {c for c, m in universe.items() if m < 0}

    since = dt.date.today() - dt.timedelta(days=days)
    # AUM 일간 변화(lp_hedge_est)를 창(window)의 첫 날부터도 계산할 수 있도록,
    # since보다 하루 이상 과거 관측치도 함께 불러온다(diff 전용 — 응답 series에는
    # since 이후 날짜만 포함시킨다).
    lookback_since = since - dt.timedelta(days=7)

    if not universe:
        return {
            "days": days,
            "universe": {"total": 0, "leverage": 0, "inverse": 0},
            "latest": None,
            "series": [],
        }

    stmt = (
        select(EtfStat)
        .where(EtfStat.code.in_(universe.keys()), EtfStat.date >= lookback_since)
        .order_by(EtfStat.code, EtfStat.date)
    )
    rows = (await session.execute(stmt)).scalars().all()

    # code별로 (date -> aum) 시계열을 모아 "직전 관측치 대비" AUM 변화를 계산한다.
    by_code: dict[str, list[EtfStat]] = defaultdict(list)
    for r in rows:
        by_code[r.code].append(r)

    aum_diff: dict[tuple[str, dt.date], int] = {}
    for code, code_rows in by_code.items():
        prev_aum: int | None = None
        for r in code_rows:
            if prev_aum is not None and r.aum is not None:
                aum_diff[(code, r.date)] = r.aum - prev_aum
            if r.aum is not None:
                prev_aum = r.aum

    daily: dict[dt.date, dict] = {}

    def _bucket(date: dt.date) -> dict:
        return daily.setdefault(
            date,
            {
                "net_bet": 0,
                "lp_hedge_est": 0,
                "lp_hedge_has_data": False,
                "leverage_inflow": 0,
                "inverse_inflow": 0,
                "leverage_count": 0,
                "inverse_count": 0,
            },
        )

    for r in rows:
        if r.date < since:
            continue
        multiplier = universe[r.code]
        bucket = _bucket(r.date)

        if r.net_inflow is not None:
            bucket["net_bet"] += r.net_inflow * multiplier
            if r.code in leverage_codes:
                bucket["leverage_inflow"] += r.net_inflow
                bucket["leverage_count"] += 1
            else:
                bucket["inverse_inflow"] += r.net_inflow
                bucket["inverse_count"] += 1

        diff = aum_diff.get((r.code, r.date))
        if diff is not None:
            bucket["lp_hedge_est"] += diff * _sign(multiplier)
            bucket["lp_hedge_has_data"] = True

    series = [
        {
            "date": date.isoformat(),
            "net_bet": b["net_bet"],
            "lp_hedge_est": b["lp_hedge_est"] if b["lp_hedge_has_data"] else None,
            "leverage_inflow": b["leverage_inflow"],
            "inverse_inflow": b["inverse_inflow"],
            "counts": {
                "leverage": b["leverage_count"],
                "inverse": b["inverse_count"],
            },
        }
        for date, b in sorted(daily.items())
    ]

    return {
        "days": days,
        "universe": {
            "total": len(universe),
            "leverage": len(leverage_codes),
            "inverse": len(inverse_codes),
        },
        "latest": series[-1] if series else None,
        "series": series,
    }


@router.get("/list")
async def etf_list(
    days: int = Query(7, ge=1, le=90, description="이 창 안의 가장 최근 etf_stats.date만 사용"),
    limit: int = Query(300, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """ETF 목록 스냅샷(AUM 내림차순) — etf_stats(가장 최근 날짜) + stocks 이름 조인.

    별도 요청 없이도 즉시 가능해(등록된 etf_stats/stocks를 그대로 읽기만 함) 이번에
    함께 501 스텁을 해제했다(§4.5-1 작업 지시 "선택 — 하면 보고"). derivative-flow와
    같은 파생형 분류(classify_derivative)를 각 행에 얹어 프런트에서 배지로 쓸 수
    있게 한다.
    """
    from sqlalchemy import func

    since = dt.date.today() - dt.timedelta(days=days)
    latest_date = (
        await session.execute(select(func.max(EtfStat.date)).where(EtfStat.date >= since))
    ).scalar()

    if latest_date is None:
        return {"date": None, "days": days, "rows": []}

    stmt = (
        select(EtfStat, Stock.name)
        .join(Stock, Stock.code == EtfStat.code)
        .where(EtfStat.date == latest_date)
        .order_by(EtfStat.aum.desc().nulls_last())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()

    return {
        "date": latest_date.isoformat(),
        "days": days,
        "rows": [
            {
                "code": stat.code,
                "name": name,
                "nav": float(stat.nav) if stat.nav is not None else None,
                "aum": stat.aum,
                "net_inflow": stat.net_inflow,
                "derivative_multiplier": classify_derivative(name),
            }
            for stat, name in rows
        ],
    }
