"""Unit tests for app.quant.volume_profile (PLAN.md §5.34, 거래량 프로파일 근사
+ 지지/저항 후보 감지).

volume_surge.py 테스트(test_volume_surge.py)와 동일한 house 패턴 — 순수 함수라
DB/세션 없이 합성 OHLCV bar 리스트(list[dict])만으로 검증한다. bin 경계/분배량은
전부 손계산으로 미리 검증해 둔 값이다.
"""

from __future__ import annotations

from app.quant import volume_profile


def _bar(low: float, high: float, volume: float) -> dict:
    return {"low": low, "high": high, "volume": volume}


# -- compute_volume_profile -------------------------------------------------


def test_compute_volume_profile_empty_input_returns_degenerate_result():
    result = volume_profile.compute_volume_profile([], num_bins=10)

    assert result == {
        "bins": [],
        "poc": None,
        "total_volume": 0.0,
        "bar_count": 0,
        "num_bins": 10,
    }


def test_compute_volume_profile_skips_bars_with_missing_or_invalid_fields():
    bars = [
        _bar(0, 90, 30),
        {"low": None, "high": 50, "volume": 100},  # low 없음 -> 제외
        {"low": 10, "high": None, "volume": 100},  # high 없음 -> 제외
        {"low": 10, "high": 20, "volume": None},  # volume 없음 -> 제외
        {"low": 50, "high": 10, "volume": 100},  # high < low(데이터 이상) -> 제외
        {"low": 10, "high": 20, "volume": -5},  # volume < 0 -> 제외
    ]

    result = volume_profile.compute_volume_profile(bars, num_bins=3)

    # 유효한 봉은 첫 번째(0~90, volume=30) 하나뿐.
    assert result["bar_count"] == 1
    assert result["total_volume"] == 30.0


def test_compute_volume_profile_distributes_volume_evenly_across_overlapping_bins():
    # 가격 구간 0~90을 3개 bin으로 나누면 [0,30),[30,60),[60,90] — 폭 30씩.
    # 봉 하나(low=0, high=90, volume=30)는 3개 bin 전부에 걸치므로 균등분배로
    # 각 bin에 30/3=10씩 담겨야 한다(모듈 docstring (a)절 "균등분배" 그대로).
    bars = [_bar(0, 90, 30)]

    result = volume_profile.compute_volume_profile(bars, num_bins=3)

    assert [b["volume"] for b in result["bins"]] == [10.0, 10.0, 10.0]
    assert result["bins"][0] == {
        "price_low": 0.0,
        "price_high": 30.0,
        "price_mid": 15.0,
        "volume": 10.0,
    }
    assert result["bins"][1]["price_low"] == 30.0
    assert result["bins"][2]["price_high"] == 90.0
    assert result["total_volume"] == 30.0
    # 3개 bin이 동률이라 max()가 고르는 첫 번째(가격이 가장 낮은) bin이 POC.
    assert result["poc"] == result["bins"][0]


def test_compute_volume_profile_bar_spanning_two_bins_splits_in_half():
    # 거래량 0짜리 두 봉으로 전체 가격 구간을 0~90으로 고정해 둔다(자신은 어느
    # bin에도 거래량을 더하지 않음 — 오직 range 계산용). 이 range를 3 bin
    # ([0,30),[30,60),[60,90])으로 나눈 뒤, low=40,high=80인 봉은 bin index
    # 1([30,60))과 2([60,90]) 두 개에 걸친다 -> volume 20을 절반씩(각 10)으로
    # 나눠 담아야 한다. bin 0은 아무 봉도 안 걸쳐 0.
    bars = [_bar(0, 0, 0), _bar(90, 90, 0), _bar(40, 80, 20)]

    result = volume_profile.compute_volume_profile(bars, num_bins=3)

    assert [b["volume"] for b in result["bins"]] == [0.0, 10.0, 10.0]


def test_compute_volume_profile_degenerate_single_price_range():
    # 모든 봉이 동일한 저가=고가(가격 변동 없음)면 bin을 여러 개로 못 나누므로
    # 단일 bin에 전체 거래량이 담긴다.
    bars = [_bar(100, 100, 5), _bar(100, 100, 7)]

    result = volume_profile.compute_volume_profile(bars, num_bins=10)

    assert result["num_bins"] == 1
    assert result["bins"] == [
        {"price_low": 100.0, "price_high": 100.0, "price_mid": 100.0, "volume": 12.0}
    ]
    assert result["poc"] == result["bins"][0]
    assert result["bar_count"] == 2


def test_compute_volume_profile_poc_is_bin_with_max_volume():
    # 4개 bin(0~100, 폭 25): [0,25),[25,50),[50,75),[75,100]. 대부분의 거래량이
    # bin 2([50,75))에 몰리게 만든다.
    bars = [_bar(0, 100, 4) for _ in range(3)]  # 각 봉이 4 bin에 걸침 -> 1씩 = 3개 봉이면 bin당 3
    bars.append(_bar(55, 70, 100))  # bin index 2 안에서만 -> 그대로 100 추가

    result = volume_profile.compute_volume_profile(bars, num_bins=4)

    volumes = [b["volume"] for b in result["bins"]]
    assert volumes == [3.0, 3.0, 103.0, 3.0]
    assert result["poc"]["price_low"] == 50.0
    assert result["poc"]["price_high"] == 75.0
    assert result["poc"]["volume"] == 103.0


# -- detect_levels ------------------------------------------------------------


