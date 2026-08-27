"""app/quant/supply_absorption_checklist.py 순수 함수 단위테스트 (PLAN.md §5.80).

전부 DB/네트워크 무관 — 합성 데이터로 각 체크리스트 항목(어제 고점 돌파,
어제 종가 공방, 거래량-저가 4분면, 투자자 연속매수, peer 비교)이 올바르게
계산되는지 확인한다.
"""

from __future__ import annotations

from app.quant.supply_absorption_checklist import evaluate_supply_absorption


def _price(date: str, open_: float, high: float, low: float, close: float, volume: int) -> dict:
    return {
        "date": date,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "changeRate": 0.0,
        "volume": volume,
        "value": 0,
    }


def _flow_rows(*net_values: float) -> list[dict]:
    return [{"date": f"2026010{i + 1}", "net_value": v, "net_volume": None} for i, v in enumerate(net_values)]


def test_insufficient_data_when_fewer_than_two_days():
    result = evaluate_supply_absorption([_price("20260101", 100, 105, 95, 100, 1000)], {})
    assert result == {"insufficient_data": True}


def test_prev_high_breakout_and_holding_above():
    prices = [
        _price("20260101", 300, 320, 296, 311, 770_000),  # 어제
        _price("20260102", 306, 325, 303, 322, 500_000),  # 오늘 — 고가 325 > 320, 종가 322 > 320
    ]
    result = evaluate_supply_absorption(prices, {})
    breakout = result["prev_high_breakout"]
    assert breakout["prev_high"] == 320
    assert breakout["broken"] is True
    assert breakout["holding_above"] is True


def test_prev_high_fake_breakout_not_holding():
    prices = [
        _price("20260101", 300, 320, 296, 311, 770_000),
        _price("20260102", 306, 322, 303, 315, 500_000),  # 고가 322로 잠깐 넘었지만 종가 315는 320 밑
    ]
    result = evaluate_supply_absorption(prices, {})
    breakout = result["prev_high_breakout"]
    assert breakout["broken"] is True
    assert breakout["holding_above"] is False


def test_prev_high_not_broken():
    prices = [
        _price("20260101", 300, 320, 296, 311, 770_000),
        _price("20260102", 306, 317, 303, 311, 500_000),  # 고가 317 < 320
    ]
    result = evaluate_supply_absorption(prices, {})
    breakout = result["prev_high_breakout"]
    assert breakout["broken"] is False
    assert breakout["holding_above"] is None


def test_prev_close_battleground_range_crossed():
    prices = [
        _price("20260101", 300, 320, 296, 311.5, 770_000),
        _price("20260102", 306, 317.5, 303.5, 311.5, 256_000),  # 저가~고가가 311.5를 가로지름
    ]
    result = evaluate_supply_absorption(prices, {})
    battle = result["prev_close_battleground"]
    assert battle["prev_close"] == 311.5
    assert battle["range_crossed_prev_close"] is True
    assert battle["pct_from_prev_close"] == 0.0


def test_volume_down_low_up_labels_seller_weakness():
    prices = [
        _price("20260101", 300, 320, 296.5, 311.5, 770_565),
        _price("20260102", 306, 317.5, 303.5, 311.5, 256_141),  # 거래량 감소, 저가 상승
    ]
    result = evaluate_supply_absorption(prices, {})
    quad = result["volume_low_quadrant"]
    assert quad["volume_direction"] == "down"
    assert quad["low_direction"] == "up"
    assert quad["label"] == "매도세 약화"


def test_volume_up_low_down_labels_supply_pressure_continues():
    prices = [
        _price("20260101", 300, 320, 300, 311, 300_000),
        _price("20260102", 306, 317, 290, 311, 500_000),  # 거래량 증가, 저가 하락
    ]
    result = evaluate_supply_absorption(prices, {})
    quad = result["volume_low_quadrant"]
    assert quad["volume_direction"] == "up"
    assert quad["low_direction"] == "down"
    assert quad["label"] == "물량 부담 지속"


def test_volume_down_low_down_labels_buyers_missing():
    prices = [
        _price("20260101", 300, 320, 300, 311, 500_000),
        _price("20260102", 306, 315, 290, 300, 200_000),  # 거래량 감소인데 저가도 하락
    ]
    result = evaluate_supply_absorption(prices, {})
    quad = result["volume_low_quadrant"]
    assert quad["volume_direction"] == "down"
    assert quad["low_direction"] == "down"
    assert quad["label"] == "매수세 실종 우려"


