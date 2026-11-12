"""프리마켓 예상 시작점 표본 외 기록 + 공용 입력 로더 (PLAN.md §5.107) — 참고용, 매매 입력 아님.

**로그 잡**(`premarket_outlook_log`, 평일 08:15 KST — 08:12 NXT 봉 수집 뒤): 그날 08:00 직전 NQ와 **그날 이전
데이터로만 적합한** 파라미터로 예측을 만들고, 실제 프리 시가(NXT 08:00 봉 시가)와 함께 upsert한다.
실제 시가가 없으면(휴장 등) 건너뛰고, 예측 status가 ok가 아닌 종목도 건너뛴다. 과거분 백필은 하지 않는다 —
로그는 "실제로 그 시점에 낼 수 있었던 예측"만 담는다(과거 성능은 워크포워드 검증으로 따로 본다).

이 모듈은 API(`routers/premarket.py`)와 공유하는 DB 로더·NQ 라이브 조회도 둔다.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..krx_calendar import is_krx_closed
from ..market_hours import kst_now
from ..models import IntradaySample, KrxRegularClose, NxtIntradayBar, PremarketOutlookLog
from ..quant.nq_shadow import NQ_SERIES_KEY
from ..quant.premarket_outlook import (
    OUTLOOK_CODES,
    NqSeries,
    build_samples,
    nq_change,
    outlook_for_stock,
    prev_open_day,
)
from .base import REGISTRY

logger = logging.getLogger(__name__)

NQ_LIVE_TTL_SECONDS = 300  # yfinance NQ=F 5분봉 직접 조회 캐시(5분)
NQ_LIVE_FAIL_TTL_SECONDS = 60  # 실패는 짧게만 기억(연타 방지)
_nq_live_cache: dict[str, Any] = {"ts": 0.0, "bars": None, "ok": False}
_nq_live_lock = asyncio.Lock()


async def fetch_live_nq_bars() -> list[tuple[dt.datetime, float]] | None:
    """yfinance NQ=F 5분봉(최근 5일, 약 10분 지연)을 직접 조회(기존 샘플러가 쓰는
    `us_indices.fetch_nasdaq_futures_5m_history` 재사용). 5분 캐시, 실패는 None(호출측이 DB로 폴백)."""
    async with _nq_live_lock:
        age = time.monotonic() - _nq_live_cache["ts"]
        if _nq_live_cache["ts"] and age < (NQ_LIVE_TTL_SECONDS if _nq_live_cache["ok"] else NQ_LIVE_FAIL_TTL_SECONDS):
            return _nq_live_cache["bars"]
        try:
            from ..clients import us_indices  # 지연 import — yfinance 로드는 기동 경로에서 뺀다

            raw = await asyncio.to_thread(us_indices.fetch_nasdaq_futures_5m_history, "5d")
            bars = [(b["time"], float(b["close"])) for b in raw if isinstance(b.get("time"), dt.datetime)]
            _nq_live_cache.update(ts=time.monotonic(), bars=bars or None, ok=bool(bars))
        except Exception as e:  # noqa: BLE001 - 야후 실패는 폴백으로 처리
            logger.warning("premarket_outlook: yfinance NQ 조회 실패(DB 폴백): %s", e)
            _nq_live_cache.update(ts=time.monotonic(), bars=None, ok=False)
        return _nq_live_cache["bars"]


async def load_db_nq_bars(session: AsyncSession) -> list[tuple[dt.datetime, float]]:
    """intraday_sample(nq_futures, 5분봉) 전체 -> (봉 시작 UTC, 종가)."""
    stmt = (
        select(IntradaySample.time, IntradaySample.value)
        .where(IntradaySample.series_key == NQ_SERIES_KEY, IntradaySample.resolution_seconds == 300)
        .order_by(IntradaySample.time)
    )
    return [(t, float(v)) for t, v in (await session.execute(stmt)).all()]


async def load_stock_series(session: AsyncSession, code: str) -> tuple[dict[dt.date, int], dict[dt.date, int]]:
    """종목의 (날짜->KRX 정규장 종가, 날짜->NXT 08:00 봉 시가)."""
    closes = {
        d: int(c)
        for d, c in (
            await session.execute(select(KrxRegularClose.date, KrxRegularClose.close).where(KrxRegularClose.code == code))
        ).all()
    }
    opens = {
        d: int(o)
        for d, o in (
            await session.execute(
                select(NxtIntradayBar.date, NxtIntradayBar.open).where(
                    NxtIntradayBar.code == code, NxtIntradayBar.time == "0800", NxtIntradayBar.open > 0
                )
            )
        ).all()
    }
    return closes, opens


def _to_row(day: dt.date, code: str, res: dict, nq_pct: float, base_close: int, actual_open: int) -> dict:
    actual_gap = (actual_open / base_close - 1) * 100
    return {
        "date": day,
        "code": code,
        "nq_change_pct": nq_pct,
        "expected_gap_pct": res["expected_gap_pct"],
        "resid_std_pct": res["resid_std_pct"],
        "n": res["n"],
        "alpha_pct": res["alpha_pct"],
        "beta": res["beta"],
        "base_close": base_close,
        "actual_open": actual_open,
        "actual_gap_pct": actual_gap,
        "error_pct": actual_gap - res["expected_gap_pct"],
    }


async def collect_premarket_outlook_log(session: AsyncSession, target_date: dt.date | None = None) -> tuple[int, str | None]:
    """오늘(target_date) 예측을 실제 프리 시가와 함께 upsert. 반환=(적재 종목 수, 메모). commit은 run_job이."""
    day = target_date or kst_now().date()
    if is_krx_closed(day):
        return 0, "휴장일 — 건너뜀"
    base_date = prev_open_day(day, is_krx_closed)

    bars = await load_db_nq_bars(session)
    live = await fetch_live_nq_bars()  # 실패하면 None — DB 샘플만으로 진행
    nq = NqSeries(bars + (live or []))
    ch = nq_change(nq, base_date, day)  # 대상일 07:55 이전 마지막 봉(고정)
    if ch is None:
        return 0, "NQ 변화 계산 불가 — 건너뜀"

    saved, skipped = 0, []
    for code in OUTLOOK_CODES:
        try:
            async with session.begin_nested():
                closes, opens = await load_stock_series(session, code)
                actual_open, base_close = opens.get(day), closes.get(base_date)
                if not actual_open or not base_close:
                    skipped.append(f"{code}(시가/기준종가 없음)")
                    continue
                samples = build_samples(nq, closes, opens)  # 적합은 fit_for_target이 day 이전만 쓴다
                res = outlook_for_stock(samples, day, ch["log_change"], base_close)
                if res["status"] != "ok":
                    skipped.append(f"{code}({res['status']})")
                    continue
                row = _to_row(day, code, res, ch["change_pct"], base_close, actual_open)
                stmt = pg_insert(PremarketOutlookLog).values(row)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[PremarketOutlookLog.date, PremarketOutlookLog.code],
                    set_={k: stmt.excluded[k] for k in row if k not in ("date", "code")},
                )
                await session.execute(stmt)
                saved += 1
        except Exception as e:  # noqa: BLE001 - 종목 하나의 실패가 나머지에 번지지 않게
            skipped.append(f"{code}(오류 {e})")
            logger.warning("premarket_outlook_log: %s 실패(무시): %s", code, e)
    return saved, ("건너뜀: " + ", ".join(skipped))[:500] if skipped else None


REGISTRY["premarket_outlook_log"] = collect_premarket_outlook_log
