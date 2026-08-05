"""Unit tests for `_golden_cross_in_lookback`/`_volume_spike_in_lookback`
(app.collectors.auto_trader) — PLAN.md §5.54, 2026-08-05 실측 버그 수정.

`BARS`는 2026-08-05 실제 0167A0 1분봉(0167A0가 실제로 10:33 KST에 골든크로스+
거래량 스파이크를 동시에 냈던 실데이터 슬라이스, 사용자가 직접 지적해서 발견한
케이스)을 그대로 가져온 것이다 — 합성 데이터가 아니라 실제로 폴링이 놓쳤던
상황을 그대로 재현한다. 이 슬라이스는 T=21(21번째 봉까지의 시점)에서
`moving_average_cross`가 "golden", `volume_spike`가 `is_spike=True`를 동시에
반환하고, T=22부터는 이미 둘 다 "지나간" 상태(다음 봉으로 넘어가면 순간
이벤트가 사라짐)로 돌아간다(`app.quant.signals`로 직접 재계산해 확인됨).
"""

from __future__ import annotations

from app.collectors.auto_trader import (
    ENTRY_SIGNAL_LOOKBACK_BARS,
    _golden_cross_in_lookback,
    _volume_spike_in_lookback,
)

# 실제 0167A0 1분봉(2026-08-05, 골든크로스+거래량 스파이크가 T=21에서 동시 발생).
BARS = [
    {"close": 16685, "volume": 37541},
    {"close": 16700, "volume": 35217},
    {"close": 16675, "volume": 36567},
    {"close": 16635, "volume": 22918},
    {"close": 16625, "volume": 74522},
    {"close": 16615, "volume": 32022},
    {"close": 16540, "volume": 65436},
    {"close": 16600, "volume": 46187},
    {"close": 16565, "volume": 51010},
    {"close": 16585, "volume": 35718},
    {"close": 16580, "volume": 33942},
    {"close": 16550, "volume": 37093},
    {"close": 16515, "volume": 53271},
    {"close": 16495, "volume": 82054},
    {"close": 16525, "volume": 114549},
    {"close": 16500, "volume": 41955},
    {"close": 16560, "volume": 85426},
    {"close": 16605, "volume": 68070},
    {"close": 16595, "volume": 62447},
    {"close": 16680, "volume": 141981},
    {"close": 16650, "volume": 124041},  # T=21 -> golden cross + volume spike, 여기서 발생
    {"close": 16625, "volume": 56417},  # T=22 -> 이미 지나감(naive 체크는 놓침)
    {"close": 16620, "volume": 44399},  # T=23
    {"close": 16665, "volume": 92646},  # T=24 -> lookback 창(3봉) 밖, 더 이상 안 잡힘
    {"close": 16640, "volume": 32562},  # T=25
]

HIT_INDEX = 21  # 1-indexed 봉 개수 기준 — bars[:HIT_INDEX]가 정확히 그 순간.


def test_lookback_window_is_three_bars():
    """이 테스트 파일의 나머지 단언들이 가정하는 상수값을 명시적으로 고정한다
    — 상수가 바뀌면 아래 T=22~25 기대값도 같이 바뀌어야 하므로, 실수로
    상수만 바뀌고 테스트를 안 고치는 사고를 여기서 먼저 잡는다."""
    assert ENTRY_SIGNAL_LOOKBACK_BARS == 3


def test_exact_moment_is_caught():
    """T=HIT_INDEX(사건이 일어난 바로 그 순간)는 당연히 감지돼야 한다."""
    bars = BARS[:HIT_INDEX]
    assert _golden_cross_in_lookback(bars) is True
    assert _volume_spike_in_lookback(bars) is True


def test_one_bar_late_is_still_caught():
    """2026-08-05에 실제로 놓쳤던 상황 — 폴링이 정확한 순간을 못 맞추고 1봉
    늦게 도착한 경우(T=HIT_INDEX+1). 이번 수정으로 이제는 잡혀야 한다."""
    bars = BARS[: HIT_INDEX + 1]
    assert _golden_cross_in_lookback(bars) is True
    assert _volume_spike_in_lookback(bars) is True


def test_two_bars_late_is_still_caught():
    """룩백 창(3봉)의 마지막 여유분 — T=HIT_INDEX+2까지는 여전히 잡혀야 한다."""
    bars = BARS[: HIT_INDEX + 2]
    assert _golden_cross_in_lookback(bars) is True
    assert _volume_spike_in_lookback(bars) is True


def test_three_bars_late_expires_out_of_window():
    """룩백 창을 벗어나면(T=HIT_INDEX+3) 더 이상 잡히지 않는다 — 조건을
    무한정 느슨하게 만드는 게 아니라 딱 최근 몇 봉만 봐준다는 걸 확인한다."""
    bars = BARS[: HIT_INDEX + 3]
    assert _golden_cross_in_lookback(bars) is False
    assert _volume_spike_in_lookback(bars) is False


def test_naive_latest_bar_check_would_have_missed_it_one_bar_late():
    """대조군 — 이번에 고치기 전의 동작(가장 최신 봉만 보는 것)이 실제로
    T=HIT_INDEX+1에서 이미 신호를 놓쳤다는 걸 재확인한다(회귀 방지)."""
    from app.quant.signals import moving_average_cross, volume_spike

    bars = BARS[: HIT_INDEX + 1]
    assert moving_average_cross(bars)["state"] != "golden"
    assert volume_spike(bars)["is_spike"] is False


def test_too_few_bars_returns_false_not_error():
    """워밍업 구간(장기이평 계산에 필요한 21봉 미만)에서는 조용히 False —
    크래시하지 않는다."""
    assert _golden_cross_in_lookback(BARS[:5]) is False
    assert _volume_spike_in_lookback(BARS[:1]) is False
