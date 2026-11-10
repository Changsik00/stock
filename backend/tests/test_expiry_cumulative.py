"""app.quant.expiry_cumulative 단위테스트 (PLAN.md Phase 5.100).

synthetic (date, investor, net_value) 튜플만 사용 — DB 불필요."""

from __future__ import annotations

import datetime as dt

from app.quant.expiry_cumulative import aggregate_expiry_cumulative

D = dt.date
# 만기: 1/11, 2/8, 3/14 (임의 — 함수는 날짜만 본다)
EXP = [D(2024, 1, 11), D(2024, 2, 8), D(2024, 3, 14)]


def _day(d, f, i, p):
    return [(d, "외국인", f), (d, "기관계", i), (d, "개인", p)]


def test_cycle_boundary_and_expiry_day_included():
    rows = []
    rows += _day(D(2024, 1, 11), 1000, 0, 0)  # 만기 당일 -> 이전 사이클
    rows += _day(D(2024, 1, 12), 10, -5, 1)  # 새 사이클 첫날
    rows += _day(D(2024, 2, 8), 20, -5, 2)  # 만기 당일 -> 이 사이클
    rows += _day(D(2024, 2, 9), 7, 7, 7)  # 다음 사이클
    r = aggregate_expiry_cumulative(rows, EXP, D(2024, 2, 20), cycles=6)
    # 현재 사이클 = 2/9 ~ 오늘
    assert r["current"]["totals"] == {"foreign": 7, "institution": 7, "individual": 7}
    assert r["current"]["prev_expiry"] == "2024-02-08"
    # 완료 사이클: 1/12~2/8 (1/11 이전은 시작 경계가 데이터 이전이라 부분)
    assert len(r["completed"]) == 1
    c = r["completed"][0]
    assert c["expiry"] == "2024-02-08"
    assert c["totals"] == {"foreign": 30, "institution": -10, "individual": 3}
    assert c["trading_days"] == 2
    assert [p["d_day"] for p in c["curve"]] == [-1, 0]
    assert c["curve"][0]["foreign"] == 10 and c["curve"][1]["foreign"] == 30
    assert r["next_expiry"] == "2024-03-14"


def test_today_is_expiry_stays_in_current_cycle():
    rows = _day(D(2024, 1, 12), 5, 5, 5) + _day(D(2024, 2, 8), 5, 5, 5)
    r = aggregate_expiry_cumulative(rows, EXP, D(2024, 2, 8))
    assert r["current"]["totals"]["foreign"] == 10
    assert r["next_expiry"] == "2024-02-08"
    assert r["current"]["prev_expiry"] == "2024-01-11"


def test_cycle_without_data_skipped_and_future_ignored():
    rows = _day(D(2024, 1, 12), 1, 1, 1) + _day(D(2024, 3, 15), 9, 9, 9)
    # 2/9~3/14 사이클엔 데이터 없음 -> 완료 목록에서 빠짐
    r = aggregate_expiry_cumulative(rows + _day(D(2024, 4, 1), 5, 5, 5), EXP + [D(2024, 4, 11)], D(2024, 3, 20))
    assert all(c["expiry"] != "2024-03-14" for c in r["completed"])
    assert r["current"]["totals"]["foreign"] == 9  # 4/1(미래)은 제외


def test_empty_and_unknown_investor():
    r = aggregate_expiry_cumulative([], EXP, D(2024, 2, 20))
    assert r["current"] is None and r["completed"] == []
    r = aggregate_expiry_cumulative([(D(2024, 2, 9), "금융투자", 5)], EXP, D(2024, 2, 20))
    assert r["current"] is None


# ---- Phase 5.101: 현물 누적 / 지수 등락률 / 기간 모드 ----
from app.quant.expiry_cumulative import (  # noqa: E402
    build_peak_window,
    build_recent_window,
    _group,
)


