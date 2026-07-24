"""Unit tests for app.quant.volume_surge (PLAN.md §5.24, 시장 전체 거래량 급증
감지).

Same house pattern as tests/test_flow_acceleration.py's compute-function
tests, but this module needs no DB/session at all(순수 함수) — bars는 그냥
list[dict]로 직접 구성한다.
"""

from __future__ import annotations

from app.quant import volume_surge


def _bars(volumes: list[int | None]) -> list[dict]:
    """volume만 다른 값을 가진 최소 bar dict 리스트(오름차순, 과거->최신)."""
    return [{"volume": v} for v in volumes]


def test_volume_surge_normal_case_hand_computed():
    # baseline 30분: 전부 volume=100 -> baseline_avg=100
    # recent 5분: 전부 volume=400 -> recent_avg=400
    # multiple = 400/100 = 4.0
    bars = _bars([100] * 30 + [400] * 5)

    result = volume_surge.compute_volume_surge(bars)

    assert result == {
        "recent_minutes": 5,
        "baseline_minutes": 30,
        "recent_avg_volume": 400.0,
        "baseline_avg_volume": 100.0,
        "multiple": 4.0,
    }


def test_volume_surge_returns_none_when_bars_insufficient():
    # 34개만 있음(5+30=35 미만) -> None
    bars = _bars([100] * 34)

    assert volume_surge.compute_volume_surge(bars) is None


def test_volume_surge_boundary_exactly_enough_bars():
    # 정확히 35개(5+30) -> 경계값에서도 정상 계산돼야 함
    bars = _bars([100] * 30 + [200] * 5)

    result = volume_surge.compute_volume_surge(bars)

    assert result is not None
    assert result["baseline_avg_volume"] == 100.0
    assert result["recent_avg_volume"] == 200.0
    assert result["multiple"] == 2.0


def test_volume_surge_returns_none_when_baseline_avg_is_zero():
    # baseline 구간 전부 거래량 0 -> 배율이 무의미(0-나눗셈 방지) -> None
    bars = _bars([0] * 30 + [50] * 5)

    assert volume_surge.compute_volume_surge(bars) is None


def test_volume_surge_treats_missing_or_none_volume_as_zero():
    # baseline 30개 중 절반은 volume=None, 절반은 volume 키 자체가 없음(dict에
    # 없는 경우) — 둘 다 0으로 취급되어 baseline_avg=100(총 3000/30)이 나와야
    # 하고, 예외 없이 계산이 끝나야 한다.
    baseline_bars: list[dict] = [{"volume": None} for _ in range(15)] + [{"volume": 200} for _ in range(15)]
    recent_bars: list[dict] = [{"volume": None} for _ in range(2)] + [{"volume": 500} for _ in range(3)]
    bars = baseline_bars + recent_bars

    result = volume_surge.compute_volume_surge(bars)

    assert result is not None
    assert result["baseline_avg_volume"] == 100.0  # (15*0 + 15*200) / 30
    assert result["recent_avg_volume"] == 300.0  # (2*0 + 3*500) / 5
    assert result["multiple"] == 3.0


def test_volume_surge_returns_none_when_all_bars_missing_volume():
    # 전부 volume 키가 없거나 None -> baseline_avg=0 -> None(0-나눗셈 방지).
    bars = [{"volume": None} for _ in range(30)] + [{} for _ in range(5)]

    assert volume_surge.compute_volume_surge(bars) is None


def test_volume_surge_custom_window_minutes():
    bars = _bars([50] * 10 + [150] * 3)

    result = volume_surge.compute_volume_surge(bars, recent_minutes=3, baseline_minutes=10)

    assert result == {
        "recent_minutes": 3,
        "baseline_minutes": 10,
        "recent_avg_volume": 150.0,
        "baseline_avg_volume": 50.0,
        "multiple": 3.0,
    }
