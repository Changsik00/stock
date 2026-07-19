"""Unit tests for app.market_hours — extracted from routers/markets.py
(PLAN.md 서버 측 능동 60초 갱신 작업, 2026-07-20) so routers/markets.py and
collectors/live_refresh.py can share the same "정규장 개장 여부" logic without
duplication.
"""

from __future__ import annotations

import datetime as dt

from app.market_hours import KST, is_market_closed


def test_open_during_regular_hours_on_a_weekday():
    # 2026-07-20 is a Monday.
    during = dt.datetime(2026, 7, 20, 10, 0, tzinfo=KST)
    assert is_market_closed(during) is False


def test_closed_before_open_time():
    before_open = dt.datetime(2026, 7, 20, 8, 59, tzinfo=KST)
    assert is_market_closed(before_open) is True


def test_closed_at_and_after_close_time():
    at_close = dt.datetime(2026, 7, 20, 15, 30, tzinfo=KST)
    after_close = dt.datetime(2026, 7, 20, 16, 0, tzinfo=KST)
    assert is_market_closed(at_close) is True
    assert is_market_closed(after_close) is True


def test_closed_on_weekend_even_during_regular_hours():
    # 2026-07-19 is a Sunday, 2026-07-25 is a Saturday.
    sunday = dt.datetime(2026, 7, 19, 10, 0, tzinfo=KST)
    saturday = dt.datetime(2026, 7, 25, 10, 0, tzinfo=KST)
    assert is_market_closed(sunday) is True
    assert is_market_closed(saturday) is True


def test_closed_just_before_midnight_on_sunday_not_treated_as_open():
    # Regression guard for the 2026-07-19 fix noted in market_hours.py's
    # docstring: a naive "after 15:30" check used to misclassify Sunday
    # early-morning hours as open.
    sunday_early_morning = dt.datetime(2026, 7, 19, 1, 0, tzinfo=KST)
    assert is_market_closed(sunday_early_morning) is True