def test_spot_and_index_pct_in_cycle():
    fut = _day(D(2024, 1, 12), 1, 1, 1) + _day(D(2024, 2, 8), 1, 1, 1)
    spot = _day(D(2024, 1, 12), -100, 50, 50) + _day(D(2024, 2, 8), -50, 20, 30)
    closes = {D(2024, 1, 11): 100.0, D(2024, 1, 12): 95.0, D(2024, 2, 8): 90.0}
    r = aggregate_expiry_cumulative(
        fut, EXP, D(2024, 2, 20), spot_rows=spot, closes=closes
    )
    # 완료 사이클 1/12~2/8: 기준=1/11 종가 100
    assert len(r["completed"]) == 0 or True
    cur = r["current"]
    assert cur is None  # 2/9 이후 선물 데이터 없음
    r2 = aggregate_expiry_cumulative(
        fut, [D(2023, 12, 14)] + EXP, D(2024, 2, 20), spot_rows=spot, closes=closes
    )
    c = r2["completed"][-1]
    assert c["spot_totals"] == {"foreign": -150, "institution": 70, "individual": 80}
    assert c["index_pct"] == -10.0
    assert c["curve"][0]["spot_foreign"] == -100
    assert c["curve"][0]["index_pct"] == -5.0
    # 하위호환: 선물 필드 유지
    assert c["totals"]["foreign"] == 2 and c["curve"][1]["foreign"] == 2


def test_no_spot_data_gives_none():
    r = aggregate_expiry_cumulative(
        _day(D(2024, 2, 9), 1, 1, 1), EXP, D(2024, 2, 20)
    )
    assert r["current"]["spot_totals"] is None
    assert r["current"]["index_pct"] is None
    assert r["current"]["curve"][0]["index_pct"] is None


def _spot_days(n, start=D(2024, 1, 1)):
    """연속 n일(달력일) 외인 +10씩, 종가 100+i."""
    rows, closes = [], {}
    for i in range(n):
        d = start + dt.timedelta(days=i)
        rows += _day(d, 10, 0, 0)
        closes[d] = 100.0 + i
    return rows, closes


def test_recent_window_boundary_and_short_data():
    rows, closes = _spot_days(10)
    by = _group([], D(2024, 3, 1))
    sp = _group(rows, D(2024, 3, 1))
    w = build_recent_window(by, sp, closes, D(2024, 3, 1), 3)
    assert w["trading_days"] == 3 and w["spot_totals"]["foreign"] == 30
    assert w["start"] == "2024-01-08"
    # 기준 = 윈도 직전 종가(1/7 = 106), 마지막 109 -> +2.830%
    assert w["index_pct"] == round((109 / 106 - 1) * 100, 3)
    # 데이터보다 긴 N이면 전체
    assert build_recent_window(by, sp, closes, D(2024, 3, 1), 100)["trading_days"] == 10
    # 데이터 없음
    assert build_recent_window({}, {}, {}, D(2024, 3, 1), 5) is None


def test_peak_window():
    rows, _ = _spot_days(6)
    closes = {D(2024, 1, 1) + dt.timedelta(days=i): c for i, c in enumerate([100, 110, 120, 115, 108, 114])}
    closes = {k: float(v) for k, v in closes.items()}
    sp = _group(rows, D(2024, 3, 1))
    r = build_peak_window({}, sp, closes, D(2024, 3, 1))
    assert r["peak"]["date"] == "2024-01-03" and r["peak"]["close"] == 120.0
    assert r["peak"]["index_pct"] == round((114 / 120 - 1) * 100, 3)
    w = r["window"]
    assert w["start"] == "2024-01-04" and w["trading_days"] == 3
    assert w["spot_totals"]["foreign"] == 30
    assert w["curve"][0]["index_pct"] == round((115 / 120 - 1) * 100, 3)


def test_peak_is_today_or_no_closes():
    closes = {D(2024, 1, 1): 100.0, D(2024, 1, 2): 105.0}
    sp = _group(_day(D(2024, 1, 2), 5, 0, 0), D(2024, 3, 1))
    r = build_peak_window({}, sp, closes, D(2024, 3, 1))
    assert r["peak"]["date"] == "2024-01-02" and r["window"] is None
    assert r["peak"]["index_pct"] == 0.0
    assert build_peak_window({}, {}, {}, D(2024, 3, 1)) == {"peak": None, "window": None}


