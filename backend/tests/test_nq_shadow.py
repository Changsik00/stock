"""NQ 섀도 순수 로직 단위테스트 (PLAN.md §5.91) — DB/네트워크 없음."""

from __future__ import annotations

import datetime as dt
import json

from app.collectors import auto_trader
from app.collectors.nq_futures_sampler import parse_nq_bars
from app.models import AutoTradeLog
from app.quant.nq_shadow import NqIndex, build_nq_shadow, merge_nq_shadow, nq_change_pct
from app.quant.nq_trail_backtest import (
    add_ts,
    fixed_giveback,
    nq_giveback,
    run_giveback_backtest,
    trade_metrics,
    vol_giveback,
)

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 11, 2, 0, 0, tzinfo=UTC)


def _nq(n: int, start: float = 100.0, step: float = 0.1):
    """5분봉 n개, 매 봉 +step. 봉 i는 T0+5i분 시작."""
    return [(T0 + dt.timedelta(minutes=5 * i), start + step * i) for i in range(n)]


# ---------------------------------------------------------------- 변화율


def test_change_uses_only_completed_bars_and_exact_lookback():
    bars = _nq(10)  # 마지막 봉(i=9) 시작 T0+45m, 완성 T0+50m
    asof = T0 + dt.timedelta(minutes=50)
    # 15분 전 완성봉 = i=6 (종료 35m): (100.9-100.6)/100.6
    assert nq_change_pct(bars, asof, 15) == round((100.9 - 100.6) / 100.6 * 100, 4)
    # asof 49분이면 i=9는 미완성 -> i=8 기준
    assert nq_change_pct(bars, T0 + dt.timedelta(minutes=49), 15) == round((100.8 - 100.5) / 100.5 * 100, 4)


def test_change_none_when_stale_or_gap_or_empty():
    bars = _nq(10)
    assert nq_change_pct(bars, T0 + dt.timedelta(minutes=50 + 21), 15) is None  # 20분 초과 stale
    assert nq_change_pct([], T0, 15) is None
    assert nq_change_pct(bars[:2], T0 + dt.timedelta(minutes=10), 15) is None  # 기준 봉 없음
    gap = [b for i, b in enumerate(bars) if i != 6]  # 15분 전 봉 결측
    assert nq_change_pct(gap, T0 + dt.timedelta(minutes=50), 15) is None


def test_build_shadow_fields_and_empty():
    bars = _nq(10)
    sh = build_nq_shadow(bars, T0 + dt.timedelta(minutes=50))
    assert set(sh) == {"nq_change_15m_pct", "nq_change_30m_pct", "nq_last", "ts"}
    assert sh["nq_last"] == 100.9 and sh["nq_change_30m_pct"] is not None
    empty = build_nq_shadow([], T0)
    assert empty == {"nq_change_15m_pct": None, "nq_change_30m_pct": None, "nq_last": None, "ts": None}


# ---------------------------------------------------------------- merge (추가 전용)


def _dumps(d):
    return json.dumps(d, ensure_ascii=False, default=str)


def test_merge_preserves_existing_keys_and_adds_only_nq_shadow():
    snap = {"a": 1, "b": {"x": 2}}
    out = merge_nq_shadow(snap, {"nq_last": 1.0}, _dumps)
    assert out == {"a": 1, "b": {"x": 2}, "nq_shadow": {"nq_last": 1.0}}
    assert snap == {"a": 1, "b": {"x": 2}}  # 원본 불변
    assert merge_nq_shadow(None, {"nq_last": 1.0}, _dumps) is None


def test_merge_skips_when_over_limit():
    snap = {"big": "x" * 1990}
    assert merge_nq_shadow(snap, {"nq_last": 1.0, "ts": "t"}, _dumps) is snap


# ---------------------------------------------------------------- 적재 파서


def test_parse_nq_bars_filters_and_sorts():
    t1 = dt.datetime(2026, 11, 2, 1, 0, tzinfo=UTC)
    t2 = t1 + dt.timedelta(minutes=5)
    rows = parse_nq_bars(
        [
            {"time": t2, "close": 101.0},
            {"time": t1, "close": 100.0},
            {"time": t1, "close": 100.5},  # 중복 시각 -> 마지막 값
            {"time": t2 + dt.timedelta(minutes=5), "close": float("nan")},
            {"time": t2 + dt.timedelta(minutes=10), "close": None},
            {"time": t2 + dt.timedelta(minutes=15), "close": -1},
            {"time": dt.datetime(2026, 11, 2, 2, 0), "close": 5.0},  # naive
        ]
    )
    assert [r["time"] for r in rows] == [t1, t2]
    assert rows[0]["value"] == 100.5
    assert all(r["series_key"] == "nq_futures" and r["resolution_seconds"] == 300 for r in rows)


# ---------------------------------------------------------------- 백테스트 변형


def _bars(prices, date="2026-11-02", start_min=540):
    out = []
    for i, p in enumerate(prices):
        m = start_min + i
        out.append({"date": date, "time": f"{m // 60:02d}{m % 60:02d}", "close": p, "volume": 100})
    return add_ts(out)


def _flags(n, enter_at=0):
    return [i == enter_at for i in range(n)]


def test_fixed_giveback_wider_exits_later_or_never():
    # 진입 100 -> 101.5(전환) -> 고점 102 -> 101.4(고점 대비 -0.59%) -> 100.6
    prices = [100, 101.5, 102, 101.4, 100.6]
    bars = _bars(prices)
    narrow = run_giveback_backtest(bars, _flags(5), fixed_giveback(0.5))
    wide = run_giveback_backtest(bars, _flags(5), fixed_giveback(1.0))
    assert narrow[0]["exit_price"] == 101.4 and narrow[0]["exit_reason"] == "exit_trail"
    assert wide[0]["exit_price"] == 100.6  # 1.0%: 100.98 이하에서 청산
    m = trade_metrics(narrow, bars, rebound_window=10)
    assert m["n"] == 1 and m["early_exit_pct"] == 0.0 and m["avg_hold_min"] == 3