def test_volume_up_low_up_labels_strong_buying():
    prices = [
        _price("20260101", 300, 320, 296, 311, 300_000),
        _price("20260102", 306, 330, 305, 325, 600_000),  # 거래량 증가, 저가 상승
    ]
    result = evaluate_supply_absorption(prices, {})
    quad = result["volume_low_quadrant"]
    assert quad["volume_direction"] == "up"
    assert quad["low_direction"] == "up"
    assert "강한 매수세 유입" in quad["label"]


def test_volume_low_quadrant_marks_today_as_partial_when_market_open():
    prices = [
        _price("20260101", 300, 320, 296, 311, 300_000),
        _price("20260102", 306, 317, 303, 311, 100_000),
    ]
    result = evaluate_supply_absorption(prices, {}, market_is_open=True)
    assert result["volume_low_quadrant"]["today_volume_is_partial"] is True

    result_closed = evaluate_supply_absorption(prices, {}, market_is_open=False)
    assert result_closed["volume_low_quadrant"]["today_volume_is_partial"] is False


def test_investor_flow_continuity_counts_consecutive_streak():
    flows = {
        "외국인": _flow_rows(-18445, 17931, 20882, 13574, 30197),  # 최근 4일 연속 순매수
        "기관계": _flow_rows(10000, -5000, -3000, -1000, -1869),  # 최근 4일 연속 순매도
    }
    prices = [
        _price("20260101", 300, 320, 296, 311, 300_000),
        _price("20260102", 306, 317, 303, 311, 100_000),
    ]
    result = evaluate_supply_absorption(prices, flows)
    cont = result["investor_flow_continuity"]
    assert cont["외국인"]["current_direction"] == "up"
    assert cont["외국인"]["consecutive_days_same_direction"] == 4
    assert cont["기관계"]["current_direction"] == "down"
    assert cont["기관계"]["consecutive_days_same_direction"] == 4


def test_investor_flow_continuity_streak_breaks_on_sign_change():
    flows = {"외국인": _flow_rows(100, 200, -50)}  # 마지막 날만 방향 전환
    prices = [
        _price("20260101", 300, 320, 296, 311, 300_000),
        _price("20260102", 306, 317, 303, 311, 100_000),
    ]
    result = evaluate_supply_absorption(prices, flows)
    cont = result["investor_flow_continuity"]["외국인"]
    assert cont["current_direction"] == "down"
    assert cont["consecutive_days_same_direction"] == 1


def test_peer_comparison_all_positive_is_joint_strength():
    prices = [
        _price("20260101", 300, 320, 296, 311, 300_000),
        _price("20260102", 306, 317, 303, 315, 100_000),  # changeRate 필드는 미리 계산 안 하므로 직접 세팅 필요
    ]
    prices[1]["changeRate"] = 1.5
    result = evaluate_supply_absorption(prices, {}, peer_changes={"068270": 2.0, "207940": 0.8})
    peer = result["peer_comparison"]
    assert peer["label"] == "동반 강세"


def test_peer_comparison_isolated_move():
    prices = [
        _price("20260101", 300, 320, 296, 311, 300_000),
        _price("20260102", 306, 317, 303, 315, 100_000),
    ]
    prices[1]["changeRate"] = 1.5
    result = evaluate_supply_absorption(prices, {}, peer_changes={"068270": -0.88, "207940": -0.25})
    peer = result["peer_comparison"]
    assert peer["label"] == "개별 종목 수급에 가까움(peer 동반 아님)"


def test_peer_comparison_none_when_not_supplied():
    prices = [
        _price("20260101", 300, 320, 296, 311, 300_000),
        _price("20260102", 306, 317, 303, 315, 100_000),
    ]
    result = evaluate_supply_absorption(prices, {})
    assert result["peer_comparison"] is None


def test_limitations_always_mentions_am_pm_gap():
    prices = [
        _price("20260101", 300, 320, 296, 311, 300_000),
        _price("20260102", 306, 317, 303, 315, 100_000),
    ]
    result = evaluate_supply_absorption(prices, {})
    assert any("오전/오후" in item for item in result["limitations"])
