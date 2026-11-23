"""GET /api/markets/nq-comovement — 코스피·나스닥 선물 장중 동조도 (PLAN.md Phase 5.116). 표시 전용, 매매 신호 아님.

``index_intraday_bar``(code='001' 1분봉)와 ``intraday_sample``(series_key='nq_futures' 5분봉)만 읽는다
(외부 호출 없음, DB 쓰기 없음). 계산은 quant/nq_comovement.py(순수 함수). 과거 일별 corr 분포는 10분 메모리 캐시.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..market_hours import KST, is_market_closed
from ..models import IndexIntradayBar, IntradaySample
from ..quant import nq_comovement as nc
from ..quant.nq_shadow import NQ_SERIES_KEY

router = APIRouter()

_HISTORY_TTL_SECONDS = 600
_history_cache: dict[str, object] = {"ts": 0.0, "date": None, "data": None}
_history_lock = asyncio.Lock()


def _group_nq(rows) -> dict[dt.date, dict[int, float]]:
    """(UTC time, value) -> {KST 날짜: {봉 시작 분: 종가}}. 봉 시작 시각 기준."""
    out: dict[dt.date, dict[int, float]] = defaultdict(dict)
    for t, v in rows:
        k = t.astimezone(KST)
        out[k.date()][k.hour * 60 + k.minute] = float(v)
    return out


def _group_kospi(rows) -> tuple[dict[dt.date, list[tuple[int, float]]], dict[dt.date, float]]:
    """(date, HHMM, open, close) -> 날짜별 (분, 종가) 목록과 날짜별 첫 봉 시가."""
    closes: dict[dt.date, list[tuple[int, float]]] = defaultdict(list)
    opens: dict[dt.date, float] = {}
    for d, hhmm, o, c in rows:
        m = nc.hhmm_to_min(hhmm)
        if m is None or c is None:
            continue
        closes[d].append((m, float(c)))
        if m >= nc.OPEN_MIN and d not in opens and o:
            opens[d] = float(o)
    return closes, opens


def _day_pairs(kospi_bars, kospi_open, nq_day) -> list:
    k5 = nc.resample_kospi(kospi_bars)
    anchor_nq = nq_day.get(nc.OPEN_MIN - nc.BUCKET)  # 08:55 봉 종가 = 09:00 시점
    return nc.pair_returns(k5, nq_day, kospi_open, anchor_nq)


async def _history(session: AsyncSession, today: dt.date) -> dict:
    """today 이전 거래일들의 일별 corr 목록(캐시 10분)."""
    async with _history_lock:
        now = time.monotonic()
        if (
            _history_cache["data"] is not None
            and _history_cache["date"] == today
            and now - _history_cache["ts"] < _HISTORY_TTL_SECONDS
        ):
            return _history_cache["data"]  # type: ignore[return-value]
        krows = (
            await session.execute(
                select(IndexIntradayBar.date, IndexIntradayBar.time, IndexIntradayBar.open, IndexIntradayBar.close)
                .where(IndexIntradayBar.code == "001", IndexIntradayBar.date < today)
                .order_by(IndexIntradayBar.date, IndexIntradayBar.time)
            )
        ).all()
        nrows = (
            await session.execute(
                select(IntradaySample.time, IntradaySample.value).where(
                    IntradaySample.series_key == NQ_SERIES_KEY,
                    IntradaySample.time < dt.datetime.combine(today, dt.time.min, tzinfo=KST),
                )
            )
        ).all()
        closes, opens = _group_kospi(krows)
        nq = _group_nq(nrows)
        daily: list[float] = []
        for d in sorted(closes):
            if d not in nq:
                continue
            reg = nc.regression(_day_pairs(closes[d], opens.get(d), nq[d]))
            if reg is not None:
                daily.append(reg["corr"])
        data = {"daily_corrs": daily}
        _history_cache.update({"ts": now, "date": today, "data": data})
        return data


@router.get("/api/markets/nq-comovement")
async def nq_comovement_endpoint(session: AsyncSession = Depends(get_session)):
    """코스피(5분 종가)와 NQ 5분봉의 오늘 동조도.

    Returns ``{date, market_closed, as_of, nq_delay_note, today: {n, corr, beta, r2}|None, label,
    percentile, decomposition: {...}|None, overnight: {...}|None, history: {days, median, lower, upper}}``.
    ``as_of``는 코스피·NQ 모두 값이 있는 마지막 5분 시각(HH:MM, 버킷 종료 기준). 코스피 분봉이 하나도 없으면
    ``date``가 None. 과거 분포는 같은 방식으로 계산한 정규장 전체(09:00~15:30) 일별 corr이라 장중에는
    오늘 일부 구간과 비교하게 된다.
    """
    latest = (
        await session.execute(select(func.max(IndexIntradayBar.date)).where(IndexIntradayBar.code == "001"))
    ).scalar_one_or_none()
    now_kst = dt.datetime.now(KST)
    if latest is None:
        return {"date": None, "market_closed": is_market_closed(now_kst), "as_of": None}
    today = latest

    krows = (
        await session.execute(
            select(IndexIntradayBar.date, IndexIntradayBar.time, IndexIntradayBar.open, IndexIntradayBar.close)
            .where(IndexIntradayBar.code == "001", IndexIntradayBar.date >= today - dt.timedelta(days=10))
            .order_by(IndexIntradayBar.date, IndexIntradayBar.time)
        )
    ).all()
    closes, opens = _group_kospi(krows)
    prev_days = sorted(d for d in closes if d < today)
    prev = prev_days[-1] if prev_days else None

    start = dt.datetime.combine(prev or today, dt.time.min, tzinfo=KST) - dt.timedelta(days=1)
    end = dt.datetime.combine(today + dt.timedelta(days=1), dt.time.min, tzinfo=KST)
    nrows = (
        await session.execute(
            select(IntradaySample.time, IntradaySample.value).where(
                IntradaySample.series_key == NQ_SERIES_KEY, IntradaySample.time >= start, IntradaySample.time < end
            )
        )
    ).all()
    nq = _group_nq(nrows)
    nq_today = nq.get(today, {})
    history = await _history(session, today)
    corrs: list[float] = history["daily_corrs"]  # type: ignore[assignment]

    k5 = nc.resample_kospi(closes.get(today, []))
    k_open = opens.get(today)
    anchor_nq = nq_today.get(nc.OPEN_MIN - nc.BUCKET)
    pairs = nc.pair_returns(k5, nq_today, k_open, anchor_nq)
    reg = nc.regression(pairs)

    both = sorted(set(k5) & set(nq_today))
    as_of = None
    decomposition = None
    if both:
        last = both[-1]
        end_min = last + nc.BUCKET
        as_of = f"{end_min // 60:02d}:{end_min % 60:02d}"
        if reg is not None and k_open and anchor_nq:
            kc = (k5[last] / k_open - 1) * 100
            nqc = (nq_today[last] / anchor_nq - 1) * 100
            decomposition = nc.decompose(kc, nqc, reg["beta"])
            decomposition["kospi_open"] = k_open
            decomposition["kospi_last"] = k5[last]

    overnight = None
    if prev is not None:
        prev_bars = [(m, c) for m, c in closes[prev] if m <= nc.CLOSE_MIN]
        prev_close = max(prev_bars)[1] if prev_bars else None
        nq_prev = nq.get(prev, {}).get(nc.CLOSE_MIN - nc.BUCKET)  # 15:25 봉 종가 = 15:30 시점
        overnight = nc.overnight_gap(prev_close, k_open, nq_prev, anchor_nq)
        if overnight is not None:
            overnight["prev_date"] = prev.isoformat()

    closed = today < now_kst.date() or is_market_closed(now_kst)
    return {
        "date": today.isoformat(),
        "market_closed": closed,
        "as_of": as_of,
        "today": reg,
        "label": nc.label_corr(reg["corr"], corrs) if reg else None,
        "percentile": nc.percentile_of(reg["corr"], corrs) if reg and corrs else None,
        "decomposition": decomposition,
        "overnight": overnight,
        "history": nc.history_summary(corrs),
    }
