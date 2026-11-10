"""app.krx_calendar.is_krx_closed 단위 테스트 (Phase 5.102)."""

import datetime as dt

from app.krx_calendar import is_krx_closed


def test_weekend_closed():
    assert is_krx_closed(dt.date(2026, 11, 7)) is True  # 토
    assert is_krx_closed(dt.date(2026, 11, 8)) is True  # 일


def test_fixed_date_holidays_closed():
    for d in [(2026, 10, 9), (2026, 12, 25), (2026, 12, 31), (2027, 1, 1), (2027, 5, 5)]:
        assert is_krx_closed(dt.date(*d)) is True


def test_known_variable_holidays_closed():
    for d in [(2026, 9, 24), (2026, 9, 25), (2026, 10, 5), (2026, 8, 17), (2026, 7, 17)]:
        assert is_krx_closed(dt.date(*d)) is True


def test_normal_trading_days_open():
    for d in [(2026, 11, 9), (2026, 11, 10), (2026, 10, 8), (2026, 10, 12)]:
        assert is_krx_closed(dt.date(*d)) is False
