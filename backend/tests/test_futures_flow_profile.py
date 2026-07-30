"""Unit tests for app.quant.futures_flow_profile (PLAN.md §5.41, K200 선물
외국인/기관 순매매 가격대별 프로파일 근사).

test_volume_profile.py와 동일한 house 패턴 — 순수 함수라 DB/세션 없이 합성
bar/flow_by_date만으로 검증한다. bin 경계/분배량은 전부 손계산으로 미리 검증한 값이다.
"""

from __future__ import annotations

from app.quant import futures_flow_profile as ffp


def _bar(date: str, low: float, high: float) -> dict:
    return {"date": date, "low": low, "high": high}


# -- compute_flow_profile ----------------------------------------------------


def test_compute_flow_profile_empty_input_returns_degenerate_result():
    result = ffp.compute_flow_profile([], {}, num_bins=10)

    assert result == {"bins": [], "total_net_value": 0.0, "bar_count": 0, "num_bins": 10}


def test_compute_flow_profile_bar_without_matching_flow_entry_is_skipped_not_zero_filled():
    # "20260101" 봉은 flow_by_date에 키가 아예 없다 -> 0으로 채우지 않고 건너뛴다
    # (모듈 docstring "키가 없음 vs 정확히 0" 구분, _valid_bars_with_flow 참고).
    bars = [_bar("20260101", 0, 90), _bar("20260102", 0, 90)]
    flow_by_date = {"20260102": -30.0}  # 20260101은 의도적으로 누락

    result = ffp.compute_flow_profile(bars, flow_by_date, num_bins=3)

    assert result["bar_count"] == 1
    assert result["total_net_value"] == -30.0


def test_compute_flow_profile_negative_flow_produces_negative_bin_totals():
    # 가격구간 0~90을 3 bin([0,30),[30,60),[60,90])으로 나누고, 순매도(-30)인
    # 봉 하나가 3개 bin 전부에 걸치면 균등분배로 각 bin에 -10씩 담겨야 한다.
    bars = [_bar("20260101", 0, 90)]
    flow_by_date = {"20260101": -30.0}

    result = ffp.compute_flow_profile(bars, flow_by_date, num_bins=3)

    assert [b["net_value"] for b in result["bins"]] == [-10.0, -10.0, -10.0]
    assert result["total_net_value"] == -30.0
    assert result["bar_count"] == 1


def test_compute_flow_profile_positive_and_negative_days_net_out_per_bin():
    # 두 봉이 같은 가격구간(0~90, 3bin)에 걸치고 부호가 반대면 bin별로 상쇄된다.
    bars = [_bar("20260101", 0, 90), _bar("20260102", 0, 90)]
    flow_by_date = {"20260101": 60.0, "20260102": -30.0}

    result = ffp.compute_flow_profile(bars, flow_by_date, num_bins=3)

    # 20260101: +20씩, 20260102: -10씩 -> 합 +10씩.
    assert [b["net_value"] for b in result["bins"]] == [10.0, 10.0, 10.0]
    assert result["total_net_value"] == 30.0
    assert result["bar_count"] == 2


def test_compute_flow_profile_degenerate_single_price_range():
    bars = [_bar("20260101", 100, 100), _bar("20260102", 100, 100)]
    flow_by_date = {"20260101": 5.0, "20260102": -12.0}

    result = ffp.compute_flow_profile(bars, flow_by_date, num_bins=10)

    assert result["num_bins"] == 1
    assert result["bins"] == [
        {"price_low": 100.0, "price_high": 100.0, "price_mid": 100.0, "net_value": -7.0}
    ]
    assert result["bar_count"] == 2


def test_compute_flow_profile_skips_bars_with_invalid_price_fields():
    bars = [
        _bar("20260101", 0, 90),
        {"date": "20260102", "low": None, "high": 50},  # low 없음
        {"date": "20260103", "low": 50, "high": 10},  # high < low
    ]
    flow_by_date = {"20260101": 10.0, "20260102": 5.0, "20260103": 5.0}

    result = ffp.compute_flow_profile(bars, flow_by_date, num_bins=3)

    assert result["bar_count"] == 1
    assert result["total_net_value"] == 10.0


# -- detect_flow_levels -------------------------------------------------------


def _profile_with_bar_count(bins: list[dict], bar_count: int) -> dict:
    total_net_value = sum(b["net_value"] for b in bins)
    return {"bins": bins, "total_net_value": total_net_value, "bar_count": bar_count}


