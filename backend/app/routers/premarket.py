"""GET /api/markets/premarket-outlook — 프리마켓 예상 시작점 (PLAN.md §5.107). 참고용, 매매 신호 아님.

밤사이 NQ 변화로 대상 거래일 NXT 08:00 시가 갭을 종목별 회귀(최근 60거래일)로 예측한다. 정의·적합은
`quant/premarket_outlook.py`, 입력 로더는 `collectors/premarket_outlook_log.py`. **DB에 쓰지 않는다**(로그는
08:15 `premarket_outlook_log` 잡이 담당). 서버 캐시 60초(warm 함수 + TTL + Lock 패턴).

세션 상태(KST): forecast(마감 후~대상일 08:00 전 / 휴장일) · opened(08:00~09:00, 예측은 08:00 직전 NQ 기준 고정 +
실제 프리 시가) · hidden(09:00~15:30, items 빈 배열). 키움·야후 조회가 실패해도 500을 내지 않고 항목 status로 표현한다
(NQ 라이브 실패 -> DB 샘플로 폴백하고 그 시각을 표기).

참고: 기존 `nasdaq-futures/live`는 온디맨드 yfinance 호출을 금지(§5.56)하지만, 이 엔드포인트는 밤사이·새벽에
DB 샘플이 비어 사용자 요청으로 5분 캐시 라이브 조회를 허용한다.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients.kiwoom import KiwoomClient
from ..collectors.nxt_minute_bars import NXT_SUFFIX, parse_krx_close_rows, parse_nxt_rows
from ..collectors.premarket_outlook_log import fetch_live_nq_bars, load_db_nq_bars, load_stock_series
from ..db import get_session
from ..market_hours import kst_now
from ..models import PremarketOutlookLog
from ..quant.premarket_outlook import (
    DISCLAIMER,
    KST,
    OUTLOOK_STOCKS,
    WEAK_NQ_PCT,
    NqSeries,
    build_samples,
    nq_change,
    outlook_for_stock,
    session_phase,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["markets"])

_CACHE_TTL_SECONDS = 60
_cache: dict[str, Any] = {"ts": 0.0, "data": None}
_cache_lock = asyncio.Lock()

# 확정된 값은 다시 안 부른다: (code, date) -> 값. 기준 종가·프리 시가는 한번 정해지면 바뀌지 않는다.
_live_close_cache: dict[tuple[str, dt.date], int] = {}
_live_open_cache: dict[tuple[str, dt.date], int] = {}


def _r2(v: float | None) -> float | None:
    return None if v is None else round(float(v), 2)


# -- 키움 라이브 조회(읽기 전용 시세 TR) — 테스트에서 monkeypatch ----------------------


async def fetch_krx_close_live(code: str, day: dt.date) -> int | None:
    """DB에 아직 없는 기준 종가(예: 오늘 15:30 이후 ~ 20:05 배치 전)를 통합 5분봉 1페이지의 15:30 봉에서 얻는다."""
    if (code, day) in _live_close_cache:
        return _live_close_cache[(code, day)]
    try:
        async with KiwoomClient() as client:
            data, _ = await client.stock_minute_chart(code, "5")
        for r in parse_krx_close_rows(code, data):
            _live_close_cache[(code, r["date"])] = r["close"]
        return _live_close_cache.get((code, day))
    except Exception as e:  # noqa: BLE001 - 키움 실패는 status로 표현
        logger.warning("premarket_outlook: 키움 기준 종가 조회 실패 %s %s: %s", code, day, e)
        return None


async def fetch_nxt_open_live(code: str, day: dt.date) -> int | None:
    """DB에 아직 없는 프리 시가(NXT 08:00 봉 시가)를 `_NX` 5분봉 1페이지에서 얻는다."""
    if (code, day) in _live_open_cache:
        return _live_open_cache[(code, day)]
    try:
        async with KiwoomClient() as client:
            data, _ = await client.stock_minute_chart(code + NXT_SUFFIX, "5")
        for r in parse_nxt_rows(code, data):
            if r["time"] == "0800":
                _live_open_cache[(code, r["date"])] = int(r["open"])
        return _live_open_cache.get((code, day))
    except Exception as e:  # noqa: BLE001
        logger.warning("premarket_outlook: 키움 NXT 시가 조회 실패 %s %s: %s", code, day, e)
        return None


# -- 표본 외 기록 집계 ----------------------------------------------------------------


def summarize_track(rows: list[Any]) -> dict:
    """로그 행들 -> {n, mae_pct, hit_rate}. mae = |error_pct| 평균, hit_rate = |NQ 변화|>=0.5%인 날 중
    예측 갭과 실제 갭의 부호가 같은 비율(해당 일이 없으면 None)."""
    done = [r for r in rows if r.error_pct is not None]
    if not done:
        return {"n": 0, "mae_pct": None, "hit_rate": None}
    mae = sum(abs(r.error_pct) for r in done) / len(done)
    strong = [
        r
        for r in done
        if r.nq_change_pct is not None
        and abs(r.nq_change_pct) >= WEAK_NQ_PCT
        and r.expected_gap_pct is not None
        and r.actual_gap_pct is not None
    ]
    hit = (sum(1 for r in strong if r.expected_gap_pct * r.actual_gap_pct > 0) / len(strong)) if strong else None
    return {"n": len(done), "mae_pct": _r2(mae), "hit_rate": None if hit is None else round(hit, 2)}


# -- 본체 -----------------------------------------------------------------------------


def _empty_item(code: str, name: str, status: str = "no_data") -> dict:
    return {
        "code": code,
        "name": name,
        "base_close": None,
        "status": status,
        "n": 0,
        "alpha_pct": None,
        "beta": None,
        "resid_std_pct": None,
        "resid_mad_pct": None,
        "expected_gap_pct": None,
        "expected_low_pct": None,
        "expected_high_pct": None,
        "expected_price": None,
        "actual_open": None,
        "actual_gap_pct": None,
        "error_pct": None,
        "track": {"n": 0, "mae_pct": None, "hit_rate": None},
    }


async def _build_item(
    session: AsyncSession, code: str, name: str, ph: dict, nq: NqSeries, ch: dict | None, track_rows: list[Any]
) -> dict:
    item = _empty_item(code, name)
    item["track"] = summarize_track(track_rows)
    base_date, target = ph["base_date"], ph["target_date"]
    closes, opens = await load_stock_series(session, code)
    base_close = closes.get(base_date) or await fetch_krx_close_live(code, base_date)
    item["base_close"] = base_close
    res = outlook_for_stock(
        build_samples(nq, closes, opens), target, ch["log_change"] if ch else None, base_close
    )
    item["status"] = res["status"]
    if "n" in res:
        item["n"] = res["n"]
    if res["status"] == "ok":
        for k in (
            "alpha_pct",
            "beta",
            "resid_std_pct",
            "resid_mad_pct",
            "expected_gap_pct",
            "expected_low_pct",
            "expected_high_pct",
        ):
            item[k] = _r2(res[k])
        item["expected_price"] = res["expected_price"]
    if ph["phase"] == "opened":
        actual = opens.get(target) or await fetch_nxt_open_live(code, target)
        if actual and base_close:
            item["actual_open"] = int(actual)
            item["actual_gap_pct"] = _r2((actual / base_close - 1) * 100)
            if res["status"] == "ok":
                item["error_pct"] = _r2((actual / base_close - 1) * 100 - res["expected_gap_pct"])
        elif actual:
            item["actual_open"] = int(actual)
    return item


async def _build_payload(session: AsyncSession) -> dict:
    now = kst_now()
    ph = session_phase(now)
    payload: dict[str, Any] = {
        "disclaimer": DISCLAIMER,
        "phase": ph["phase"],
        "base_date": ph["base_date"].isoformat(),
        "target_date": ph["target_date"].isoformat(),
        "target_date_assumed": ph["target_date_assumed"],
        "nq": None,
        "items": [],
        "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    if ph["phase"] == "hidden":
        return payload

    db_bars = await load_db_nq_bars(session)
    live = await fetch_live_nq_bars()  # 실패하면 None -> DB 샘플로 폴백
    nq = NqSeries(db_bars + (live or []))  # 같은 시각이면 live가 DB를 덮어쓴다(dict 마지막 값)
    asof = now if ph["phase"] == "forecast" else None  # opened는 08:00 직전 NQ로 고정
    ch = nq_change(nq, ph["base_date"], ph["target_date"], now=asof)
    if ch is not None:
        ref = now if ph["phase"] == "forecast" else dt.datetime.combine(ph["target_date"], dt.time(8, 0), tzinfo=KST)
        age = max(0, int((ref - ch["now_time"] - dt.timedelta(minutes=5)).total_seconds() // 60))
        payload["nq"] = {
            # 화면 표기용: 봉 시작이 아니라 봉 종료 시각(= 그 종가가 찍힌 시각)으로 내려준다(15:25 봉 -> 15:30).
            "base_time": (ch["base_time"] + dt.timedelta(minutes=5)).astimezone(KST).isoformat(),
            "base_value": _r2(ch["base_value"]),
            "now_time": (ch["now_time"] + dt.timedelta(minutes=5)).astimezone(KST).isoformat(),
            "now_value": _r2(ch["now_value"]),
            "change_pct": _r2(ch["change_pct"]),
            "weak": ch["weak"],
            "age_minutes": age,
            "delay_note": "약 10분 지연",
            "source": "live" if live else "db_fallback",
        }

    log_rows = (await session.execute(select(PremarketOutlookLog))).scalars().all()
    for code, name in OUTLOOK_STOCKS:
        try:
            item = await _build_item(
                session, code, name, ph, nq, ch, [r for r in log_rows if r.code == code]
            )
        except Exception as e:  # noqa: BLE001 - 항목 하나의 실패가 500이 되지 않게
            logger.warning("premarket_outlook: %s 항목 실패: %s", code, e)
            item = _empty_item(code, name)
        payload["items"].append(item)
    return payload


async def _warm_premarket_outlook(session: AsyncSession) -> dict:
    """60초 캐시(Lock으로 캐시 미스 중복 계산 방지)."""
    async with _cache_lock:
        if _cache["data"] is not None and time.monotonic() - _cache["ts"] < _CACHE_TTL_SECONDS:
            return _cache["data"]
        data = await _build_payload(session)
        _cache["data"], _cache["ts"] = data, time.monotonic()
        return data


@router.get("/api/markets/premarket-outlook")
async def premarket_outlook(session: AsyncSession = Depends(get_session)):
    """프리마켓 예상 시작점(참고용, 매매 신호 아님). 응답 형식은 모듈 docstring·PLAN.md §5.107."""
    return await _warm_premarket_outlook(session)
