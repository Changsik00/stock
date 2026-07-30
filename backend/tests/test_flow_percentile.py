"""app/quant/flow_percentile.py 순수 함수 단위테스트 (PLAN.md §5.38-2).

전부 DB/네트워크 무관 — 합성 데이터로 손계산한 기대값과 비교한다
(test_sector_rotation.py/test_quant_screener.py와 동일한 스타일).
"""

from __future__ import annotations

from app.quant.flow_percentile import (
    DEFAULT_TIER_COUNT,
    MIN_SAMPLES_PER_TIER,
    compute_flow_percentiles,
)


def _row(code, market, market_value_million, flow_net_value):
    return {
        "code": code,
        "market": market,
        "market_value_million": market_value_million,
        "flow_net_value": flow_net_value,
    }


def test_tier_assignment_by_market_cap_rank():
    """8개 종목, tier_count=4 -> 2개씩 정확히 4개 tier로 시총 내림차순 분류."""
    rows = [
        _row("A", "kospi", 800, 0),
        _row("B", "kospi", 700, 0),
        _row("C", "kospi", 600, 0),
        _row("D", "kospi", 500, 0),
        _row("E", "kospi", 400, 0),
        _row("F", "kospi", 300, 0),
        _row("G", "kospi", 200, 0),
        _row("H", "kospi", 100, 0),
    ]
    result = compute_flow_percentiles(rows, tier_count=4)
    kospi = result["kospi"]
    assert kospi["reason"] is None
    assert kospi["sample_size"] == 8

    by_code = {r["code"]: r for r in kospi["results"]}
    assert by_code["A"]["tier"] == 1 and by_code["B"]["tier"] == 1
    assert by_code["C"]["tier"] == 2 and by_code["D"]["tier"] == 2
    assert by_code["E"]["tier"] == 3 and by_code["F"]["tier"] == 3
    assert by_code["G"]["tier"] == 4 and by_code["H"]["tier"] == 4
    assert all(r["tier_size"] == 2 for r in kospi["results"])


def test_tier_sizes_distribute_remainder_to_larger_cap_tiers():
    """n=10, tier_count=4 -> remainder=2 -> [3, 3, 2, 2] (시총이 큰 tier부터 +1)."""
    rows = [_row(f"S{i}", "kospi", 1000 - i, 0) for i in range(10)]
    result = compute_flow_percentiles(rows, tier_count=4)
    sizes_by_tier: dict[int, int] = {}
    for r in result["kospi"]["results"]:
        sizes_by_tier[r["tier"]] = r["tier_size"]
    assert sizes_by_tier == {1: 3, 2: 3, 3: 2, 4: 2}


def test_percentile_rank_formula_hand_verified():
    """단일 tier(4개, tier_count=1)로 표준 percentile rank 공식을 손계산 검증.

    flow_net_value = [5, 10, 20, 20] 일 때:
    - 5: below=0, equal=1 -> (0+0.5)/4*100 = 12.5
    - 10: below=1, equal=1 -> (1+0.5)/4*100 = 37.5
    - 20(두 개 다): below=2, equal=2 -> (2+1)/4*100 = 75.0
    """
    rows = [
        _row("X5", "kospi", 100, 5),
        _row("X10", "kospi", 90, 10),
        _row("X20a", "kospi", 80, 20),
        _row("X20b", "kospi", 70, 20),
    ]
    result = compute_flow_percentiles(rows, tier_count=1)
    kospi = result["kospi"]
    assert kospi["reason"] is None
    by_code = {r["code"]: r["percentile"] for r in kospi["results"]}
    assert by_code["X5"] == 12.5
    assert by_code["X10"] == 37.5
    assert by_code["X20a"] == 75.0
    assert by_code["X20b"] == 75.0
    # tier 전체 percentile 평균은 정확히 50이어야 한다(동점 보정 공식의 성질).
    assert sum(by_code.values()) / len(by_code) == 50.0


