"""app/quant/auto_trade_backtest.py 순수 함수 단위테스트 (PLAN.md §5.74).

전부 DB/네트워크 무관 — 합성 일봉으로 §5.54 핵심 규칙(진입/손절/트레일전환/
트레일청산)이 올바른 시점에 발동하는지 확인한다. 사용자가 코드 리뷰 중
발견한 구조적 의심점("트레일 청산 기준이 진입가 고정값이라 트레일링 중
큰 폭 상승 후 반전되면 수익을 거의 다 반납할 수 있다")을 실제로 재현하는
`test_ratchet_exits_earlier_than_fixed_floor_after_large_giveback`가 핵심이다.
"""

from __future__ import annotations

from app.quant.auto_trade_backtest import run_backtest


def _bar(date: str, close: float, volume: float) -> dict:
    return {"date": date, "close": close, "volume": volume}


def _warmup_bars(n: int = 24, price: float = 100.0) -> list[dict]:
    """골든/데드크로스도 거래량 스파이크도 뜨지 않는 평평한 워밍업 구간.
    ma_cross는 종가가 전부 동일하면 short==long이라 교차가 생기지 않는다.
    volume_spike는 거래량 이력에 약간의 변동(950/1050 교대)을 둬야
    표준편차가 0이 아니게 돼(§5.3 `volume_spike` 표준편차 0 예외 처리 참고)
    이후 진짜 스파이크 봉에서 z-score가 의미 있게 계산된다."""
    bars = []
    for i in range(n):
        vol = 950.0 if i % 2 == 0 else 1050.0
        bars.append(_bar(f"2026-01-{i + 1:02d}", price, vol))
    return bars


def test_entry_triggers_on_golden_cross_and_volume_spike():
    bars = _warmup_bars(24, price=100.0)
    # 트리거 봉 — 종가 급등(골든크로스 유발) + 거래량 급증(스파이크).
    bars.append(_bar("2026-01-25", 110.0, 5000.0))
    # 그 뒤로는 평평하게 유지(추가 이벤트 없음) — 진입만 확인하는 용도라
    # 아직 손절/트레일 조건에 안 걸리게 진입가 근처에 둔다.
    for i in range(5):
        bars.append(_bar(f"2026-01-{26 + i}", 110.0, 1000.0))

    result = run_backtest(bars)

    # 아직 보유 중(포지션이 안 닫힘)이라 완결된 거래는 0건이어야 한다 —
    # 진입 자체가 됐는지는 다음 테스트(손절)에서 청산까지 마쳐 확인한다.
    assert result["trades"] == []
    assert result["summary"]["n"] == 0


def test_stop_loss_exits_at_minus_1_5_pct_from_entry():
    bars = _warmup_bars(24, price=100.0)
    bars.append(_bar("2026-01-25", 110.0, 5000.0))  # 진입(entry_price=110)
    bars.append(_bar("2026-01-26", 108.0, 1000.0))  # -1.818% <= -1.5% -> 손절

    result = run_backtest(bars)

    assert len(result["trades"]) == 1
    trade = result["trades"][0]
    assert trade["exit_reason"] == "stop_loss"
    assert trade["entry_price"] == 110.0
    assert trade["exit_price"] == 108.0
    assert trade["pct_return"] < -1.5
    assert result["summary"]["n"] == 1
    assert result["summary"]["win_rate_pct"] == 0.0


def test_no_entry_without_volume_spike():
    """골든크로스만 있고 거래량 스파이크가 없으면 진입하지 않는다(§5.54
    진입 조건은 AND) — 거래량을 평상시 그대로 둔 채 종가만 급등시킨다."""
    bars = _warmup_bars(24, price=100.0)
    bars.append(_bar("2026-01-25", 110.0, 1000.0))  # 거래량 스파이크 없음
    for i in range(5):
        bars.append(_bar(f"2026-01-{26 + i}", 105.0, 1000.0))  # -1.5% 근처까지 하락해도

    result = run_backtest(bars)

    assert result["trades"] == []  # 진입 자체가 안 됐으니 손절도 없어야 함


