"""GET /api/markets/flow-rank — 투자자별 순매수/순매도 상위 종목 스냅샷 (PLAN.md §4.5/§6 3.5-2b).
GET /api/markets/flow-path — ETF look-through 수급 경로 분해 상위 (PLAN.md §4.5/§6 3.5-3).
GET /api/markets/value-rank — 거래대금 상위 종목("돈이 모이는 곳") 스냅샷 (PLAN.md §4.6 3.6-1).

DB 전용 조회다(§5.4 "DB 캐싱 우선") — collectors/flow_rank.py·collectors/flow_path.py가
미리 적재해 둔 테이블을 그대로 읽어 반환할 뿐, 이 라우터에서 네이버를 직접 호출하지
않는다.

flow-rank는 날짜별로 묶어 반환한다(최근 날짜 먼저) — flow_rank는 소스 제약상
(naver_rank.py docstring 참고) 하루 배치당 최근 2거래일만 채워지므로, days로 조회
가능한 날짜 수는 실제로는 배치를 며칠 반복 실행한 누적分만큼이다. side 파라미터
(buy/sell, 기본 buy)로 순매수/순매도 랭킹을 고른다 — 기본값이 buy라 side를 안 주는
기존 호출자는 그대로 동작한다(하위호환). 각 row의 net_value/quantity는 항상 양수
(크기)이고 어느 방향인지는 side로만 구분한다(models.py FlowRank docstring 참고).

flow-path는 side 파라미터가 없다 — collectors/flow_path.py가 direct_net을 계산할 때
이미 side='buy'(순매수) 행만 쓰도록 고정했으므로(순매도까지 합치면 "직접 순매수"의
의미가 사라짐) 이 핸들러 자체는 변경하지 않는다. days 창 안에서 가장 최근 날짜
하나만 골라(flow_rank와 달리 날짜별 비교 UI가 아직 없음) via_etf_net 내림차순 상위
limit개를 반환한다.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import FlowPath, FlowRank, Stock, ValueRank

router = APIRouter(tags=["markets"])

INVESTORS = {"foreign", "institution"}
SIDES = {"buy", "sell"}
MARKET_FILTERS = {"all", "kospi", "kosdaq"}


@router.get("/api/markets/flow-rank")
async def flow_rank_series(
    investor: str = Query("foreign", description="foreign 또는 institution"),
    side: str = Query("buy", description="buy(순매수) 또는 sell(순매도) — 기본 buy로 하위호환 유지"),
    days: int = Query(1, ge=1, le=30),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if investor not in INVESTORS:
        raise HTTPException(400, f"investor must be one of {sorted(INVESTORS)}")
    if side not in SIDES:
        raise HTTPException(400, f"side must be one of {sorted(SIDES)}")

    since = dt.date.today() - dt.timedelta(days=days)
    stmt = (
        select(FlowRank)
        .where(FlowRank.investor == investor, FlowRank.side == side, FlowRank.date >= since)
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
                "quantity": r.quantity,
                "turnover": float(r.turnover) if r.turnover is not None else None,
                "is_etf": r.is_etf,
                # §4.6 3.6-1: 2026-07-18부터 적재되는 nullable 컬럼(collectors/flow_rank.py
                # 참고) — 그 이전 적재분은 market이 NULL로 온다.
                "market": r.market,
            }
        )

    return {
        "investor": investor,
        "side": side,
        "days": days,
        "dates": [{"date": iso, "rows": entries} for iso, entries in dates.items()],
    }


@router.get("/api/markets/flow-path")
async def flow_path_top(
    days: int = Query(7, ge=1, le=90, description="이 창 안의 가장 최근 flow_path.date만 사용"),
    limit: int = Query(30, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """via_etf_net(ETF 경유 유입) 상위 종목 — collectors/flow_path.py가 적재한
    flow_path 중 days 창 안의 가장 최근 날짜 하나를 골라 via_etf_net 내림차순
    limit개를 반환한다. 날짜가 하나도 없으면(배치 미실행) rows는 빈 배열.

    이름 해석 순서: (1) stocks 테이블(현재는 ETF만 채워져 있음, Phase 2-2 종목마스터
    수집 전) -> (2) flow_rank(날짜 무관 가장 최근 관측치 — 개별주 이름은 여기서만
    구할 수 있는 경우가 많다, PLAN.md §4.5 지시 "flow_rank name 활용") -> (3) 그래도
    없으면 code 그대로. top_etfs는 collectors/flow_path.py가 이미 상위 5개로 잘라
    저장해 두었으므로 여기서는 그대로 내려준다.
    """
    since = dt.date.today() - dt.timedelta(days=days)
    latest_date = (
        await session.execute(select(func.max(FlowPath.date)).where(FlowPath.date >= since))
    ).scalar()

    if latest_date is None:
        return {"date": None, "days": days, "rows": []}

    stmt = (
        select(FlowPath)
        .where(FlowPath.date == latest_date)
        .order_by(FlowPath.via_etf_net.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()

    codes = [r.code for r in rows]
    name_map: dict[str, str] = {}
    if codes:
        name_rows = (
            await session.execute(select(Stock.code, Stock.name).where(Stock.code.in_(codes)))
        ).all()
        name_map = dict(name_rows)

        missing = [c for c in codes if c not in name_map]
        if missing:
            # flow_rank는 날짜별 스냅샷이라 같은 code가 여러 날짜에 걸쳐 나타날 수
            # 있다 -> 가장 최근 날짜의 이름을 쓴다(rank 오름차순은 무관, date desc만).
            fr_rows = (
                await session.execute(
                    select(FlowRank.code, FlowRank.name, FlowRank.date)
                    .where(FlowRank.code.in_(missing), FlowRank.name.isnot(None))
                    .order_by(FlowRank.date.desc())
                )
            ).all()
            for code, name, _date in fr_rows:
                name_map.setdefault(code, name)

    return {
        "date": latest_date.isoformat(),
        "days": days,
        "rows": [
            {
                "code": r.code,
                "name": name_map.get(r.code, r.code),
                "direct_net": r.direct_net,
                "via_etf_net": r.via_etf_net,
                "top_etfs": r.top_etfs or [],
            }
            for r in rows
        ],
    }


@router.get("/api/markets/value-rank")
async def value_rank_top(
    market: str = Query(
        "all", description="kospi/kosdaq/all(코스피+코스닥을 합쳐 거래대금 내림차순으로 재정렬)"
    ),
    days: int = Query(7, ge=1, le=90, description="이 창 안의 가장 최근 value_rank.date만 사용"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """거래대금 상위 종목("돈이 모이는 곳") 스냅샷 — collectors/value_rank.py가
    적재한 value_rank 중 days 창 안의 가장 최근 날짜 하나를 골라 반환한다
    (flow-path 핸들러와 동일 패턴: value_rank도 날짜별 비교 UI가 아직 없는
    단일 스냅샷 표라 days는 "얼마나 과거까지 최근 날짜를 찾아볼지"에만 쓰인다).

    market="all"일 때는 코스피+코스닥 저장된 상위 종목(각 시장 최대 100개,
    collectors/value_rank.py TOP_N)을 합쳐 거래대금(value) 내림차순으로 다시
    정렬하고 새 "표시 순위" 1..N을 매긴다 — 원본 market별 rank와 다를 수 있다
    (collectors/flow_rank.py가 코스피+코스닥을 합칠 때와 동일한 설계 결정).
    market="kospi"/"kosdaq"이면 저장된 시장별 rank를 그대로 쓴다.
    """
    if market not in MARKET_FILTERS:
        raise HTTPException(400, f"market must be one of {sorted(MARKET_FILTERS)}")

    since = dt.date.today() - dt.timedelta(days=days)
    market_clause = [] if market == "all" else [ValueRank.market == market]

    latest_date = (
        await session.execute(
            select(func.max(ValueRank.date)).where(ValueRank.date >= since, *market_clause)
        )
    ).scalar()

    if latest_date is None:
        return {"market": market, "date": None, "days": days, "rows": []}

    stmt = (
        select(ValueRank)
        .where(ValueRank.date == latest_date, *market_clause)
        .order_by(ValueRank.market.asc(), ValueRank.rank.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()

    if market == "all":
        rows = sorted(rows, key=lambda r: r.value if r.value is not None else -1, reverse=True)
        row_ranks = enumerate(rows, start=1)
    else:
        row_ranks = ((r.rank, r) for r in rows)

    return {
        "market": market,
        "date": latest_date.isoformat(),
        "days": days,
        "rows": [
            {
                "rank": rank,
                "market": r.market,
                "code": r.code,
                "name": r.name,
                "value": r.value,
                "change_rate": float(r.change_rate) if r.change_rate is not None else None,
                "is_etf": r.is_etf,
                "turnover": float(r.turnover) if r.turnover is not None else None,
            }
            for rank, r in row_ranks
        ],
    }