def _bins_from_values(values: list[float]) -> list[dict]:
    return [
        {"price_low": i * 10, "price_high": (i + 1) * 10, "price_mid": i * 10 + 5, "net_value": v}
        for i, v in enumerate(values)
    ]


def test_detect_flow_levels_returns_empty_when_bar_count_below_minimum():
    bins = _bins_from_values([100, -100])
    profile = _profile_with_bar_count(bins, bar_count=ffp.MIN_BARS_FOR_LEVELS - 1)

    assert ffp.detect_flow_levels(profile) == {"buy_levels": [], "sell_levels": []}


def test_detect_flow_levels_returns_empty_for_empty_profile():
    profile = {"bins": [], "total_net_value": 0.0, "bar_count": 0}

    assert ffp.detect_flow_levels(profile) == {"buy_levels": [], "sell_levels": []}


def test_detect_flow_levels_finds_buy_peak_only_when_no_negative_bins():
    # 전부 0 이상, index 2(90)만 확실한 국소최댓값. 음수 bin이 없으니 sell_levels는
    # 반드시 비어야 한다(0은 side_values>0 조건에서 걸러짐).
    bins = _bins_from_values([5, 10, 90, 10, 5])
    profile = _profile_with_bar_count(bins, bar_count=ffp.MIN_BARS_FOR_LEVELS)

    levels = ffp.detect_flow_levels(profile)

    assert levels["sell_levels"] == []
    assert len(levels["buy_levels"]) == 1
    assert levels["buy_levels"][0]["net_value"] == 90
    assert levels["buy_levels"][0]["price_mid"] == 25


def test_detect_flow_levels_finds_sell_peak_only_when_no_positive_bins():
    bins = _bins_from_values([-5, -10, -90, -10, -5])
    profile = _profile_with_bar_count(bins, bar_count=ffp.MIN_BARS_FOR_LEVELS)

    levels = ffp.detect_flow_levels(profile)

    assert levels["buy_levels"] == []
    assert len(levels["sell_levels"]) == 1
    assert levels["sell_levels"][0]["net_value"] == -90  # 부호 있는 원래 값 그대로


def test_detect_flow_levels_finds_both_sides_independently_in_different_zones():
    # index1(60)=매수 집중, index4(-90)=매도 집중 — 서로 다른 가격 구간.
    # threshold(buy)=60*0.3=18, threshold(sell, 절댓값)=90*0.3=27 — 손계산 검증.
    bins = _bins_from_values([5, 60, -5, -5, -90, -5])
    profile = _profile_with_bar_count(bins, bar_count=ffp.MIN_BARS_FOR_LEVELS)

    levels = ffp.detect_flow_levels(profile)

    assert len(levels["buy_levels"]) == 1
    assert levels["buy_levels"][0]["net_value"] == 60
    assert levels["buy_levels"][0]["price_mid"] == 15

    assert len(levels["sell_levels"]) == 1
    assert levels["sell_levels"][0]["net_value"] == -90
    assert levels["sell_levels"][0]["price_mid"] == 45


def test_detect_flow_levels_filters_weak_peaks_below_prominence_ratio():
    # buy: index1(20)은 이웃(10,10)보다 국소최댓값이지만 전체 최댓값(100)의
    # 30%=30보다 낮아 걸러진다. index2(100)만 남아야 한다.
    bins = _bins_from_values([5, 20, 100, 10, 10])
    profile = _profile_with_bar_count(bins, bar_count=ffp.MIN_BARS_FOR_LEVELS)

    levels = ffp.detect_flow_levels(profile)

    assert len(levels["buy_levels"]) == 1
    assert levels["buy_levels"][0]["net_value"] == 100


def test_detect_flow_levels_respects_max_levels_per_side_cap():
    # 매수 쪽 지그재그 3개 피크(100, 90, 80) 모두 threshold(=100*0.3=30) 통과 —
    # max_levels_per_side=2로 상한이 걸리는지 확인.
    bins = _bins_from_values([100, 1, 90, 1, 80, 1])
    profile = _profile_with_bar_count(bins, bar_count=ffp.MIN_BARS_FOR_LEVELS)

    levels = ffp.detect_flow_levels(profile, max_levels_per_side=2)

    assert [lv["net_value"] for lv in levels["buy_levels"]] == [100, 90]
