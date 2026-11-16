"""app.quant.scorecard 순수 함수 테스트 (PLAN.md §5.109) — 합성 데이터만 쓴다(DB·네트워크 없음, 실 DB에 행을 남기지 않는다)."""

from __future__ import annotations

import datetime as dt
import math

import pytest

from app.quant import scorecard as sc
from app.quant.premarket_outlook import Sample

D0 = dt.date(2024, 1, 1)


def day(i: int) -> dt.date:
    return D0 + dt.timedelta(days=i)


def row(i, group, outcome, hit):
    return {"date": day(i), "group": group, "outcome": outcome, "hit": hit}


# -- 공통 채점 ---------------------------------------------------------------------


def test_score_group_basic_edge_and_avg():
    rows = [row(i, "g", 1.0 if i % 4 else -1.0, i % 4 != 0) for i in range(40)]  # 30 적중 / 10 미적중
    g = sc.score_group(rows, 50.0, "g", "G", "rule")
    assert (g["n"], g["hits"], g["hit_rate"]) == (40, 30, 75.0)
    assert g["baseline_rate"] == 50.0 and g["edge"] == 25.0
    assert g["avg_outcome"] == 0.5
    assert g["status"] == "ok"


def test_insufficient_below_30():
    rows = [row(i, "g", 1.0, True) for i in range(29)]
    assert sc.score_group(rows, 50.0, "g", "G", "r")["status"] == "insufficient"
    rows.append(row(29, "g", 1.0, True))
    assert sc.score_group(rows, 50.0, "g", "G", "r")["status"] == "ok"


def test_recent_is_last_20():
    # 앞 30개는 전부 미적중, 뒤 20개는 전부 적중 -> 최근 20회 적중률 100%
    rows = [row(i, "g", -1.0, False) for i in range(30)] + [row(30 + i, "g", 1.0, True) for i in range(20)]
    g = sc.score_group(rows, 50.0, "g", "G", "r")
    assert g["recent"] == {"n": 20, "hits": 20, "hit_rate": 100.0}
    assert g["hit_rate"] == 40.0


def test_unscored_group_has_n_and_avg_only():
    rows = [row(i, "m", 2.0, None) for i in range(5)]
    g = sc.score_group(rows, None, "m", "M", "r", scored=False)
    assert g["n"] == 5 and g["avg_outcome"] == 2.0
    assert g["hits"] is None and g["hit_rate"] is None and g["edge"] is None
    assert g["recent"]["hit_rate"] is None


def test_pending_rows_are_excluded():
    rows = [row(0, "g", 1.0, True), row(1, "g", None, None)]  # 둘째 날은 결과 아직 없음
    assert len(sc.drop_pending(rows)) == 1
    sig = sc.build_signal("k", "K", "c", "o", rows, [{"key": "g", "label": "G", "rule": "r"}], {"g": 50.0})
    assert sig["groups"][0]["n"] == 1
    assert sig["period"] == {"start": day(0).isoformat(), "end": day(0).isoformat(), "trading_days": 1}


def test_error_signal_shape():
    e = sc.error_signal("k", "K", "c", "o", "boom")
    assert e["error"] == "boom" and e["groups"] == []


# -- 시장 우세 판정 워크포워드 -------------------------------------------------------------


def _market(n=60, seed=7):
    """결정적 의사난수로 만든 합성 (flows, closes). flow 부호는 4일 주기로 섞는다."""
    flows, closes = [], []
    price, x = 100.0, seed
    for i in range(n):
        x = (x * 1103515245 + 12345) % (2**31)
        net = 1.0 if (x >> 8) % 3 else -1.0
        flows.append((day(i), net))
        price *= 1 + (((x >> 4) % 200) - 95) / 10000
        closes.append((day(i), price))
    return flows, closes


def test_regime_no_future_leak():
    """t 이후 데이터(수급·종가)를 통째로 바꿔도 t까지의 판정(라벨·스트릭)은 바뀌지 않는다."""
    flows, closes = _market(80)
    t = 50
    base = sc.walkforward_regime(flows, closes, warmup=10)
    # t 이후 flow 부호 반전 + 종가 왜곡
    flows2 = flows[: t + 1] + [(d, -v) for d, v in flows[t + 1 :]]
    closes2 = closes[: t + 1] + [(d, c * (3 if i % 2 else 0.2)) for i, (d, c) in enumerate(closes[t + 1 :])]
    alt = sc.walkforward_regime(flows2, closes2, warmup=10)
    for a, b in zip(base[: t + 1], alt[: t + 1]):
        assert (a["date"], a["streak"], a["label"]) == (b["date"], b["streak"], b["label"])
    # 종가가 t+1에서 바뀌면 t의 '결과'만 바뀐다(판정은 같다)
    assert base[t]["label"] == alt[t]["label"]


