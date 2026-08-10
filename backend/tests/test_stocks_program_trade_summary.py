"""app/routers/stocks.py::_compute_program_trade_summary 순수 함수 단위테스트.

DB/네트워크 무관 — 알려진 program_trade 행 리스트(오름차순, `_read_program_trade`
출력 형태)로 기대값을 손계산해 검증한다(test_quant_screener.py와 동일한 스타일).
스트릭 판정은 `quant/regime_backtest.py::next_streak`을 그대로 재사용한다는 게
사용자 요구사항이라, 여기서는 그 재사용이 낳는 결과값(연속/전환/None-skip)만
검증하고 next_streak 자체의 로직은 재검증하지 않는다.
"""

from __future__ import annotations

from app.routers.stocks import _compute_program_trade_summary


def _row(date: str, total_net: int | None) -> dict:
    return {"date": date, "arb_net": None, "non_arb_net": None, "total_net": total_net}


def test_empty_rows_returns_zero_streak_and_none_cumulatives():
    result = _compute_program_trade_summary([])
    assert result == {"streak": 0, "cumulative_net_5d": None, "cumulative_net_10d": None}


def test_all_positive_rows_accumulate_positive_streak():
    rows = [_row("20260801", 100), _row("20260802", 200), _row("20260803", 300)]
    result = _compute_program_trade_summary(rows)
    assert result["streak"] == 3
    assert result["cumulative_net_5d"] == 600  # 3개뿐이라 5/10일 창 모두 3개 합
    assert result["cumulative_net_10d"] == 600


def test_all_negative_rows_accumulate_negative_streak():
    rows = [_row("20260801", -100), _row("20260802", -50)]
    result = _compute_program_trade_summary(rows)
    assert result["streak"] == -2
    assert result["cumulative_net_5d"] == -150
    assert result["cumulative_net_10d"] == -150


def test_sign_flip_resets_streak_to_one():
    # +, +, - : 마지막날 부호가 바뀌므로 스트릭은 -1로 리셋된다(연속 누적 아님).
    rows = [_row("20260801", 100), _row("20260802", 50), _row("20260803", -10)]
    result = _compute_program_trade_summary(rows)
    assert result["streak"] == -1
    assert result["cumulative_net_5d"] == 140
    assert result["cumulative_net_10d"] == 140


def test_zero_total_net_resets_streak_to_zero():
    rows = [_row("20260801", 100), _row("20260802", 0), _row("20260803", 50)]
    result = _compute_program_trade_summary(rows)
    # next_streak(streak, 0) == 0 이므로 둘째날에 0으로 리셋, 셋째날 다시 +1부터 시작.
    assert result["streak"] == 1
    assert result["cumulative_net_5d"] == 150
    assert result["cumulative_net_10d"] == 150


def test_none_total_net_is_skipped_for_streak_but_ignored_in_cumulative():
    rows = [
        _row("20260801", 100),
        _row("20260802", None),  # 조회 실패/결측일 — 스트릭 유지, 합산에서 제외
        _row("20260803", 200),
    ]
    result = _compute_program_trade_summary(rows)
    assert result["streak"] == 2  # None은 건너뛰고 이전 스트릭(+1)에서 +1 더 누적
    assert result["cumulative_net_5d"] == 300  # 100 + 200 (None 제외)
    assert result["cumulative_net_10d"] == 300


def test_all_none_total_net_gives_none_cumulatives_and_zero_streak():
    rows = [_row("20260801", None), _row("20260802", None)]
    result = _compute_program_trade_summary(rows)
    assert result["streak"] == 0
    assert result["cumulative_net_5d"] is None
    assert result["cumulative_net_10d"] is None


def test_cumulative_windows_use_only_most_recent_rows():
    # 12개 행: 5일/10일 누적 창이 서로 다른 부분집합을 합산해야 한다.
    rows = [_row(f"202608{i:02d}", 10) for i in range(1, 13)]  # 12행, 각 total_net=10
    result = _compute_program_trade_summary(rows)
    assert result["cumulative_net_5d"] == 50  # 마지막 5개
    assert result["cumulative_net_10d"] == 100  # 마지막 10개
    assert result["streak"] == 12


def test_fewer_rows_than_window_uses_all_available_rows_for_both_windows():
    rows = [_row("20260801", 10), _row("20260802", 20), _row("20260803", 30)]
    result = _compute_program_trade_summary(rows)
    assert result["cumulative_net_5d"] == 60
    assert result["cumulative_net_10d"] == 60