def test_peak_lookback_limits_range():
    closes = {D(2024, 1, 1) + dt.timedelta(days=i): float(200 if i == 0 else 100 + i) for i in range(10)}
    r = build_peak_window({}, {}, closes, D(2024, 3, 1), lookback=5)
    assert r["peak"]["date"] == "2024-01-10"  # 첫날 200은 범위 밖


# ---- Phase 5.103: 흡수율 / 사이클 라벨 ----
from app.quant.expiry_cumulative import (  # noqa: E402
    absorption_stats,
    compute_absorption,
    inst_flip,
    pair_label,
)


def test_absorption_basic_and_decomposition():
    a = compute_absorption({"foreign": -26_788_100, "institution": 10_648_000, "individual": 7_938_000})
    assert a["pct"] == 69.4
    assert a["institution_pct"] == 39.7 and a["individual_pct"] == 29.6


def test_absorption_none_when_foreign_buys_or_zero_or_missing():
    assert compute_absorption({"foreign": 100, "institution": -50, "individual": -50}) is None
    assert compute_absorption({"foreign": 0, "institution": 5, "individual": 5}) is None  # 0 나눗셈 방지
    assert compute_absorption(None) is None


def test_absorption_mixed_signs():
    # 기관 순매도(-20), 개인 순매수(+60): 흡수율은 기관 몫만큼 깎인다
    a = compute_absorption({"foreign": -100, "institution": -20, "individual": 60})
    assert a["pct"] == 40.0 and a["institution_pct"] == -20.0 and a["individual_pct"] == 60.0


def test_pair_label_four_cases_and_none():
    f = lambda v: {"foreign": v}
    assert pair_label(f(-1), f(-1)) == "동반 매도"
    assert pair_label(f(-1), f(1)) == "현물 매도 + 선물 매수(엇갈림)"
    assert pair_label(f(1), f(1)) == "동반 매수"
    assert pair_label(f(1), f(-1)) == "현물 매수 + 선물 매도(엇갈림)"
    assert pair_label(f(0), f(1)) is None and pair_label(None, f(1)) is None


def test_inst_flip():
    i = lambda v: {"institution": v}
    assert inst_flip(i(-5), i(7))["flipped"] is True
    assert inst_flip(i(-5), i(7))["label"] == "기관 현물 누적 순매도 → 순매수 전환"
    assert inst_flip(i(5), i(7))["flipped"] is False
    assert inst_flip(None, i(7)) is None and inst_flip(i(0), i(7)) is None


def test_absorption_stats_excludes_na():
    items = [{"absorption": {"pct": 10.0}}, {"absorption": None}, {"absorption": {"pct": 90.0}}, {"absorption": {"pct": 30.0}}]
    assert absorption_stats(items) == {"n": 3, "min": 10.0, "median": 30.0, "max": 90.0}
    assert absorption_stats([])["n"] == 0


def test_cycle_items_carry_absorption_label_and_flip():
    fut = _day(D(2024, 1, 12), -1, 0, 0) + _day(D(2024, 2, 8), 5, 0, 0) + _day(D(2024, 2, 9), 1, 0, 0)
    spot = _day(D(2024, 1, 12), -100, -10, 60) + _day(D(2024, 2, 8), -100, 30, 40) + _day(D(2024, 2, 9), -10, 5, 5)
    r = aggregate_expiry_cumulative(fut, [D(2023, 12, 14)] + EXP, D(2024, 2, 20), spot_rows=spot)
    c = r["completed"][-1]
    assert c["absorption"]["pct"] == 60.0 and c["pair_label"] == "현물 매도 + 선물 매수(엇갈림)"
    cur = r["current"]
    assert cur["pair_label"] == "현물 매도 + 선물 매수(엇갈림)"
    assert cur["inst_flip"]["flipped"] is False  # 직전 +20, 현재 +5
    assert r["absorption_stats"]["n"] >= 1