def test_regime_truncation_equals_prefix():
    """데이터를 t까지만 줘도 전체를 줬을 때의 t 판정과 같다(= t 시점에 알 수 있던 정보만 쓴다)."""
    flows, closes = _market(80)
    full = sc.walkforward_regime(flows, closes, warmup=10)
    for t in (20, 35, 60):
        cut = sc.walkforward_regime(flows[: t + 1], closes[: t + 1], warmup=10)
        assert cut[t]["label"] == full[t]["label"] and cut[t]["streak"] == full[t]["streak"]


def test_regime_bucket_stats_use_only_resolved_days():
    """우세 판정에 쓰는 버킷 표본은 결과가 확정된(다음 종가가 t 이전/당일인) 날뿐이다.
    첫 2일 연속 순매수 시점(t=1)에는 확정된 과거 표본이 없어 중립이다."""
    flows = [(day(i), 1.0) for i in range(5)]
    closes = [(day(i), 100.0 + i) for i in range(5)]
    wf = sc.walkforward_regime(flows, closes, warmup=0)
    assert [j["label"] for j in wf][:2] == ["중립", "중립"]  # 1매수, 2매수(과거 2매수 표본 없음)
    # 3일째(스트릭 3)에는 t=1(2매수)의 결과가 이미 확정이지만 버킷이 다르다(3+매수) -> 아직 표본 없음 -> 중립
    assert wf[2]["label"] == "중립"
    # 4일째(스트릭 4, 3+매수): t=2의 결과(3+매수, 다음 종가 확정)가 들어와 우세
    assert wf[3]["label"] == "우세"


def test_regime_warmup_excluded_and_pending_dropped():
    flows, closes = _market(60)
    sig = sc.build_regime_signal(flows, closes, warmup=40)
    ns = sum(g["n"] for g in sig["groups"])
    assert ns <= 60 - 40  # 워밍업 40일 제외
    assert sig["period"]["end"] == day(58).isoformat()  # 마지막 날(59)은 다음 날이 없어 결과 없음 -> 제외


def test_regime_hit_directions():
    # 우세: 다음날 상승이 적중 / 열세: 하락이 적중
    flows = [(day(i), 1.0) for i in range(6)] + [(day(i), -1.0) for i in range(6, 14)]
    closes = [(day(i), 100.0 + (i if i < 7 else -(i - 6))) for i in range(15)]
    sig = sc.build_regime_signal(flows, closes, warmup=0)
    by = {g["key"]: g for g in sig["groups"]}
    assert by["우세"]["n"] > 0 and by["우세"]["hit_rate"] is not None
    assert by["열세"]["hits"] == by["열세"]["n"]  # 하락장이라 열세는 전부 적중
    assert by["중립"]["hits"] is None


# -- 변동성 국면 워크포워드 ----------------------------------------------------------------


def _vol_days(n=60):
    out = []
    for i in range(n):
        rv = 0.5 + (i * 37 % 11) / 10
        out.append({"date": day(i), "rv": rv, "rest_rng": 1.0 + rv * 0.8 + (i % 3) * 0.05})
    return out


def test_vol_threshold_uses_only_prior_days():
    days = _vol_days(60)
    t = 40
    base = sc.walkforward_vol(days, min_prior=20)
    days2 = days[: t + 1] + [{**d, "rv": d["rv"] * 9, "rest_rng": d["rest_rng"] * 9} for d in days[t + 1 :]]
    alt = sc.walkforward_vol(days2, min_prior=20)
    a = next(j for j in base if j["date"] == day(t))
    b = next(j for j in alt if j["date"] == day(t))
    assert (a["label"], a["pct"], a["median"]) == (b["label"], b["pct"], b["median"])


def test_vol_warmup_and_pending():
    days = _vol_days(25)
    days[-1]["rest_rng"] = None  # 오늘: 결과 아직 없음
    wf = sc.walkforward_vol(days, min_prior=20)
    assert wf[0]["date"] == day(20)  # 이전 20일이 쌓이기 전은 판정 없음
    assert wf[-1]["above"] is None
    sig = sc.build_vol_signal(days, min_prior=20)
    assert sum(g["n"] for g in sig["groups"]) == len(wf) - 1  # 결과 없는 날 제외


def test_vol_day_stats_windows_do_not_overlap():
    """rest_rng는 09:30 이후 봉만 쓴다: 초반(09:00~09:29)에만 큰 폭이 있고 이후는 잔잔하면 rest_rng는 작다."""
    bars = []
    for m in range(540, 540 + 390):
        wide = m < 570
        h = 100 + (5 if wide and m == 545 else 0.1)
        bars.append({"m": m, "o": 100.0, "h": h, "l": 99.9, "c": 102.0 if m == 545 else 100.0})
    st = sc.vol_day_stats(bars, {"prev_c": 100.0, "avg20": 1.0})
    assert st is not None
    assert st["rest_rng"] == pytest.approx(0.2, abs=1e-6)  # 초반의 +5는 결과에 들어오지 않음
    assert st["rv"] > 0


