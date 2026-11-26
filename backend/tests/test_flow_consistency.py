"""quant/flow_consistency.py 순수 함수 테스트 (PLAN.md Phase 5.113)."""

from __future__ import annotations

import datetime as dt

from app.quant import flow_consistency as fc


def _pts(values, start_min=9 * 60, step=10):
    """분 단위 정수 시각(09:00부터 step분 간격)으로 곡선 점 생성."""
    return [(start_min + i * step, v) for i, v in enumerate(values)]


def test_straight_line_selling_is_consistent_sell():
    values = [-1000.0 * (i + 1) for i in range(30)]
    r = fc.analyze_curve(_pts(values))
    assert r["label"] == "일관 매도"
    assert r["down_share"] == 1.0
    assert r["rebound"] == 0.0
    assert r["final"] == -30000.0
    assert r["r2"] > 0.99
    assert r["buckets"] == 30


def test_v_shape_is_mixed():
    down = [-1000.0 * (i + 1) for i in range(15)]
    up = [down[-1] + 1000.0 * (i + 1) for i in range(15)]
    r = fc.analyze_curve(_pts(down + up))
    assert r["label"] == "혼조"
    assert r["rebound"] == 15000.0


def test_selling_with_big_rebound_is_mixed():
    # 계속 내리다 중간에 크게 반등 -> down_share는 높아도 rebound 조건 탈락
    values = [-1000.0 * (i + 1) for i in range(20)]
    values += [values[-1] + 6000.0]  # 반등
    values += [values[-1] - 1000.0 * (i + 1) for i in range(10)]
    r = fc.analyze_curve(_pts(values))
    assert r["label"] == "혼조"


def test_leading_zeros_are_ignored():
    values = [0.0] * 5 + [-1000.0 * (i + 1) for i in range(10)]
    r = fc.analyze_curve(_pts(values))
    assert r["buckets"] == 10
    assert r["label"] == "일관 매도"


def test_mirror_buying():
    values = [1000.0 * (i + 1) for i in range(12)]
    r = fc.analyze_curve(_pts(values))
    assert r["label"] == "일관 매수"
    assert r["up_share"] == 1.0
    assert r["pullback"] == 0.0


def test_too_few_points_returns_none():
    assert fc.analyze_curve(_pts([-1.0, -2.0, -3.0, -4.0, -5.0])) is None
    assert fc.analyze_curve([]) is None
    # 선행 0만 있으면 유효 버킷 없음
    assert fc.analyze_curve(_pts([0.0] * 20)) is None


def test_resample_takes_last_value_per_bucket_with_datetimes():
    base = dt.datetime(2026, 11, 20, 9, 0)
    pts = [(base + dt.timedelta(minutes=m), float(-m)) for m in range(0, 100)]
    values = fc.resample_10min(pts)
    # 09:00 버킷의 마지막 값은 09:09 -> -9 (선행 0 제거 규칙은 0 값에만 적용)
    assert values[0] == -9.0
    assert len(values) == 10


def test_history_stats():
    d = [dt.date(2026, 11, 1) + dt.timedelta(days=i) for i in range(8)]
    analyses = {}
    finals = [-100, -90, -50, -10, 10, 20, -80, 30]
    labels = ["일관 매도", "일관 매도", "일관 매도", "혼조", "혼조", "혼조", "혼조", "일관 매수"]
    for day, f, lab in zip(d, finals, labels):
        analyses[day] = {"final": float(f), "label": lab}
    nxt = {d[0]: -2.0, d[1]: 1.0, d[2]: -1.0, d[3]: 0.5, d[4]: 0.5, d[5]: 0.5, d[6]: 0.5, d[7]: 0.5}
    s = fc.history_stats(analyses, nxt)
    assert s["days"] == 8
    assert s["n"] == 3
    # finals 정렬: -100,-90,-80,-50,-10,10,20,30 -> 25% 위치 1.75 => -90 + (-80+90)*0.75 = -82.5
    assert abs(s["size_threshold"] - (-82.5)) < 1e-9
    assert s["n_large"] == 2  # -100, -90
    assert s["n_next"] == 2
    assert abs(s["next_day_avg_pct"] - (-0.5)) < 1e-9
    assert s["next_day_up_pct"] == 50.0
    assert s["baseline"]["n"] == 8
    assert abs(s["baseline"]["next_day_avg_pct"] - sum(nxt.values()) / 8) < 1e-9


def test_history_stats_empty_and_missing_next():
    assert fc.history_stats({}, {})["n"] == 0
    s = fc.history_stats({dt.date(2026, 1, 2): {"final": -5.0, "label": "일관 매도"}}, {})
    assert s["n_large"] == 1 and s["next_day_avg_pct"] is None


def test_percentile_rank():
    assert fc.percentile_rank(-100, [-100, -50, 0, 50]) == 25.0
    assert fc.percentile_rank(0, []) is None


def test_peak_trough_with_times():
    # 09:00부터 10분 간격: 상승해 10:00(인덱스 6)에서 고점, 이후 하락 -> 저점은 마지막
    values = [500.0 * (i + 1) for i in range(7)] + [3500.0 - 700.0 * (i + 1) for i in range(12)]
    r = fc.analyze_curve(_pts(values))
    assert r["peak"] == 3500.0
    assert r["peak_time"] == "10:00"
    assert r["trough"] == values[-1]
    assert r["trough_time"] == "12:00"
    assert r["final"] == r["trough"]


def test_peak_time_uses_bucket_start_and_datetime_input():
    kst = dt.timezone(dt.timedelta(hours=9))
    base = dt.datetime(2026, 11, 26, 9, 0, tzinfo=kst)
    # 같은 버킷(10:20~10:29) 안 두 점 -> 마지막 값이 버킷 값, 시각은 10:20
    pts = [(base + dt.timedelta(minutes=10 * i), 100.0 * (i + 1)) for i in range(8)]  # 09:00..10:10
    pts.append((dt.datetime(2026, 11, 26, 10, 21, tzinfo=kst), 5000.0))
    pts.append((dt.datetime(2026, 11, 26, 10, 28, tzinfo=kst), 900.0))
    pts += [(dt.datetime(2026, 11, 26, 10, 30, tzinfo=kst), 800.0)]
    r = fc.analyze_curve(pts)
    assert r["peak"] == 900.0
    assert r["peak_time"] == "10:20"
    assert r["trough"] == 100.0
    assert r["trough_time"] == "09:00"


def test_peak_trough_all_negative_curve():
    values = [-1000.0 * (i + 1) for i in range(10)]
    r = fc.analyze_curve(_pts(values))
    assert r["peak"] == -1000.0 and r["peak_time"] == "09:00"
    assert r["trough"] == -10000.0 and r["trough_time"] == "10:30"
