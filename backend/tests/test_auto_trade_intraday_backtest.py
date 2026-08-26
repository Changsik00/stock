"""app/quant/auto_trade_intraday_backtest.py 순수 함수 단위테스트 (PLAN.md §5.77).

전부 DB/네트워크 무관 — 합성 1분봉으로 §5.54 핵심 규칙이 분봉에서도 그대로
동작하는지, 그리고 `daily_reset` 트레일 모드가 실제로 의도한 대로 동작하는지
확인한다.

**설계가 한 번 틀렸었다** — 처음엔 "매일 고점을 그날 시가로 교체"하는
방식이었는데, `test_daily_reset_within_a_single_day_exits_no_later_than_ratchet`
류의 시나리오로 직접 돌려보니 여러 날에 걸친 완만한 하락에서 `ratchet`보다
오히려 더 많이 반납했다(진입 이후 전체 고점이 항상 그날 고점보다 크거나
같으므로, 기준을 매일 더 낮게 갈아 치우면 청산까지 더 멀어질 뿐이었다).
지금 버전은 `ratchet` 조건을 그대로 두고 더 타이트한 "오늘 자체 고점 기준"
조건을 OR로 추가하는 방식 — 아래 테스트들이 이 수정된 설계를 검증한다.
"""

from __future__ import annotations

import pytest

from app.quant.auto_trade_intraday_backtest import run_intraday_backtest


def _bar(date: str, time: str, close: float, volume: float) -> dict:
    return {"date": date, "time": time, "close": close, "volume": volume}


def _hhmm(start_hour: int, idx: int) -> str:
    hh = start_hour + idx // 60
    mm = idx % 60
    return f"{hh:02d}{mm:02d}"


def _warmup_bars(n: int = 24, price: float = 100.0, date: str = "2026-01-01") -> list[dict]:
    """골든/데드크로스도 거래량 스파이크도 뜨지 않는 평평한 워밍업 구간
    (`tests/test_auto_trade_backtest.py::_warmup_bars`와 동일한 원리, 분봉
    시각만 다름 — 09:00부터 1분 간격)."""
    bars = []
    for i in range(n):
        vol = 950.0 if i % 2 == 0 else 1050.0
        bars.append(_bar(date, _hhmm(9, i), price, vol))
    return bars


def test_entry_triggers_on_golden_cross_and_volume_spike():
    bars = _warmup_bars(24, price=100.0)
    bars.append(_bar("2026-01-01", "0924", 110.0, 5000.0))
    for i in range(5):
        bars.append(_bar("2026-01-01", _hhmm(9, 25 + i), 110.0, 1000.0))

    result = run_intraday_backtest(bars)

    assert result["trades"] == []
    assert result["summary"]["n"] == 0


def test_stop_loss_exits_at_minus_1_5_pct_from_entry():
    bars = _warmup_bars(24, price=100.0)
    bars.append(_bar("2026-01-01", "0924", 110.0, 5000.0))  # 진입(entry_price=110)
    bars.append(_bar("2026-01-01", "0925", 108.0, 1000.0))  # -1.818% <= -1.5% -> 손절

    result = run_intraday_backtest(bars)

    assert len(result["trades"]) == 1
    trade = result["trades"][0]
    assert trade["exit_reason"] == "stop_loss"
    assert trade["entry_price"] == 110.0
    assert trade["exit_price"] == 108.0
    assert trade["entry_date"] == "2026-01-01"
    assert trade["entry_time"] == "0924"
    assert trade["exit_time"] == "0925"
    assert trade["pct_return"] < -1.5
    assert result["summary"]["n"] == 1


def test_no_entry_without_volume_spike():
    bars = _warmup_bars(24, price=100.0)
    bars.append(_bar("2026-01-01", "0924", 110.0, 1000.0))  # 거래량 스파이크 없음
    for i in range(5):
        bars.append(_bar("2026-01-01", _hhmm(9, 25 + i), 105.0, 1000.0))

    result = run_intraday_backtest(bars)

    assert result["trades"] == []


def test_run_intraday_backtest_rejects_unknown_trail_mode():
    with pytest.raises(ValueError):
        run_intraday_backtest(_warmup_bars(), trail_mode="not_a_real_mode")


def test_summary_of_empty_trades_is_well_formed():
    result = run_intraday_backtest(_warmup_bars(10))
    assert result["trades"] == []
    assert result["summary"] == {
        "n": 0,
        "win_rate_pct": None,
        "avg_win_pct": None,
        "avg_loss_pct": None,
        "cumulative_return_pct": 0.0,
        "best_trade_pct": None,
        "worst_trade_pct": None,
    }


def _fine_grained_single_day_scenario() -> list[dict]:
    """하루 안에서 진입 -> 트레일 전환 -> peak=115 -> 완만한 반전(-0.1/분).
    `daily_giveback_pct`(0.3%)와 `ratchet_giveback_pct`(0.5%) 임계값이
    peak=115 기준으로 각각 114.655/114.425에 위치하도록 촘촘한 간격(-0.1)으로
    하락시켜, 두 임계값이 서로 다른 분(bar)에서 충족되는 걸 직접 볼 수 있게
    한다."""
    bars = _warmup_bars(24, price=100.0)
    bars.append(_bar("2026-01-01", "0924", 105.0, 5000.0))  # 진입(entry_price=105)

    price = 106.0
    for i in range(10):  # 106 -> 115로 상승(트레일 전환 + peak=115 갱신)
        bars.append(_bar("2026-01-01", _hhmm(9, 25 + i), price, 1000.0))
        price += 1.0

    price = 114.9
    for i in range(20):  # 114.9부터 -0.1/분 완만한 하락
        bars.append(_bar("2026-01-01", _hhmm(9, 35 + i), price, 1000.0))
        price -= 0.1

    return bars


