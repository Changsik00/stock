"""GET /api/markets/flow-anomaly — 이상 거래(거래대금 급증 + 외인·기관 쏠림 + 공매도 급증) 카드
(PLAN.md Phase 5.117). 표시 전용, 매매 신호 아님.

외부 호출을 추가하지 않는다. 오늘 거래대금은 ``routers.flow_rank`` 의 value-rank/live 캐시를 *읽기만* 한다
(``_warm_value_rank_live`` 는 캐시 미스 때 네이버를 호출하므로 쓰지 않는다 — 캐시는 대시보드의 value-rank/live
폴링이 채운다). 캐시가 비었거나 DB의 value_rank가 더 최신이면 value_rank 테이블 최신일로 폴백한다.
수급은 stock_flow(외국인+기관계), 공매도는 short_selling_stock, 20일 평균은 stock_ohlcv(close x volume,
value 컬럼은 비어 있음). DB 금액은 백만원, value-rank의 value도 백만원(naver value_million)이다.
판정은 quant/flow_anomaly.py(순수 함수). 응답은 60초 메모리 캐시.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..market_hours import KST
from ..models import ShortSellingStock, StockFlow, StockOhlcv, ValueRank
from ..quant import flow_anomaly as fa
from . import flow_rank as flow_rank_router

router = APIRouter()

CACHE_TTL_SECONDS = 60
AVG_DAYS = 20  # 직전 거래일 수
MIN_AVG_DAYS = 10  # 거래대금 평균에 필요한 최소 일수(미달이면 판정 불가)
SHORT_MIN_DAYS = 5  # 공매도 평균에 필요한 최소 일수(미달이면 공매도 조건 제외)
FULL_DAY_MIN_ROWS = 500  # stock_ohlcv에서 "완전한 거래일"로 보는 최소 종목 수(장중 부분 적재일 제외)
FLOW_INVESTORS = ("외국인", "기관계")
LIMIT_PER_SIDE = 20
SCOPE_NOTE = "수급은 거래대금 상위 약 155종목만 장중 스캔합니다. 그 밖의 종목은 판정하지 않습니다."

_cache: dict[str, object] = {"ts": 0.0, "data": None}
_lock = asyncio.Lock()


async def _today_values(session: AsyncSession) -> dict:
    """오늘 거래대금 후보 목록: {date, rows[{code,name,market,value,change_rate}], source, provisional, as_of}."""
    db_date = (await session.execute(select(func.max(ValueRank.date)))).scalar()
    cache = flow_rank_router._value_rank_live_cache.get("data")  # 읽기만 — 외부 호출 없음
    cache_date = None
    if cache and cache.get("rows") and cache.get("date"):
        cache_date = dt.date.fromisoformat(cache["date"][:10])
    if cache_date and (db_date is None or cache_date >= db_date):
        rows = [
            {
                "code": r["code"],
                "name": r.get("name"),
                "market": r.get("market"),
                "value": r.get("value"),
                "change_rate": r.get("change_rate"),
            }
            for r in cache["rows"]
            if not r.get("is_etf")
        ]
        as_of = None
        try:
            as_of = dt.datetime.fromisoformat(cache["cached_at"]).astimezone(KST).strftime("%H:%M")
        except (KeyError, ValueError, TypeError):
            pass
        return {
            "date": cache_date,
            "rows": rows,
            "source": "live",
            "provisional": not cache.get("market_closed", False),
            "as_of": as_of,
        }
    if db_date is None:
        return {"date": None, "rows": [], "source": "none", "provisional": False, "as_of": None}
    db_rows = (
        await session.execute(select(ValueRank).where(ValueRank.date == db_date, ValueRank.is_etf.is_(False)))
    ).scalars().all()
    seen: set[str] = set()
    rows = []
    for r in sorted(db_rows, key=lambda x: -(x.value or 0)):
        if r.code in seen:
            continue
        seen.add(r.code)
        rows.append(
            {
                "code": r.code,
                "name": r.name,
                "market": r.market,
                "value": r.value,
                "change_rate": float(r.change_rate) if r.change_rate is not None else None,
            }
        )
    return {"date": db_date, "rows": rows, "source": "value_rank", "provisional": False, "as_of": None}


async def _compute(session: AsyncSession) -> dict:
    today = await _today_values(session)
    day = today["date"]
    base = {
        "date": day.isoformat() if day else None,
        "as_of": today["as_of"],
        "source": today["source"],
        "provisional": today["provisional"],
        "market_closed": not today["provisional"],
        "scope_note": SCOPE_NOTE,
        "thresholds": {
            "value_surge_mult": fa.VALUE_SURGE_MULT,
            "flow_share_min": fa.FLOW_SHARE_MIN,
            "flow_abs_min_million": fa.FLOW_ABS_MIN_MILLION,
            "short_surge_mult": fa.SHORT_SURGE_MULT,
            "min_conditions": fa.MIN_CONDITIONS,
            "avg_days": AVG_DAYS,
        },
        "evaluated": 0,
        "sell": [],
        "buy": [],
        "sell_total": 0,
        "buy_total": 0,
    }
    if not day or not today["rows"]:
        return base
    codes = [r["code"] for r in today["rows"]]

    # 직전 거래일(완전 적재일만) 최대 20일
    date_rows = (
        await session.execute(
            select(StockOhlcv.date)
            .where(StockOhlcv.date < day)
            .group_by(StockOhlcv.date)
            .having(func.count() >= FULL_DAY_MIN_ROWS)
            .order_by(StockOhlcv.date.desc())
            .limit(AVG_DAYS)
        )
    ).scalars().all()
    if len(date_rows) < MIN_AVG_DAYS:
        return base
    avg_rows = (
        await session.execute(
            select(StockOhlcv.code, func.avg(StockOhlcv.close * StockOhlcv.volume) / 1e6, func.count())
            .where(StockOhlcv.date.in_(date_rows), StockOhlcv.code.in_(codes), StockOhlcv.close.isnot(None), StockOhlcv.volume.isnot(None))
            .group_by(StockOhlcv.code)
        )
    ).all()
    avg_value = {c: float(a) for c, a, n in avg_rows if n >= MIN_AVG_DAYS and a}

    flow_rows = (
        await session.execute(
            select(StockFlow.code, func.sum(StockFlow.net_value), func.count(func.distinct(StockFlow.investor)))
            .where(StockFlow.date == day, StockFlow.investor.in_(FLOW_INVESTORS), StockFlow.code.in_(codes), StockFlow.net_value.isnot(None))
            .group_by(StockFlow.code)
        )
    ).all()
    net_flow = {c: float(s) for c, s, n in flow_rows if n == len(FLOW_INVESTORS)}

    short_today = {
        c: float(v)
        for c, v in (
            await session.execute(
                select(ShortSellingStock.code, ShortSellingStock.value).where(
                    ShortSellingStock.date == day, ShortSellingStock.code.in_(codes), ShortSellingStock.value.isnot(None)
                )
            )
        ).all()
    }
    short_avg_rows = (
        await session.execute(
            select(ShortSellingStock.code, func.avg(ShortSellingStock.value), func.count())
            .where(ShortSellingStock.date.in_(date_rows), ShortSellingStock.code.in_(list(short_today)), ShortSellingStock.value.isnot(None))
            .group_by(ShortSellingStock.code)
        )
    ).all()
    short_avg = {c: float(a) for c, a, n in short_avg_rows if n >= SHORT_MIN_DAYS and a}

    sell: list[dict] = []
    buy: list[dict] = []
    evaluated = 0
    for r in today["rows"]:
        code = r["code"]
        res = fa.evaluate(r["value"], avg_value.get(code), net_flow.get(code), short_today.get(code), short_avg.get(code))
        if res is None:
            continue
        evaluated += 1
        if not res["is_candidate"]:
            continue
        item = {
            "code": code,
            "name": r["name"],
            "market": r["market"],
            "change_rate": r["change_rate"],
            "value": r["value"],
            "avg_value": avg_value[code],
            "net_flow": net_flow[code],
            "short_value": short_today.get(code),
            "short_avg": short_avg.get(code),
            **res,
        }
        (sell if res["direction"] == "sell" else buy).append(item)
    sell.sort(key=lambda x: -x["sort_key"])
    buy.sort(key=lambda x: -x["sort_key"])
    base.update(
        evaluated=evaluated,
        sell_total=len(sell),
        buy_total=len(buy),
        sell=sell[:LIMIT_PER_SIDE],
        buy=buy[:LIMIT_PER_SIDE],
    )
    return base


@router.get("/api/markets/flow-anomaly")
async def flow_anomaly(session: AsyncSession = Depends(get_session)) -> dict:
    """이상 거래 후보(매도 쏠림/매수 쏠림). 두 조건 이상 충족 종목, |순매수|÷거래대금 내림차순. 참고용."""
    async with _lock:
        now = time.monotonic()
        if _cache["data"] is not None and now - _cache["ts"] < CACHE_TTL_SECONDS:
            return _cache["data"]  # type: ignore[return-value]
        data = await _compute(session)
        _cache["data"] = data
        _cache["ts"] = now
        return data