def test_per_market_separation_kosdaq_never_compared_to_kospi():
    """KOSDAQ 종목의 시총이 KOSPI 전 종목보다 훨씬 커도, 두 시장은 완전히
    분리된 tier 분류를 받는다 — §5.19 "시총 편향" 버그 클래스 재발 방지 검증."""
    kospi_rows = [_row(f"KP{i}", "kospi", 100 - i, 0) for i in range(8)]  # 시총 100~93
    kosdaq_rows = [_row(f"KQ{i}", "kosdaq", 10_000_000 - i, 0) for i in range(8)]  # 시총 훨씬 큼

    result = compute_flow_percentiles(kospi_rows + kosdaq_rows, tier_count=4)

    assert set(result.keys()) == {"kospi", "kosdaq"}
    kospi_codes = {r["code"] for r in result["kospi"]["results"]}
    kosdaq_codes = {r["code"] for r in result["kosdaq"]["results"]}
    assert kospi_codes == {f"KP{i}" for i in range(8)}
    assert kosdaq_codes == {f"KQ{i}" for i in range(8)}
    # KOSPI 쪽 tier 1은 여전히 KOSPI 자기 자신 안에서 가장 큰 두 종목(KP0, KP1)이지,
    # KOSDAQ 종목이 섞여 들어오지 않는다.
    kospi_tier1 = {r["code"] for r in result["kospi"]["results"] if r["tier"] == 1}
    assert kospi_tier1 == {"KP0", "KP1"}


def test_missing_market_value_or_flow_excluded_silently():
    """market_value_million/flow_net_value 중 하나라도 None이면 그 행은
    조용히 제외된다(0으로 채우지 않음 — §5.19/screener.py 관례). flow_net_value=0
    (진짜 0)은 None과 달리 제외되지 않아야 한다는 점도 함께 확인한다."""
    rows = [
        _row("A", "kospi", 100, 10),
        _row("B", "kospi", None, 10),  # market_value 없음 -> 제외
        _row("C", "kospi", 90, None),  # flow 없음 -> 제외
        _row("D", "kospi", 80, 5),
        _row("E", "kospi", 70, 3),
        _row("F", "kospi", 60, 1),
        _row("G", "kospi", 50, 0),  # flow=0(진짜 0) -> 제외되면 안 됨
        _row("H", "kospi", 40, -1),
    ]
    result = compute_flow_percentiles(rows, tier_count=2)
    kospi = result["kospi"]
    assert kospi["reason"] is None
    assert kospi["sample_size"] == 6  # B, C 제외
    codes = {r["code"] for r in kospi["results"]}
    assert codes == {"A", "D", "E", "F", "G", "H"}


def test_insufficient_sample_returns_honest_reason_not_crash():
    """tier_count * MIN_SAMPLES_PER_TIER 미만이면 표본 부족으로 정직하게
    실패한다 — results는 빈 리스트, reason이 채워짐(크래시 없음)."""
    rows = [_row("A", "kospi", 100, 10), _row("B", "kospi", 90, 5)]
    result = compute_flow_percentiles(rows, tier_count=DEFAULT_TIER_COUNT)
    kospi = result["kospi"]
    assert kospi["reason"] is not None
    assert kospi["results"] == []
    assert kospi["sample_size"] == 2
    assert kospi["tier_count"] == DEFAULT_TIER_COUNT


def test_empty_rows_returns_empty_dict():
    assert compute_flow_percentiles([]) == {}


def test_exactly_at_minimum_sample_threshold_succeeds():
    """정확히 min_sample(tier_count * MIN_SAMPLES_PER_TIER)이면 성공해야 한다
    (경계값 테스트 — off-by-one 방지)."""
    tier_count = 2
    n = tier_count * MIN_SAMPLES_PER_TIER
    rows = [_row(f"S{i}", "kospi", 100 - i, i) for i in range(n)]
    result = compute_flow_percentiles(rows, tier_count=tier_count)
    assert result["kospi"]["reason"] is None
    assert result["kospi"]["sample_size"] == n
