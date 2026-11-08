"""app/quant/opening_vol.py 순수 함수 단위 테스트 (PLAN.md §5.94/§5.95)."""

import datetime as dt
import math

import pytest

from app.quant import opening_vol as ov


def _bar(m, c, o=None, h=None, l=None):
    o = c if o is None else o
    return {"m": m, "o": o, "h": c if h is None else h, "l": c if l is None else l, "c": c}


def _bars(start, n, price=100.0, step=0.0):
    return [_bar(start + i, price + step * i) for i in range(n)]


def test_hhmm_to_min():
    assert ov.hhmm_to_min("0900") == 540
    assert ov.hhmm_to_min("1530") == 930


def test_realized_vol_matches_manual():
    bars = [_bar(540, 101.0), _bar(541, 102.0)]
    exp = math.sqrt((math.log(101 / 100) * 100) ** 2 + (math.log(102 / 101) * 100) ** 2)
    assert ov.realized_vol(bars, 100.0) == pytest.approx(exp)


def test_log_returns_drops_gap_but_keeps_auction_jump():
    # 09:00, 09:01, (공백) 09:33 -> 09:33 수익률 폐기
    r = ov.log_returns([_bar(540, 100), _bar(541, 101), _bar(573, 120)], 100.0)
    assert len(r) == 2
    # 15:19 -> 15:30 종가 단일가 점프는 유지
    r2 = ov.log_returns([_bar(15 * 60 + 19, 100), _bar(15 * 60 + 30, 103)], 100.0)
    assert len(r2) == 2


def test_percentile_rank_strict_less_than():
    assert ov.percentile_rank(3, [1, 2, 3, 4]) == 50.0
    assert ov.percentile_rank(10, [1, 2]) == 100.0
    assert ov.percentile_rank(0, [1, 2]) == 0.0
    assert ov.percentile_rank(1, []) is None


@pytest.mark.parametrize(
    "pct,label",
    [(0, "calm"), (39.99, "calm"), (40, "normal"), (79.99, "normal"), (80, "high"), (100, "high"), (None, None)],
)
def test_regime_label_boundaries(pct, label):
    assert ov.regime_label(pct) == label


def test_live_window_pending_ok_partial():
    bars = _bars(540, 4)  # 09:00~09:03
    assert ov.live_window(bars, 100.0, 5)["status"] == "pending"
    assert ov.live_window(bars, 100.0, 5)["rv_pct"] is None
    bars = _bars(540, 5)
    w = ov.live_window(bars, 100.0, 5)
    assert w["status"] == "ok" and w["bars"] == 5
    # 서킷브레이커 사례: 09:00~09:02 후 09:33 재개 -> 5분 윈도우는 3봉만 -> partial
    cb = _bars(540, 3) + _bars(573, 5)
    w = ov.live_window(cb, 100.0, 5)
    assert w["status"] == "partial" and w["bars"] == 3 and w["rv_pct"] is not None
    assert ov.live_window(cb, 100.0, 30)["status"] == "partial"


def test_pick_label_uses_longest_ok_window():
    ws = [
        {"minutes": 5, "status": "ok", "percentile": 90},
        {"minutes": 15, "status": "ok", "percentile": 50},
        {"minutes": 30, "status": "pending", "percentile": None},
    ]
    assert ov.pick_label(ws) == ("normal", 15)


def test_pick_label_partial_does_not_force_label():
    ws = [{"minutes": 5, "status": "partial", "percentile": 99}, {"minutes": 15, "status": "partial", "percentile": 99}]
    assert ov.pick_label(ws) == ("partial", None)
    # 5분은 완전, 15분은 부분 -> 완전 윈도우 기준
    ws = [{"minutes": 5, "status": "ok", "percentile": 10}, {"minutes": 15, "status": "partial", "percentile": 99}]
    assert ov.pick_label(ws) == ("calm", 5)


def test_pick_label_wait_and_nodata():
    assert ov.pick_label([{"minutes": 5, "status": "pending", "percentile": None}]) == ("wait", None)
    assert ov.pick_label([{"minutes": 5, "status": "ok", "percentile": None}]) == ("nodata", 5)


def test_build_daily_context_avg20_and_next():
    rows = [(dt.date(2026, 1, 1) + dt.timedelta(days=i), 100, 102, 98, 100) for i in range(25)]
    ctx = ov.build_daily_context(rows)
    assert ctx[rows[0][0]]["prev_c"] is None
    assert ctx[rows[-1][0]]["avg20"] == pytest.approx(4.0)  # (102-98)/100*100
    assert ctx["__next"]["prev_c"] == 100.0 and ctx["__next"]["avg20"] == pytest.approx(4.0)


def test_day_features_strict_excludes_gapped_window():
    full = _bars(540, 30, 100, 0.1) + _bars(570, 330, 103)
    f = ov.day_features(full, {"prev_c": 100.0, "avg20": 2.0})
    assert f["ok5"] and f["ok30"] and f["rv5"] > 0
    gapped = full[:3] + full[33:]
    f2 = ov.day_features(gapped, {"prev_c": 100.0, "avg20": 2.0})
    assert f2["ok5"] is False and f2["ok30"] is False
    assert ov.day_features(full[:10], {"prev_c": 100.0, "avg20": 2.0}) is None  # 봉 부족


def test_rv_distribution_excludes_target_and_gapped_days():
    daily_ctx = {"__next": {"prev_c": 100.0, "avg20": 2.0}}
    d1, d2, d3 = dt.date(2026, 1, 5), dt.date(2026, 1, 6), dt.date(2026, 1, 7)
    day = _bars(540, 30, 100, 0.1) + _bars(570, 330, 103)
    by_day = {d1: day, d2: day[:3] + day[33:], d3: day}
    # 일봉 컨텍스트가 없는 날은 __next로 대체되지 않으므로(과거일) 날짜별 컨텍스트를 채운다
    for d in by_day:
        daily_ctx[d] = {"prev_c": 100.0, "avg20": 2.0}
    dist = ov.rv_distribution(by_day, daily_ctx, d3)
    assert len(dist[5]) == 1 and len(dist[30]) == 1  # d1만(d2 결측, d3 대상일 제외)


def test_build_market_snapshot_waiting_before_0905():
    snap = ov.build_market_snapshot(_bars(540, 3), {"prev_c": 100.0, "avg20": 2.0}, {5: [1.0], 15: [1.0], 30: [1.0]})
    assert snap["label"] == "wait"
    assert all(w["status"] == "pending" for w in snap["windows"])


def test_build_market_snapshot_high_and_gap_warning():
    bars = [_bar(540 + i, 100 + (i % 2) * 2, o=98.0 if i == 0 else None) for i in range(30)]
    dist = {n: [0.1, 0.2, 0.3, 0.4, 0.5] for n in (5, 15, 30)}
    snap = ov.build_market_snapshot(bars, {"prev_c": 100.0, "avg20": 2.0}, dist)
    assert snap["label"] == "high" and snap["label_basis_minutes"] == 30
    assert snap["gap_pct"] == pytest.approx(-2.0)
    assert snap["range_ratio"] is not None and not snap["has_gap"]


def test_build_market_snapshot_circuit_breaker_partial():
    bars = _bars(540, 3) + _bars(573, 40)
    snap = ov.build_market_snapshot(bars, {"prev_c": 100.0, "avg20": 2.0}, {5: [0.1], 15: [0.1], 30: [0.1]})
    assert snap["label"] == "partial" and snap["has_gap"]
    assert any("공백" in w for w in snap["warnings"])


def test_build_market_snapshot_no_bars():
    assert ov.build_market_snapshot([], None, {})["label"] == "nodata"