def test_vol_day_stats_unfinished_day_has_no_outcome():
    bars = [{"m": 540 + i, "o": 100.0, "h": 100.1, "l": 99.9, "c": 100.0} for i in range(310)]  # 14:10까지
    st = sc.vol_day_stats(bars, {"prev_c": 100.0, "avg20": 1.0})
    assert st is not None and st["rest_rng"] is None


# -- 프리마켓 워크포워드 -------------------------------------------------------------------


def _samples(n=60, beta=0.5):
    out = []
    for i in range(n):
        x = math.log(1 + (((i * 7) % 13) - 6) / 300)
        noise = (((i * 5) % 7) - 3) / 2000
        out.append(Sample(date=day(i), x=x, y=beta * x + noise))
    return out


def test_premarket_no_future_leak():
    s = _samples(60)
    t = 45
    base = sc.walkforward_premarket({"A": s})
    s2 = s[: t + 1] + [Sample(date=x.date, x=x.x * 5, y=-x.y * 9) for x in s[t + 1 :]]
    alt = sc.walkforward_premarket({"A": s2})
    a = next(r for r in base if r["date"] == day(t))
    b = next(r for r in alt if r["date"] == day(t))
    assert a["pred_pct"] == b["pred_pct"] and a["within"] == b["within"]


def test_premarket_needs_20_prior_samples():
    wf = sc.walkforward_premarket({"A": _samples(30)})
    assert wf[0]["date"] == day(20)


def test_premarket_signal_groups():
    sig = sc.build_premarket_signal({"A": _samples(80), "B": _samples(80, beta=0.3)}, {"rows": 3, "days": 1})
    by = {g["key"]: g for g in sig["groups"]}
    assert by["range"]["baseline_rate"] == 68.0
    assert by["direction"]["n"] <= by["range"]["n"]  # 방향은 |NQ| >= 0.5%인 날만
    assert any("0으로 뒀을 때" in n for n in sig["notes"])


# -- 현·선 방향 / 위험 경보 -----------------------------------------------------------------


def test_positioning_groups_and_pending():
    snaps = [
        {"date": day(0), "foreign_spot_cum": 5, "foreign_futures_cum": 3, "next_day_change_rate": 1.0},
        {"date": day(1), "foreign_spot_cum": -5, "foreign_futures_cum": -3, "next_day_change_rate": -2.0},
        {"date": day(2), "foreign_spot_cum": 5, "foreign_futures_cum": -3, "next_day_change_rate": 1.0},
        {"date": day(3), "foreign_spot_cum": None, "foreign_futures_cum": 3, "next_day_change_rate": 1.0},  # 제외
        {"date": day(4), "foreign_spot_cum": 5, "foreign_futures_cum": 3, "next_day_change_rate": None},  # 결과 없음
    ]
    sig = sc.build_positioning_signal(snaps)
    by = {g["key"]: g for g in sig["groups"]}
    assert (by["both_buy"]["n"], by["both_buy"]["hits"]) == (1, 1)
    assert (by["both_sell"]["n"], by["both_sell"]["hits"]) == (1, 1)
    assert by["mixed"]["n"] == 1 and by["mixed"]["hits"] is None
    assert by["both_buy"]["baseline_rate"] == pytest.approx(2 / 3 * 100, abs=0.1)  # 알려진 3일 중 상승 2일


def test_risk_alert_groups_and_baseline():
    snaps = [
        {"date": day(0), "risk_alert_count": 1, "same_day_remaining_change_rate": -1.0, "next_day_change_rate": 2.0},
        {"date": day(1), "risk_alert_count": 0, "same_day_remaining_change_rate": -1.0, "next_day_change_rate": -1.0},
        {"date": day(2), "risk_alert_count": 2, "same_day_remaining_change_rate": 1.0, "next_day_change_rate": None},
    ]
    sig = sc.build_risk_signal(snaps)
    by = {g["key"]: g for g in sig["groups"]}
    assert (by["same_day"]["n"], by["same_day"]["hits"]) == (2, 1)
    assert (by["next_day"]["n"], by["next_day"]["hits"]) == (1, 0)  # 결과 없는 날 제외
    assert by["same_day"]["baseline_rate"] == pytest.approx(66.7, abs=0.1)  # 3일 중 2일 음수
    assert by["same_day"]["status"] == "insufficient"


def test_scalp_conversion_keeps_existing_numbers():
    agg = {
        "distinct_days": 3,
        "date_from": "2026-01-01",
        "date_to": "2026-01-03",
        "horizons": {"5m": {"n": 40, "win_rate": 75.0, "avg_change_rate": 1.2, "median_change_rate": 1.0}},
    }
    sig = sc.build_scalp_signal(agg, {"5m": [1.0] * 15 + [-1.0] * 10})
    g = sig["groups"][0]
    assert (g["n"], g["hits"], g["hit_rate"], g["baseline_rate"], g["edge"]) == (40, 30, 75.0, None, None)
    assert g["recent"] == {"n": 20, "hits": 10, "hit_rate": 50.0}  # 마지막 20개 = 앞 10개 +, 뒤 10개 -
    assert g["status"] == "ok"
