"""GET /api/markets/scorecard — 지표 성적표 (PLAN.md §5.109). **표시·관찰 전용**, 매매 지시·자동매매와 무관.

대시보드 신호 6개(시장 우세 판정·외인 현선 방향·프리마켓 예상·변동성 국면·위험 경보·스켈핑 후보)를 같은 형식
(n·적중률·기준선·edge·최근 20회·status)으로 채점해 한 번에 내려준다. 채점 로직은 `quant/scorecard.py`(순수 함수),
이 파일은 DB 조회와 캐시만 맡는다. DB에 쓰지 않는다.

계산이 무거워(워크포워드 재계산) 모듈 캐시 TTL 10분 + asyncio.Lock — markets.py 라우터들의 TTL+Lock 관례와 동일.
한 신호가 실패해도 나머지는 내려가고, 실패한 신호는 `error`에 사유를 담는다.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..collectors.premarket_outlook_log import load_db_nq_bars, load_stock_series
from ..db import get_session
from ..market_hours import KST
from ..models import (
    IndexIntradayBar,
    IndexOhlcv,
    MarketFlow,
    PositioningSnapshot,
    PremarketOutlookLog,
    ScalpPick,
)
from ..quant import scorecard
from ..quant.opening_vol import build_daily_context, hhmm_to_min
from ..quant.premarket_outlook import OUTLOOK_CODES, NqSeries, build_samples
from ..quant.scalp_hitrate import HORIZONS, aggregate_scalp_hitrate

logger = logging.getLogger(__name__)
router = APIRouter(tags=["markets"])

_SCORECARD_CACHE_TTL_SECONDS = 600
_scorecard_cache: dict[str, object] = {"ts": 0.0, "data": None}
_scorecard_cache_lock = asyncio.Lock()

_KOSPI_INDEX_CODE = "001"  # 변동성 국면은 코스피 지수 1분봉(vol-regime의 코스피 쪽)


async def _positioning_rows(session: AsyncSession) -> list[dict]:
    stmt = select(
        PositioningSnapshot.date,
        PositioningSnapshot.snapshot_at,
        PositioningSnapshot.foreign_spot_cum,
        PositioningSnapshot.foreign_futures_cum,
        PositioningSnapshot.risk_alert_count,
        PositioningSnapshot.same_day_remaining_change_rate,
        PositioningSnapshot.next_day_change_rate,
    ).order_by(PositioningSnapshot.date)
    return [dict(r._mapping) for r in (await session.execute(stmt)).all()]


def _snapshot_time_note(rows: list[dict]) -> str:
    """스냅샷 찍힌 시각(KST) 분포 — 같은 시각대로 몰려 있으면 그 범위를 적는다."""
    times = [r["snapshot_at"].astimezone(KST) for r in rows if r.get("snapshot_at")]
    if not times:
        return "스냅샷 시각 정보 없음."
    hh = defaultdict(int)
    for t in times:
        hh[t.strftime("%H:%M")] += 1
    top, cnt = max(hh.items(), key=lambda kv: kv[1])
    lo, hi = min(times, key=lambda t: t.time()), max(times, key=lambda t: t.time())
    return (
        f"스냅샷(positioning_snapshot.snapshot_at)은 하루 한 번, 그날 처음 기록되는 시각에 찍힌다: "
        f"{len(times)}일 중 {cnt}일이 {top}(KST), 전체 범위 {lo.strftime('%H:%M')}~{hi.strftime('%H:%M')}."
    )


async def _regime(session: AsyncSession) -> dict:
    flows = (
        await session.execute(
            select(MarketFlow.date, MarketFlow.net_value)
            .where(MarketFlow.market == "kosdaq", MarketFlow.investor == "외국인")
            .order_by(MarketFlow.date)
        )
    ).all()
    closes = (
        await session.execute(
            select(IndexOhlcv.date, IndexOhlcv.close).where(IndexOhlcv.market == "kosdaq").order_by(IndexOhlcv.date)
        )
    ).all()
    return scorecard.build_regime_signal([tuple(r) for r in flows], [tuple(r) for r in closes])


async def _positioning(session: AsyncSession, snaps: list[dict]) -> dict:
    sig = scorecard.build_positioning_signal(snaps)
    have = sum(1 for s in snaps if s["foreign_spot_cum"] is not None and s["foreign_futures_cum"] is not None)
    sig["notes"] += [
        f"스냅샷 {len(snaps)}일 중 현물·선물 누적이 모두 기록된 날은 {have}일이다(나머지는 표본 제외).",
        _snapshot_time_note(snaps),
        "결과(next_day_change_rate)는 코스닥·코스피 지수가 아니라 SK하이닉스(000660)의 다음 거래일 등락률이다(collectors/positioning_snapshot.py fill_next_day).",
    ]
    return sig


async def _premarket(session: AsyncSession) -> dict:
    # 외부(yfinance) 호출 없이 DB 적재분만 쓴다 — 성적표는 읽기 전용·재현 가능해야 한다.
    nq = NqSeries(await load_db_nq_bars(session))
    samples = {}
    for code in OUTLOOK_CODES:
        closes, opens = await load_stock_series(session, code)
        samples[code] = build_samples(nq, closes, opens)
    cnt, days, lo, hi = (
        await session.execute(
            select(
                func.count(),
                func.count(func.distinct(PremarketOutlookLog.date)),
                func.min(PremarketOutlookLog.date),
                func.max(PremarketOutlookLog.date),
            )
        )
    ).one()
    logged = {
        "rows": cnt,
        "days": days,
        "start": lo.isoformat() if lo else None,
        "end": hi.isoformat() if hi else None,
    }
    return scorecard.build_premarket_signal(samples, logged)


async def _vol(session: AsyncSession) -> dict:
    daily_rows = (
        await session.execute(
            select(IndexOhlcv.date, IndexOhlcv.open, IndexOhlcv.high, IndexOhlcv.low, IndexOhlcv.close)
            .where(IndexOhlcv.market == "kospi")
            .order_by(IndexOhlcv.date)
        )
    ).all()
    ctx = build_daily_context([tuple(r) for r in daily_rows])
    real = [k for k in ctx if k != "__next"]
    last_daily = max(real) if real else None
    bar_rows = (
        await session.execute(
            select(
                IndexIntradayBar.date,
                IndexIntradayBar.time,
                IndexIntradayBar.open,
                IndexIntradayBar.high,
                IndexIntradayBar.low,
                IndexIntradayBar.close,
            )
            .where(IndexIntradayBar.code == _KOSPI_INDEX_CODE)
            .order_by(IndexIntradayBar.date, IndexIntradayBar.time)
        )
    ).all()
    by_day: dict[dt.date, list[dict]] = defaultdict(list)
    for d, t, o, h, l, c in bar_rows:
        if None in (o, h, l, c):
            continue
        by_day[d].append({"m": hhmm_to_min(t), "o": float(o), "h": float(h), "l": float(l), "c": float(c)})
    days = []
    for d in sorted(by_day):
        dd = ctx.get(d) or (ctx["__next"] if last_daily is not None and d > last_daily else None)
        st = scorecard.vol_day_stats(by_day[d], dd)
        if st:
            days.append({"date": d, **st})
    sig = scorecard.build_vol_signal(days)
    sig["notes"].append(f"코스피 지수(001) 1분봉 {len(by_day)}거래일 중 초반 30분 정의가 성립하는 {len(days)}일을 썼다(코스닥은 포함하지 않음 — 같은 날 두 시장을 합치면 표본이 부풀려진다).")
    return sig


async def _risk(session: AsyncSession, snaps: list[dict]) -> dict:
    sig = scorecard.build_risk_signal(snaps)
    alerts = sum(1 for s in snaps if (s["risk_alert_count"] or 0) > 0)
    sig["notes"] += [
        f"스냅샷 {len(snaps)}일 중 경보일은 {alerts}일뿐이라 표본이 매우 적다.",
        _snapshot_time_note(snaps),
        "risk_alert_count는 스냅샷 시점의 경보(서킷브레이커·사이드카·거래량 급증·수급 기울기) 개수다.",
        "결과 수익률은 지수가 아니라 SK하이닉스(000660) 기준이다. 당일 남은 시간 수익률 = 스냅샷 시점 가격 대비 마감가 근사치.",
    ]
    return sig


async def _scalp(session: AsyncSession) -> dict:
    rows = (await session.execute(select(ScalpPick).order_by(ScalpPick.date, ScalpPick.entry_time))).scalars().all()
    agg = aggregate_scalp_hitrate(rows)  # 기존 집계 그대로
    recent = {
        label: [float(getattr(r, col)) for r in rows if getattr(r, col) is not None] for label, col in HORIZONS
    }
    return scorecard.build_scalp_signal(agg, recent)


async def _compute(session: AsyncSession) -> dict:
    snaps: list[dict] = []
    snaps_err: str | None = None
    try:
        snaps = await _positioning_rows(session)
    except Exception as e:  # noqa: BLE001
        logger.warning("scorecard: positioning_snapshot 조회 실패: %s", e)
        await session.rollback()
        snaps_err = f"{type(e).__name__}: {e}"[:300]

    specs = [
        ("regime", "시장 우세 판정", lambda: _regime(session)),
        ("positioning", "외인 현·선 방향", lambda: _positioning(session, snaps)),
        ("premarket", "프리마켓 예상", lambda: _premarket(session)),
        ("vol_regime", "변동성 국면", lambda: _vol(session)),
        ("risk_alert", "위험 경보", lambda: _risk(session, snaps)),
        ("scalp", "스켈핑 후보", lambda: _scalp(session)),
    ]
    signals = []
    for key, label, fn in specs:
        try:
            if key in ("positioning", "risk_alert") and snaps_err:
                raise RuntimeError(snaps_err)
            signals.append(await fn())
        except Exception as e:  # noqa: BLE001 - 한 신호의 실패가 나머지를 막지 않는다
            logger.warning("scorecard: %s 계산 실패: %s", key, e)
            await session.rollback()
            signals.append(scorecard.error_signal(key, label, "", "", f"{type(e).__name__}: {e}"[:300]))
    return {"generated_at": dt.datetime.now(KST).isoformat(), "signals": signals}


async def _warm_scorecard(session: AsyncSession) -> dict:
    async with _scorecard_cache_lock:
        now = time.monotonic()
        cached = _scorecard_cache["data"]
        if cached is not None and (now - _scorecard_cache["ts"]) < _SCORECARD_CACHE_TTL_SECONDS:
            return cached
        payload = await _compute(session)
        _scorecard_cache["data"] = payload
        _scorecard_cache["ts"] = time.monotonic()
        return payload


@router.get("/api/markets/scorecard")
async def markets_scorecard(session: AsyncSession = Depends(get_session)) -> dict:
    """지표 성적표(PLAN.md §5.109) — 신호 6개를 같은 형식으로 채점. 표시·관찰 전용(매매 지시 아님).

    Returns ``{"generated_at", "signals": [{"key","label","claim","outcome","period":{start,end,trading_days},
    "groups":[{key,label,rule,n,hits,hit_rate,baseline_rate,edge,avg_outcome,avg_outcome_label,
    recent:{n,hits,hit_rate},status("ok"|"insufficient")}],"notes":[...],"error":null|str}]}``.
    """
    return await _warm_scorecard(session)
