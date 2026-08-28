"""app/quant/short_balance_trend.py 순수 함수 단위테스트 (PLAN.md §5.81).

전부 DB/네트워크 무관 — 합성 잔고 시계열로 방향/연속일수/peak 대비 감소폭/
phase 라벨이 올바르게 계산되는지 확인한다.
"""

from __future__ import annotations

from app.quant.short_balance_trend import evaluate_short_balance_trend


def _row(date: str, balance_qty: int | None) -> dict:
    return {"date": date, "volume": 0, "value": 0, "balance_qty": balance_qty, "balance_value": 0}


def test_insufficient_data_when_no_rows():
    result = evaluate_short_balance_trend([])
    assert result == {
        "insufficient_data": True,
        "reason": "보고의무 발생 종목이 아니거나(순보유잔고 비공개) 유효 데이터가 부족함",
    }


def test_insufficient_data_when_all_none():
    rows = [_row("20260101", None), _row("20260102", None)]
    result = evaluate_short_balance_trend(rows)
    assert result["insufficient_data"] is True


def test_insufficient_data_when_only_one_valid_row():
    rows = [_row("20260101", None), _row("20260102", 1000)]
    result = evaluate_short_balance_trend(rows)
    assert result["insufficient_data"] is True


def test_none_rows_are_skipped_not_treated_as_gaps():
    rows = [
        _row("20260101", 1000),
        _row("20260102", None),  # 건너뜀
        _row("20260103", 1200),
    ]
    result = evaluate_short_balance_trend(rows)
    assert result["insufficient_data"] is False
    assert result["today_balance_qty"] == 1200
    assert result["current_direction"] == "up"


def test_rising_balance_labels_expanding_short_position():
    rows = [_row(f"2026010{i}", 1000 + i * 100) for i in range(1, 6)]  # 계속 상승
    result = evaluate_short_balance_trend(rows)
    assert result["current_direction"] == "up"
    assert result["consecutive_days_same_direction"] == 4
    assert result["phase"] == "공매도 잔고 확대 중(신규 공매도 유입 가능성)"
    # 계속 상승 중이므로 오늘 자체가 peak
    assert result["peak"]["balance_qty"] == 1500
    assert result["days_since_peak"] == 0
    assert result["pct_off_peak"] == 0.0


def test_declining_balance_after_peak_labels_short_covering():
    rows = [
        _row("20260101", 1000),
        _row("20260102", 1500),
        _row("20260103", 2000),  # peak
        _row("20260104", 1800),
        _row("20260105", 1600),
        _row("20260106", 1400),
    ]
    result = evaluate_short_balance_trend(rows)
    assert result["current_direction"] == "down"
    assert result["consecutive_days_same_direction"] == 3
    assert result["peak"] == {"date": "20260103", "balance_qty": 2000}
    assert result["days_since_peak"] == 3
    assert result["pct_off_peak"] == -30.0
    assert result["phase"] == "공매도 잔고 감소 중(숏커버링 국면일 수 있음)"


def test_small_decline_off_peak_does_not_claim_short_covering():
    rows = [
        _row("20260101", 1000),
        _row("20260102", 2000),  # peak
        _row("20260103", 1990),
        _row("20260104", 1980),
    ]
    result = evaluate_short_balance_trend(rows)
    assert result["current_direction"] == "down"
    assert result["consecutive_days_same_direction"] == 2
    # peak 대비 -1% 이내라 "국면일 수 있음"까지는 아직 못 감(감소폭 미미)
    assert result["phase"] == "공매도 잔고 소폭 감소 중"


def test_direction_flip_breaks_streak():
    rows = [
        _row("20260101", 1000),
        _row("20260102", 1200),
        _row("20260103", 1100),  # 방향 전환
    ]
    result = evaluate_short_balance_trend(rows)
    assert result["current_direction"] == "down"
    assert result["consecutive_days_same_direction"] == 1


def test_flat_direction_when_unchanged():
    rows = [_row("20260101", 1000), _row("20260102", 1000)]
    result = evaluate_short_balance_trend(rows)
    assert result["current_direction"] == "flat"
    assert result["consecutive_days_same_direction"] == 0
    assert result["phase"] == "방향 불분명(횡보 또는 데이터 변동 미미)"


def test_limitations_always_mentions_reporting_threshold_caveat():
    rows = [_row("20260101", 1000), _row("20260102", 1100)]
    result = evaluate_short_balance_trend(rows)
    assert any("보고의무" in item for item in result["limitations"])