def _ratchet_giveback_scenario() -> list[dict]:
    """진입 -> 트레일 전환 -> 큰 폭 상승(peak=115) -> 반전(하락 속도 -1.5/일).

    `moving_average_cross`의 "dead"는 순간 이벤트(전환되는 그 봉에서만
    참)라 `fixed_floor`가 실제로 청산하려면 "그 데드크로스가 뜨는 바로 그 날"
    가격도 이미 플로어(진입가+0.5%=105.525) 이하여야 한다 — 두 조건이 같은
    날 겹치도록 하락 기울기를 사전 계산해 뒀다(하락 -1.5/일이면 데드크로스가
    뜨는 날 종가가 정확히 105.0으로 플로어 밑까지 내려와 있다, 아래
    `test_ratchet_exits_earlier_than_fixed_floor_after_large_giveback`
    docstring 참고). 이 자체가 관찰: 하락 기울기가 이보다 완만하면(예:
    -0.5/일) `fixed_floor`는 이 시나리오에서 **한 번도 청산하지 않는다** —
    데드크로스가 플로어 도달보다 훨씬 먼저 뜨고 다시는 안 뜨기 때문이다."""
    bars = _warmup_bars(24, price=100.0)
    bars.append(_bar("2026-01-25", 105.0, 5000.0))  # 진입(entry_price=105)

    # 10거래일 동안 106 -> 115로 완만히 상승 — +1%(트레일 전환) 통과 후
    # peak이 계속 갱신된다.
    price = 106.0
    for i in range(10):
        bars.append(_bar(f"2026-02-{i + 1:02d}", price, 1000.0))
        price += 1.0  # 106,107,...,115

    # 15거래일 동안 114 -> 91.5로 하락(-1.5/일). 손절선(진입가 105 기준
    # -1.5% = 103.425)에 닿기 전, 7일째(종가 105.0)에 데드크로스가 형성되며
    # 동시에 플로어(105.525) 밑으로 내려가 fixed_floor도 그 날 청산된다.
    price = 114.0
    for i in range(15):
        bars.append(_bar(f"2026-03-{i + 1:02d}", price, 1000.0))
        price -= 1.5

    return bars


def test_ratchet_exits_earlier_than_fixed_floor_after_large_giveback():
    """사용자가 지적한 구조적 의심점의 핵심 재현 테스트 — 고점(peak) 대비
    반납폭 기준(ratchet)이 진입가 고정 플로어 기준(fixed_floor)보다 수익을
    덜 반납한 채(더 높은 가격에) 청산하는지 확인한다."""
    bars = _ratchet_giveback_scenario()

    ratchet_result = run_backtest(bars, trail_mode="ratchet")
    fixed_result = run_backtest(bars, trail_mode="fixed_floor")

    assert len(ratchet_result["trades"]) == 1
    assert len(fixed_result["trades"]) == 1
    ratchet_trade = ratchet_result["trades"][0]
    fixed_trade = fixed_result["trades"][0]
    assert ratchet_trade["exit_reason"] == "exit_trail"
    assert fixed_trade["exit_reason"] == "exit_trail"

    # 핵심 단언 — ratchet이 fixed_floor보다 더 일찍(더 높은 가격에) 팔아
    # 수익을 덜 반납해야 한다.
    assert ratchet_trade["exit_price"] > fixed_trade["exit_price"]
    assert ratchet_trade["pct_return"] > fixed_trade["pct_return"]


def test_run_backtest_rejects_unknown_trail_mode():
    import pytest

    with pytest.raises(ValueError):
        run_backtest(_warmup_bars(), trail_mode="not_a_real_mode")


def test_summary_of_empty_trades_is_well_formed():
    result = run_backtest(_warmup_bars(10))
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
