"""Unit tests for app.derivatives (K200 선물 만기일 순수 계산부, PLAN.md §4.5-3).

둘째 목요일 계산이 핵심이라 여러 달의 손계산 픽스처로 검증한다(달력 경계, 만기 당일,
네 마녀의 날 여부 포함).
"""

from __future__ import annotations

import datetime as dt

from app.derivatives import (
    days_to_expiry,
    expiries_between,
    is_quadruple_witching,
    next_futures_expiry,
)


# ---------------------------------------------------------------------------
# next_futures_expiry — 둘째 목요일 계산, 달력 경계, 만기 당일 처리
# ---------------------------------------------------------------------------


def test_next_futures_expiry_before_second_thursday_same_month():
    # 2026-07-01(수) -> 7월 둘째 목요일은 2026-07-09.
    assert next_futures_expiry(dt.date(2026, 7, 1)) == dt.date(2026, 7, 9)


def test_next_futures_expiry_on_expiry_day_returns_same_day():
    # 만기 당일(둘째 목요일 그 자체)이면 D-0으로 당일을 반환한다.
    assert next_futures_expiry(dt.date(2026, 7, 9)) == dt.date(2026, 7, 9)


def test_next_futures_expiry_after_second_thursday_rolls_to_next_month():
    # 2026-07-19(일, 7월 만기 이후) -> 다음 만기는 8월 둘째 목요일(2026-08-13).
    assert next_futures_expiry(dt.date(2026, 7, 19)) == dt.date(2026, 8, 13)


def test_next_futures_expiry_crosses_year_boundary():
    # 12월 만기(2026-12-10) 이후 -> 다음 만기는 이듬해 1월 둘째 목요일.
    result = next_futures_expiry(dt.date(2026, 12, 15))
    assert result == dt.date(2027, 1, 14)


def test_next_futures_expiry_various_first_weekdays():
    # 그 달 1일의 요일이 다양한 경우에도 둘째 목요일 산식이 맞는지 교차 확인.
    assert next_futures_expiry(dt.date(2026, 3, 1)) == dt.date(2026, 3, 12)  # 1일=일요일
    assert next_futures_expiry(dt.date(2026, 6, 1)) == dt.date(2026, 6, 11)  # 1일=월요일
    assert next_futures_expiry(dt.date(2026, 9, 1)) == dt.date(2026, 9, 10)  # 1일=화요일
    assert next_futures_expiry(dt.date(2026, 1, 1)) == dt.date(2026, 1, 8)  # 1일=목요일(당월 첫 목요일이 1일인 경우)


# ---------------------------------------------------------------------------
# is_quadruple_witching — 네 마녀의 날(3/6/9/12월 둘째 목요일)
# ---------------------------------------------------------------------------


def test_is_quadruple_witching_true_for_quarterly_second_thursday():
    assert is_quadruple_witching(dt.date(2026, 3, 12)) is True
    assert is_quadruple_witching(dt.date(2026, 6, 11)) is True
    assert is_quadruple_witching(dt.date(2026, 9, 10)) is True
    assert is_quadruple_witching(dt.date(2026, 12, 10)) is True


def test_is_quadruple_witching_false_for_non_quarterly_month():
    # 7월은 분기월이 아니므로 둘째 목요일이어도 네 마녀의 날이 아니다.
    assert is_quadruple_witching(dt.date(2026, 7, 9)) is False


def test_is_quadruple_witching_false_for_quarterly_month_wrong_day():
    # 분기월이어도 둘째 목요일이 아니면 False.
    assert is_quadruple_witching(dt.date(2026, 3, 1)) is False
    assert is_quadruple_witching(dt.date(2026, 3, 19)) is False


# ---------------------------------------------------------------------------
# days_to_expiry — D-day
# ---------------------------------------------------------------------------


def test_days_to_expiry_positive_countdown():
    # 2026-07-01 -> 7월 만기(07-09)까지 8일.
    assert days_to_expiry(dt.date(2026, 7, 1)) == 8


def test_days_to_expiry_zero_on_expiry_day():
    assert days_to_expiry(dt.date(2026, 7, 9)) == 0


def test_days_to_expiry_after_rollover():
    # 2026-07-19 -> 다음 만기 08-13까지 25일.
    assert days_to_expiry(dt.date(2026, 7, 19)) == 25


# ---------------------------------------------------------------------------
# expiries_between — 구간 내 모든 과거 만기일 나열 (PLAN.md §5.42-1)
# ---------------------------------------------------------------------------


def test_expiries_between_multi_year_range_has_twelve_per_year():
    # 2024-01-01~2025-12-31 만 2년 -> 정확히 24개(매달 1개), 연도 경계(12월->1월)도
    # 자연스럽게 넘어가는지 함께 확인.
    result = expiries_between(dt.date(2024, 1, 1), dt.date(2025, 12, 31))
    assert len(result) == 24
    assert result == sorted(result)
    assert result[0] == dt.date(2024, 1, 11)  # 2024-01 둘째 목요일
    assert result[-1] == dt.date(2025, 12, 11)  # 2025-12 둘째 목요일
    # 연도 경계를 건너 12월->1월로 넘어가는 지점이 끊기지 않았는지.
    dec_2024 = dt.date(2024, 12, 12)
    jan_2025 = dt.date(2025, 1, 9)
    assert dec_2024 in result
    assert jan_2025 in result
    assert result.index(jan_2025) == result.index(dec_2024) + 1


def test_expiries_between_short_range_within_one_month():
    # 둘째 목요일 이전에서 끝나는 짧은 구간 -> 그 달 만기가 아직 안 왔으므로 빈 리스트.
    assert expiries_between(dt.date(2026, 7, 1), dt.date(2026, 7, 8)) == []
    # 둘째 목요일을 포함하도록 하루만 늘리면 1개.
    assert expiries_between(dt.date(2026, 7, 1), dt.date(2026, 7, 9)) == [dt.date(2026, 7, 9)]


def test_expiries_between_boundary_inclusion_exact_expiry_dates():
    # start/end가 정확히 만기일이면 양끝 다 포함된다.
    start = dt.date(2026, 6, 11)  # 2026-06 둘째 목요일
    end = dt.date(2026, 7, 9)  # 2026-07 둘째 목요일
    assert expiries_between(start, end) == [start, end]
    # start를 하루 뒤로 밀면 6월 만기가 빠진다.
    assert expiries_between(start + dt.timedelta(days=1), end) == [end]
    # end를 하루 앞으로 당기면 7월 만기가 빠진다.
    assert expiries_between(start, end - dt.timedelta(days=1)) == [start]


def test_expiries_between_start_after_end_returns_empty():
    assert expiries_between(dt.date(2026, 7, 9), dt.date(2026, 7, 1)) == []