def _profile_with_bar_count(bins: list[dict], bar_count: int) -> dict:
    total_volume = sum(b["volume"] for b in bins)
    poc = max(bins, key=lambda b: b["volume"]) if bins else None
    return {"bins": bins, "poc": poc, "total_volume": total_volume, "bar_count": bar_count}


def test_detect_levels_returns_empty_when_bar_count_below_minimum():
    # MIN_BARS_FOR_LEVELS(20) 미만이면 피크 계산 자체를 포기하고 빈 리스트.
    bins = [{"price_low": 0, "price_high": 10, "price_mid": 5, "volume": 100}]
    profile = _profile_with_bar_count(bins, bar_count=volume_profile.MIN_BARS_FOR_LEVELS - 1)

    assert volume_profile.detect_levels(profile) == []


def test_detect_levels_returns_empty_for_empty_profile():
    profile = {"bins": [], "poc": None, "total_volume": 0.0, "bar_count": 0}

    assert volume_profile.detect_levels(profile) == []


def test_detect_levels_finds_single_obvious_spike_as_poc():
    # 20개 봉이 0~100 구간 전체(4 bin, 폭 25)에 균등분배로 조금씩(봉당 volume=1
    # -> bin당 0.25) 쌓이고, 21번째 봉이 bin index 2([50,75)) 안에만 정확히
    # 들어가는 volume=100짜리 스파이크. bar_count=21 >= MIN_BARS_FOR_LEVELS(20).
    bars = [_bar(0, 100, 1) for _ in range(20)]
    bars.append(_bar(55, 70, 100))

    profile = volume_profile.compute_volume_profile(bars, num_bins=4)
    assert profile["bar_count"] == 21

    levels = volume_profile.detect_levels(profile)

    # bin 0/1/3은 전부 5.0으로 동률(이웃보다 "엄격히" 크지 않아 피크가 아님),
    # bin 2만 105.0으로 압도적 -> 피크는 1개, 그게 곧 POC.
    assert len(levels) == 1
    assert levels[0]["price_low"] == 50.0
    assert levels[0]["price_high"] == 75.0
    assert levels[0]["volume"] == 105.0
    assert levels[0]["is_poc"] is True


def test_detect_levels_filters_out_weak_peaks_below_prominence_ratio():
    # 7개 bin, 손으로 국소 최댓값 2개를 심는다: index 2(volume=100, 전체 최댓값)와
    # index 5(volume=20, 이웃(10,10)보다는 높지만 전체 최댓값 100의 30%=30보다
    # 낮아 min_prominence_ratio=0.3 기본값에서 걸러져야 함).
    bin_volumes = [5, 10, 100, 10, 10, 20, 10]
    bins = [
        {"price_low": i * 10, "price_high": (i + 1) * 10, "price_mid": i * 10 + 5, "volume": v}
        for i, v in enumerate(bin_volumes)
    ]
    profile = _profile_with_bar_count(bins, bar_count=volume_profile.MIN_BARS_FOR_LEVELS)

    levels = volume_profile.detect_levels(profile)

    assert len(levels) == 1
    assert levels[0]["volume"] == 100
    assert levels[0]["is_poc"] is True


def test_detect_levels_keeps_multiple_peaks_above_threshold_sorted_and_capped():
    # 두 피크 모두 threshold를 넘도록: index 1(volume=60)과 index 4(volume=90,
    # 전체 최댓값). threshold = 90*0.3=27 — 둘 다 통과.
    bin_volumes = [5, 60, 5, 5, 90, 5]
    bins = [
        {"price_low": i * 10, "price_high": (i + 1) * 10, "price_mid": i * 10 + 5, "volume": v}
        for i, v in enumerate(bin_volumes)
    ]
    profile = _profile_with_bar_count(bins, bar_count=volume_profile.MIN_BARS_FOR_LEVELS)

    levels = volume_profile.detect_levels(profile, max_levels=8)

    assert [lv["volume"] for lv in levels] == [90, 60]  # 거래량 내림차순
    assert levels[0]["is_poc"] is True
    assert levels[1]["is_poc"] is False


def test_detect_levels_respects_max_levels_cap():
    # 6개 bin 전부가 피크가 되도록 지그재그(높음/낮음 교대) 패턴을 만들고,
    # max_levels로 상한이 실제로 걸리는지 확인한다.
    bin_volumes = [100, 1, 90, 1, 80, 1]
    bins = [
        {"price_low": i * 10, "price_high": (i + 1) * 10, "price_mid": i * 10 + 5, "volume": v}
        for i, v in enumerate(bin_volumes)
    ]
    profile = _profile_with_bar_count(bins, bar_count=volume_profile.MIN_BARS_FOR_LEVELS)

    levels = volume_profile.detect_levels(profile, max_levels=2)

    assert len(levels) == 2
    assert [lv["volume"] for lv in levels] == [100, 90]


def test_detect_levels_edge_bins_only_need_to_beat_single_neighbor():
    # 첫/마지막 bin이 이웃 하나만 이기면 피크로 인정되는지 확인. index 0
    # (volume=50)은 오른쪽 이웃(10)만 이기면 되고, index 4(volume=60, 마지막)는
    # 왼쪽 이웃(10)만 이기면 된다.
    bin_volumes = [50, 10, 5, 10, 60]
    bins = [
        {"price_low": i * 10, "price_high": (i + 1) * 10, "price_mid": i * 10 + 5, "volume": v}
        for i, v in enumerate(bin_volumes)
    ]
    profile = _profile_with_bar_count(bins, bar_count=volume_profile.MIN_BARS_FOR_LEVELS)

    levels = volume_profile.detect_levels(profile)

    assert sorted(lv["volume"] for lv in levels) == [50, 60]
