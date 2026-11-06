"""트레일 반납폭 변형 백테스트 — 고정 격자 / 변동성 비례 / NQ 보조 (PLAN.md §5.91).

**오프라인 분석 전용 — 실거래 경로에서 import되지 않는다.** 진입(`decide_idle_action`
+ 골든/거래량 룩백), 손절, holding->trailing 전환은 `auto_trade_intraday_backtest.py`
와 인자 하나 바뀌지 않게 동일한 함수를 쓰고, 트레일 청산 조건만
``current <= peak * (1 - giveback/100)``에서 giveback을 변형별로 달리한다
(``giveback_fn``). 진입 신호는 상태(포지션 유무)와 무관하게 봉마다 결정되므로
한 번만 미리 계산해(``precompute_entry_flags``) 모든 변형이 공유한다.
"""

from __future__ import annotations

import datetime as dt
import statistics
from typing import Any, Callable

from ..collectors.auto_trader import _golden_cross_in_lookback, _volume_spike_in_lookback
from .auto_trade_backtest import _summarize
from .auto_trade_rules import (
    decide_idle_action,
    evaluate_stop_loss,
    evaluate_trail_activate,
    next_peak_price,
)
from .nq_shadow import NqIndex
from .signals import moving_average_cross, volume_spike

KST = dt.timezone(dt.timedelta(hours=9))

# giveback_fn(i, bars) -> 해당 봉에서 허용하는 고점 대비 반납폭(%)
GivebackFn = Callable[[int, list[dict]], float]


def add_ts(bars: list[dict]) -> list[dict]:
    """각 봉에 ``ts``(KST aware, 봉 라벨 시각 + 1분 = 종가 확정 시각)를 추가한 복사본."""
    out = []
    for b in bars:
        d = dt.date.fromisoformat(b["date"]) if isinstance(b["date"], str) else b["date"]
        hh, mm = int(b["time"][:2]), int(b["time"][2:4])
        ts = dt.datetime.combine(d, dt.time(hh, mm), tzinfo=KST) + dt.timedelta(minutes=1)
        out.append({**b, "ts": ts})
    return out


def precompute_entry_flags(bars: list[dict]) -> list[bool]:
    """idle일 때 i번째 봉에서 진입 판정이 'enter'인지 — 라이브/기존 백테스트와 동일 규칙."""
    flags = []
    for i in range(1, len(bars) + 1):
        window = bars[:i]
        ma_cross = moving_average_cross(window)
        vspike = volume_spike(window)
        golden = ma_cross["state"] == "golden" or _golden_cross_in_lookback(window)
        spike = bool(vspike["is_spike"]) or _volume_spike_in_lookback(window)
        flags.append(decide_idle_action("golden" if golden else "none", spike)["action"] == "enter")
    return flags


def run_giveback_backtest(bars: list[dict], entry_flags: list[bool], giveback_fn: GivebackFn) -> list[dict]:
    """트레이드 리스트. 각 트레이드: 기존 필드 + ``entry_idx``/``exit_idx``/``hold_bars``."""
    trades: list[dict] = []
    status = "idle"
    entry_price = entry_idx = peak = None

    def close_trade(i: int, reason: str) -> None:
        nonlocal status, entry_price, entry_idx, peak
        px = float(bars[i]["close"])
        trades.append(
            {
                "entry_date": bars[entry_idx]["date"],
                "entry_time": bars[entry_idx]["time"],
                "entry_price": entry_price,
                "exit_date": bars[i]["date"],
                "exit_time": bars[i]["time"],
                "exit_price": px,
                "exit_reason": reason,
                "pct_return": round((px - entry_price) / entry_price * 100, 4),
                "entry_idx": entry_idx,
                "exit_idx": i,
                "hold_bars": i - entry_idx,
            }
        )
        status, entry_price, entry_idx, peak = "idle", None, None, None

    for i, bar in enumerate(bars):
        px = float(bar["close"])
        if status == "idle":
            if entry_flags[i]:
                status, entry_price, entry_idx, peak = "holding", px, i, None
            continue
        if evaluate_stop_loss(entry_price, px):
            close_trade(i, "stop_loss")
            continue
        if status == "holding":
            if evaluate_trail_activate(entry_price, px):
                status, peak = "trailing", px
            continue
        peak = next_peak_price(peak if peak is not None else entry_price, px)
        if px <= peak * (1 - giveback_fn(i, bars) / 100):
            close_trade(i, "exit_trail")
    return trades