def test_stop_loss_unchanged_by_giveback():
    bars = _bars([100, 98.4])
    for g in (0.3, 1.2):
        t = run_giveback_backtest(bars, _flags(2), fixed_giveback(g))
        assert t[0]["exit_reason"] == "stop_loss"


def test_early_exit_ratio_detects_rebound():
    prices = [100, 101.5, 102, 101.4, 101.9, 102.2]  # 101.4 청산 후 +0.79%
    bars = _bars(prices)
    t = run_giveback_backtest(bars, _flags(6), fixed_giveback(0.5))
    assert trade_metrics(t, bars)["early_exit_pct"] == 100.0


def test_vol_giveback_clamped_and_scales():
    calm = _bars([100 + 0.01 * (i % 2) for i in range(30)])
    wild = _bars([100 + 0.5 * (i % 2) for i in range(30)])
    fn = vol_giveback(k=4, n=20, lo=0.3, hi=1.2)
    assert fn(29, calm) == 0.3  # 하한
    assert fn(29, wild) == 1.2  # 상한
    mid = _bars([100 + 0.1 * (i % 2) for i in range(30)])
    assert 0.3 < vol_giveback(4, 20, 0.0, 9.9)(29, mid) < 9.9
    assert fn(2, calm) == 0.3  # 표본 부족 -> lo


def test_nq_giveback_widens_only_when_rising_and_placebo_lag():
    # NQ: KST 2026-11-02 09:00 이전 구간 상승 -> 09:30 부근 조회 시 > 0
    t0 = dt.datetime(2026, 11, 2, 0, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
    rising = NqIndex([(t0 + dt.timedelta(minutes=5 * i), 100 + i) for i in range(30)])
    falling = NqIndex([(t0 + dt.timedelta(minutes=5 * i), 100 - i) for i in range(30)])
    bars = _bars([100] * 3, start_min=9 * 60 + 5)  # ts = 09:06..
    # 09:06 까지의 NQ는 t0(09:00? t0=00:00 KST) 기준이라 stale -> 폭 base
    assert nq_giveback(rising, 15, 0.5, 0.8)(0, bars) == 0.5
    bars2 = _bars([100] * 3, start_min=1 * 60 + 10)  # 01:11 -> NQ 완성봉 충분
    assert nq_giveback(rising, 15, 0.5, 0.8)(0, bars2) == 0.8
    assert nq_giveback(falling, 15, 0.5, 0.8)(0, bars2) == 0.5
    assert nq_giveback(rising, 15, 0.5, 0.8, lag_days=7)(0, bars2) == 0.5  # 7일 전 데이터 없음


# ---------------------------------------------------------------- _log 섀도 기록(판정 무영향)


class _FakeSession:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


async def test_log_adds_nq_shadow_only_for_exit_and_trail_events(monkeypatch):
    from app.collectors import nq_futures_sampler

    async def fake_shadow(session, asof=None):
        return {"nq_change_15m_pct": 0.1, "nq_change_30m_pct": None, "nq_last": 1.0, "ts": "t"}

    monkeypatch.setattr(nq_futures_sampler, "get_nq_shadow", fake_shadow)
    auto_trader._last_log_by_code.clear()
    s = _FakeSession()
    snap = {"k": 1}
    for ev, code in (("exit_trail", "T1"), ("trail_activate", "T2"), ("entry", "T3"), ("error", "T4")):
        await auto_trader._log(s, event_type=ev, code=code, price=1.0, reason="r", signal_snapshot=dict(snap))
    got = {o.event_type: json.loads(o.signal_snapshot) for o in s.added}
    assert got["exit_trail"] == {"k": 1, "nq_shadow": {"nq_change_15m_pct": 0.1, "nq_change_30m_pct": None, "nq_last": 1.0, "ts": "t"}}
    assert "nq_shadow" in got["trail_activate"]
    assert got["entry"] == {"k": 1} and got["error"] == {"k": 1}
    auto_trader._last_log_by_code.clear()


async def test_log_survives_shadow_failure_and_respects_2000_limit(monkeypatch):
    from app.collectors import nq_futures_sampler

    async def boom(session, asof=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(nq_futures_sampler, "get_nq_shadow", boom)
    auto_trader._last_log_by_code.clear()
    s = _FakeSession()
    await auto_trader._log(s, event_type="exit_trail", code="T5", price=1.0, reason="r", signal_snapshot={"k": 1})
    assert json.loads(s.added[0].signal_snapshot) == {"k": 1}  # 원본 그대로 기록

    async def ok(session, asof=None):
        return {"nq_change_15m_pct": 0.1, "nq_change_30m_pct": 0.2, "nq_last": 1.0, "ts": "t"}

    monkeypatch.setattr(nq_futures_sampler, "get_nq_shadow", ok)
    big = {"pad": "x" * 1960}
    await auto_trader._log(s, event_type="exit_trail", code="T6", price=1.0, reason="r", signal_snapshot=big)
    out = s.added[1].signal_snapshot
    assert len(out) <= 2000 and json.loads(out) == big  # 초과 시 추가 생략 -> 유효 JSON 유지
    auto_trader._last_log_by_code.clear()


def test_signal_snapshot_column_limit_is_2000():
    assert AutoTradeLog.signal_snapshot.property.columns[0].type.length == 2000
