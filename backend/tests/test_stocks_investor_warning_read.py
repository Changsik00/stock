"""Unit tests for routers.stocks._read_investor_warning_status (PLAN.md
§5.39-2/3). No real DB — a FakeSession returns canned InvestorWarningEvent
rows for the SELECT-by-code query and a canned scalar for the caution
"as-of" MAX(designated_date) query, mirroring the FakeSession style already
used by tests/test_markets_short_selling_router.py.
"""

from __future__ import annotations

import datetime as dt

from app.models import InvestorWarningEvent
from app.routers.stocks import _read_investor_warning_status


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeRowsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    """First execute() call (SELECT InvestorWarningEvent) returns `rows`;
    every subsequent call (SELECT func.max(...)) returns `caution_as_of`."""

    def __init__(self, rows, caution_as_of):
        self._rows = rows
        self._caution_as_of = caution_as_of
        self._call_count = 0

    async def execute(self, stmt):
        self._call_count += 1
        if self._call_count == 1:
            return _FakeRowsResult(self._rows)
        return _FakeScalarResult(self._caution_as_of)


def _event(tier, designated, released=None, market="KOSPI", warning_type=None):
    return InvestorWarningEvent(
        tier=tier,
        raw_name="비비안",
        designated_date=designated,
        code="002070",
        market=market,
        warning_type=warning_type,
        notice_date=designated - dt.timedelta(days=1),
        released_date=released,
    )


async def test_read_investor_warning_status_active_warning():
    session = FakeSession(
        rows=[_event("warning", dt.date(2026, 7, 20), released=None)],
        caution_as_of=dt.date(2026, 7, 30),
    )
    result = await _read_investor_warning_status(session, "002070")

    assert result["active_tier"] == "warning"
    assert result["label"] == "투자경고종목"
    assert result["market"] == "KOSPI"
    assert result["designated_date"] == "20260720"
    assert result["warning_type"] is None


async def test_read_investor_warning_status_no_history_returns_no_active_tier():
    session = FakeSession(rows=[], caution_as_of=None)
    result = await _read_investor_warning_status(session, "005930")

    assert result["active_tier"] is None
    assert result["designated_date"] is None


async def test_read_investor_warning_status_released_row_is_not_active():
    session = FakeSession(
        rows=[_event("warning", dt.date(2026, 4, 28), released=dt.date(2026, 5, 11))],
        caution_as_of=None,
    )
    result = await _read_investor_warning_status(session, "000500")

    assert result["active_tier"] is None