# ---------------------------------------------------------------- 변형(giveback_fn 팩토리)


def fixed_giveback(g: float) -> GivebackFn:
    return lambda i, bars: g


def _minute_returns_pct(bars: list[dict], i: int, n: int) -> list[float]:
    """i번째 봉까지 최근 n개 1분 수익률(%) — 같은 거래일 안에서만(일 경계 갭 제외)."""
    out = []
    for j in range(i, max(i - n, 0), -1):
        if bars[j]["date"] != bars[j - 1]["date"]:
            break
        p0 = float(bars[j - 1]["close"])
        if p0 > 0:
            out.append((float(bars[j]["close"]) - p0) / p0 * 100)
    return out


def vol_giveback(k: float, n: int, lo: float = 0.3, hi: float = 1.2) -> GivebackFn:
    """giveback = clamp(k * 최근 n분 1분 수익률 표준편차(%), lo, hi). 표본 <5개면 lo."""

    def fn(i: int, bars: list[dict]) -> float:
        rets = _minute_returns_pct(bars, i, n)
        if len(rets) < 5:
            return lo
        return min(hi, max(lo, k * statistics.pstdev(rets)))

    return fn


def nq_giveback(
    nq: NqIndex, minutes: int, base: float = 0.5, wide: float = 0.8, lag_days: int = 0
) -> GivebackFn:
    """NQ ``minutes``분 변화율 > 0이면 wide, 아니면(또는 값 없음) base.
    ``lag_days``>0이면 asof를 그만큼 과거로 밀어 조회한다(플라시보: 다른 날의 NQ)."""

    def fn(i: int, bars: list[dict]) -> float:
        asof = bars[i]["ts"] - dt.timedelta(days=lag_days)
        ch = nq.change_pct(asof, minutes)
        return wide if (ch is not None and ch > 0) else base

    return fn


# ---------------------------------------------------------------- 지표


def trade_metrics(trades: list[dict], bars: list[dict], rebound_window: int = 30, rebound_pct: float = 0.5) -> dict[str, Any]:
    """_summarize + 거래당 평균, 평균 보유(분봉 수), 최대낙폭(누적합 곡선, %p),
    조기청산 비율(트레일 청산 후 ``rebound_window``봉 내(같은 날) 종가가 청산가 대비
    ``rebound_pct``% 이상 더 오른 비율)."""
    s = _summarize(trades)
    n = len(trades)
    s["avg_trade_pct"] = round(s["cumulative_return_pct"] / n, 4) if n else None
    s["avg_hold_min"] = round(sum(t["hold_bars"] for t in trades) / n, 1) if n else None
    eq = peak = mdd = 0.0
    for t in trades:
        eq += t["pct_return"]
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    s["max_drawdown_pct"] = round(mdd, 4)
    trail = [t for t in trades if t["exit_reason"] == "exit_trail"]
    early = 0
    for t in trail:
        i = t["exit_idx"]
        fut = [
            float(b["close"])
            for b in bars[i + 1 : i + 1 + rebound_window]
            if b["date"] == bars[i]["date"]
        ]
        if fut and max(fut) >= t["exit_price"] * (1 + rebound_pct / 100):
            early += 1
    s["n_trail_exit"] = len(trail)
    s["early_exit_pct"] = round(early / len(trail) * 100, 1) if trail else None
    return s