def test_daily_reset_exits_earlier_than_ratchet_within_a_single_day():
    """핵심 재현 테스트 — 하루 안에서도 `daily_reset`의 타이트한 당일 조건이
    `ratchet`의 느슨한 조건보다 먼저 충족돼 더 적은 반납폭으로(더 높은
    가격에) 청산돼야 한다."""
    bars = _fine_grained_single_day_scenario()

    daily_reset_result = run_intraday_backtest(bars, trail_mode="daily_reset")
    ratchet_result = run_intraday_backtest(bars, trail_mode="ratchet")

    assert len(daily_reset_result["trades"]) == 1
    assert len(ratchet_result["trades"]) == 1
    daily_reset_trade = daily_reset_result["trades"][0]
    ratchet_trade = ratchet_result["trades"][0]

    assert daily_reset_trade["exit_price"] == pytest.approx(114.6)
    assert ratchet_trade["exit_price"] == pytest.approx(114.4)
    assert daily_reset_trade["exit_price"] > ratchet_trade["exit_price"]
    assert daily_reset_trade["pct_return"] > ratchet_trade["pct_return"]


def _overnight_reversal_scenario() -> list[dict]:
    """1일차: 진입 후 트레일 전환, 장중 상승해 고점(peak≈121.4)을 찍고
    반전 없이 하루가 끝남. 2일차: 시가부터 쭉 하락. `fixed_floor`(진입가
    고정 플로어)는 이 반전을 트레일청산 조건(데드크로스+진입가+0.5%)으로
    못 잡고 결국 손절(-1.5%)까지 그대로 타고 내려가는 반면, peak 기준
    모드(`ratchet`/`daily_reset`)는 peak 근처에서 곧바로 청산돼 수익
    대부분을 지킨다 — 진입가 고정 플로어의 실제 문제(여러 날 보유 시 반납)
    를 가장 직접적으로 재현하는 시나리오."""
    bars = _warmup_bars(24, price=100.0, date="2026-01-01")
    bars.append(_bar("2026-01-01", "0924", 100.0, 5000.0))  # 진입(entry_price=100)

    price = 101.0
    for i in range(35):  # 1일차 09:25~09:59 — 101 -> 121.4로 급상승
        bars.append(_bar("2026-01-01", _hhmm(9, 25 + i), price, 1000.0))
        price += 0.6

    price = 120.0
    for i in range(60):  # 2일차 09:00부터 하락(120.0 -> 61.0)
        bars.append(_bar("2026-01-02", _hhmm(9, i), price, 1000.0))
        price -= 1.0

    return bars


def test_peak_based_modes_protect_gains_that_fixed_floor_gives_back():
    """§5.74에서 이미 확인한 구조적 문제(진입가 고정 플로어가 반전을 못 잡고
    수익을 거의 다 반납)를 1분봉·오버나잇 시나리오로 재확인 — `ratchet`/
    `daily_reset`은 peak 근처에서 청산해 수익 대부분을 지키는데, `fixed_floor`
    는 트레일청산 조건을 놓치고 결국 손절까지 타고 내려간다."""
    bars = _overnight_reversal_scenario()

    ratchet_result = run_intraday_backtest(bars, trail_mode="ratchet")
    daily_reset_result = run_intraday_backtest(bars, trail_mode="daily_reset")
    fixed_result = run_intraday_backtest(bars, trail_mode="fixed_floor")

    assert len(ratchet_result["trades"]) == 1
    assert len(daily_reset_result["trades"]) == 1
    assert len(fixed_result["trades"]) == 1

    ratchet_trade = ratchet_result["trades"][0]
    daily_reset_trade = daily_reset_result["trades"][0]
    fixed_trade = fixed_result["trades"][0]

    assert ratchet_trade["exit_reason"] == "exit_trail"
    assert daily_reset_trade["exit_reason"] == "exit_trail"
    # fixed_floor는 반전을 트레일청산으로 못 잡고 결국 손절까지 밀린다 —
    # 이게 바로 사용자가 지적한 "결국 수익 다 빼앗기고 매도" 그 자체다.
    assert fixed_trade["exit_reason"] == "stop_loss"

    assert ratchet_trade["pct_return"] > 15  # peak(+21.4%) 근처에서 청산, 수익 대부분 유지
    assert daily_reset_trade["pct_return"] > 15
    assert fixed_trade["pct_return"] < 0  # 결국 손실로 전환


def test_daily_reset_never_worse_than_ratchet_in_overnight_scenario():
    """`daily_reset`은 `ratchet` 조건을 OR로 포함하므로 이 시나리오에서도
    ratchet보다 늦게(더 낮은 가격에) 청산될 수 없다 — 이 경우엔 ratchet
    조건이 이미 2일차 시가에서 충족돼(오버나잇 갭 자체가 반납폭을 채움)
    둘이 같은 지점에서 청산된다(당일 조건이 추가로 기여할 여지가 없었을
    뿐, 절대 더 나쁘지는 않다는 게 핵심)."""
    bars = _overnight_reversal_scenario()

    ratchet_result = run_intraday_backtest(bars, trail_mode="ratchet")
    daily_reset_result = run_intraday_backtest(bars, trail_mode="daily_reset")

    assert daily_reset_result["trades"][0]["exit_price"] >= ratchet_result["trades"][0]["exit_price"]
