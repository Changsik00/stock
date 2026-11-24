"""quant/flow_anomaly.py 순수 함수 테스트 (PLAN.md Phase 5.117)."""

from app.quant import flow_anomaly as fa


def test_altogen_like_sell_skew():
    # 알테오젠 2026-11-24 형태: 거래대금 1,722억(172,200), 평균 약 600억, 기관 -551억, 공매도 231억 vs 69억
    r = fa.evaluate(172_200, 60_000, -55_100 + 0.0, 23_100, 6_900)
    assert r["is_candidate"] and r["direction"] == "sell"
    assert r["conditions"] == {"value_surge": True, "flow_large": True, "short_surge": True}
    assert r["condition_count"] == 3


def test_single_condition_is_not_candidate():
    r = fa.evaluate(100_000, 100_000, 100, None, None)
    assert r["condition_count"] == 0 and not r["is_candidate"]


def test_flow_requires_both_share_and_absolute():
    # 비중 10% 이상이어도 50억 미만이면 대량 수급 아님
    r = fa.evaluate(30_000, 10_000, 4_000)
    assert not r["conditions"]["flow_large"]
    # 50억 이상이어도 비중 10% 미만이면 아님
    r = fa.evaluate(100_000, 10_000, 9_000)
    assert not r["conditions"]["flow_large"]


def test_short_missing_is_excluded_not_failed():
    r = fa.evaluate(100_000, 20_000, 20_000, None, 5_000)
    assert r["short_available"] is False and r["short_ratio"] is None
    assert r["is_candidate"] and r["direction"] == "buy"  # 거래대금 급증 + 대량 수급
    r2 = fa.evaluate(100_000, 20_000, 20_000, 5_000, 0)  # 평균 0이면 판정 제외
    assert r2["short_available"] is False


def test_unevaluable_inputs():
    assert fa.evaluate(None, 1, 1) is None
    assert fa.evaluate(100, None, 1) is None
    assert fa.evaluate(100, 0, 1) is None
    assert fa.evaluate(100, 50, None) is None


def test_zero_net_has_no_direction():
    r = fa.evaluate(100_000, 20_000, 0, 10_000, 1_000)
    assert r["direction"] is None and not r["is_candidate"]


def test_threshold_boundaries_inclusive():
    r = fa.evaluate(50_000, 25_000, 5_000)  # 2.0배, 10%, 50억 정확히 경계
    assert r["conditions"]["value_surge"] and r["conditions"]["flow_large"]
