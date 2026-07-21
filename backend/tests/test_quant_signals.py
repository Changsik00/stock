"""app/quant/signals.py 순수 함수 단위테스트 (PLAN.md §5.3).

전부 DB/네트워크 무관 — 알려진 입력값으로 기대값을 손계산해 검증한다.
"""

from __future__ import annotations

from app.quant.signals import (
    compute_vwap,
    detect_breakout,
    momentum,
    moving_average_cross,
    volume_spike,
)


def _bar(o, h, l, c, v):  # noqa: E741 - l(low)은 도메인 관례상 명확한 축약
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


# -- compute_vwap ------------------------------------------------------------


def test_vwap_empty_bars_returns_none():
    assert compute_vwap([]) == {"value": None, "deviation_pct": None}


def test_vwap_known_values():
    # 전형가격 = (h+l+c)/3
    # bar1: (110+90+100)/3=100, vol=10 -> pv=1000
    # bar2: (120+100+110)/3=110, vol=20 -> pv=2200
    bars = [_bar(100, 110, 90, 100, 10), _bar(105, 120, 100, 110, 20)]
    result = compute_vwap(bars)
    # vwap = (1000+2200)/(10+20) = 3200/30 = 106.666...
    assert result["value"] == round(3200 / 30, 2)
    current = 110
    expected_dev = (current - result["value"]) / result["value"] * 100
    assert result["deviation_pct"] == round(expected_dev, 2)


def test_vwap_zero_volume_returns_none():
    bars = [_bar(100, 110, 90, 100, 0)]
    assert compute_vwap(bars) == {"value": None, "deviation_pct": None}


# -- detect_breakout ----------------------------------------------------------


def test_breakout_single_bar_is_none():
    assert detect_breakout([_bar(100, 105, 95, 102, 10)])["direction"] == "none"


def test_breakout_new_high():
    bars = [
        _bar(100, 105, 95, 100, 10),
        _bar(100, 103, 98, 101, 10),
        _bar(101, 110, 101, 108, 10),  # close 108 >= prior high 105
    ]
    assert detect_breakout(bars) == {"direction": "high"}


def test_breakout_new_low():
    bars = [
        _bar(100, 105, 95, 100, 10),
        _bar(100, 103, 98, 101, 10),
        _bar(98, 99, 90, 91, 10),  # close 91 <= prior low 95
    ]
    assert detect_breakout(bars) == {"direction": "low"}


def test_breakout_no_new_extreme_is_none():
    bars = [
        _bar(100, 105, 95, 100, 10),
        _bar(100, 103, 98, 101, 10),
        _bar(101, 102, 99, 100, 10),  # close 100 within [95,105]
    ]
    assert detect_breakout(bars) == {"direction": "none"}


# -- moving_average_cross ------------------------------------------------------


def test_ma_cross_insufficient_data_returns_none():
    bars = [_bar(100, 100, 100, 100, 10) for _ in range(10)]
    result = moving_average_cross(bars, short_window=5, long_window=20)
    assert result == {"state": "none", "short_ma": None, "long_ma": None}


def test_ma_cross_golden():
    # 21개 종가: 앞 20개는 평탄(100), 마지막(21번째)에 급등(150)해서
    # 단기(5) 평균이 장기(20) 평균을 아래->위로 교차하도록 구성.
    closes = [100.0] * 20 + [150.0]
    bars = [_bar(c, c, c, c, 10) for c in closes]
    result = moving_average_cross(bars, short_window=5, long_window=20)
    assert result["state"] == "golden"
    # 직전 시점(마지막 제외 20개): short=mean(마지막 5개인 100,100,100,100,100)=100,
    # long=mean(전체 20개 100)=100 -> prev_short <= prev_long (100<=100) 충족
    # 현재 시점(21개, 최근 20/5개만 사용): short=mean(100,100,100,100,150)=110,
    # long=mean(마지막 20개 = 19*100 + 150)/20 = 102.5 (가장 오래된 1개는 창 밖으로 빠짐)
    assert result["short_ma"] == round((100 * 4 + 150) / 5, 2)
    assert result["long_ma"] == round((100 * 19 + 150) / 20, 2)


def test_ma_cross_dead():
    closes = [100.0] * 20 + [50.0]
    bars = [_bar(c, c, c, c, 10) for c in closes]
    result = moving_average_cross(bars, short_window=5, long_window=20)
    assert result["state"] == "dead"


def test_ma_cross_no_event_stays_none():
    # 계속 상승 추세지만 "교차 순간"이 아닌 경우(이미 단기>장기 상태 유지)
    closes = [100.0 + i for i in range(22)]
    bars = [_bar(c, c, c, c, 10) for c in closes]
    result = moving_average_cross(bars, short_window=5, long_window=20)
    assert result["state"] == "none"


# -- volume_spike ---------------------------------------------------------------


def test_volume_spike_insufficient_history_returns_none():
    bars = [_bar(100, 100, 100, 100, 10)]
    assert volume_spike(bars) == {"zscore": None, "is_spike": False, "ratio": None}


def test_volume_spike_known_values():
    # 이전 20봉 거래량 100(고정) -> mean=100, stdev(pstdev)=0 -> zscore None (분모 0)
    history = [_bar(100, 100, 100, 100, 100) for _ in range(20)]
    spike_bar = _bar(100, 100, 100, 100, 320)  # 3.2배
    bars = history + [spike_bar]
    result = volume_spike(bars, window=20)
    assert result["zscore"] is None  # stdev=0이라 정의 불가
    assert result["ratio"] == 3.2
    assert result["is_spike"] is False


def test_volume_spike_zscore_with_variance():
    # 이전 5봉: 10,20,30,40,50 -> mean=30, pstdev=sqrt(((20^2+10^2+0+10^2+20^2))/5)
    history_vols = [10, 20, 30, 40, 50]
    bars = [_bar(100, 100, 100, 100, v) for v in history_vols] + [
        _bar(100, 100, 100, 100, 100)
    ]
    result = volume_spike(bars, window=5)
    import statistics

    mean = statistics.mean(history_vols)
    stdev = statistics.pstdev(history_vols)
    expected_z = (100 - mean) / stdev
    assert result["zscore"] == round(expected_z, 2)
    assert result["ratio"] == round(100 / mean, 2)
    assert result["is_spike"] == (expected_z >= 2.0)


# -- momentum ---------------------------------------------------------------


def test_momentum_insufficient_bars_returns_none():
    assert momentum([_bar(100, 100, 100, 100, 10)]) == {
        "return_pct": None,
        "window_bars": 5,
    }


def test_momentum_known_values():
    closes = [100, 101, 102, 103, 104, 110]  # 6개, window_bars=5 -> start=closes[0]=100
    bars = [_bar(c, c, c, c, 10) for c in closes]
    result = momentum(bars, window_bars=5)
    expected = (110 - 100) / 100 * 100
    assert result == {"return_pct": round(expected, 2), "window_bars": 5}


def test_momentum_uses_partial_window_when_data_short():
    closes = [100, 105]  # window_bars=5인데 2개뿐 -> start=closes[0]
    bars = [_bar(c, c, c, c, 10) for c in closes]
    result = momentum(bars, window_bars=5)
    expected = (105 - 100) / 100 * 100
    assert result == {"return_pct": round(expected, 2), "window_bars": 5}
