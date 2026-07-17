"""Unit tests for app.collectors.breadth.collect_breadth.

No real network/DB involved — _fetch_breadth_blocking is monkeypatched and upserts
are captured via a fake AsyncSession.execute (same style as
tests/test_flow_rank_collector.py, tests/test_ohlcv_collector.py).
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.collectors import breadth

DATE = dt.date(2026, 7, 18)

KOSPI_BREADTH = {"adv": 384, "dec": 488, "flat": 40, "limit_up": 6, "limit_down": 0}
KOSDAQ_BREADTH = {"adv": 501, "dec": 1182, "flat": 56, "limit_up": 11, "limit_down": 1}


class _FakeSession:
    def __init__(self):
        self.executed: list = []

    async def execute(self, stmt):
        self.executed.append(stmt)


def _values_of(stmt) -> dict:
    # SQLAlchemy Insert construct exposes bound values via .compile().params for
    # simple literal values() calls.
    return dict(stmt.compile().params)


async def test_collect_breadth_upserts_both_markets(monkeypatch):
    def fake_fetch(market):
        return KOSPI_BREADTH if market == "kospi" else KOSDAQ_BREADTH

    monkeypatch.setattr(breadth, "_fetch_breadth_blocking", fake_fetch)

    session = _FakeSession()
    rows, message = await breadth.collect_breadth(session, DATE)

    assert rows == 2
    assert message is None
    assert len(session.executed) == 2

    kospi_values = _values_of(session.executed[0])
    assert kospi_values["market"] == "kospi"
    assert kospi_values["date"] == DATE
    assert kospi_values["adv"] == 384
    assert kospi_values["dec"] == 488
    assert kospi_values["flat"] == 40
    assert kospi_values["limit_up"] == 6
    assert kospi_values["limit_down"] == 0

    kosdaq_values = _values_of(session.executed[1])
    assert kosdaq_values["market"] == "kosdaq"
    assert kosdaq_values["dec"] == 1182


async def test_collect_breadth_survives_one_market_failure(monkeypatch):
    """코스닥 소스 호출이 실패해도 코스피는 정상 적재되고, message에 실패한
    시장이 안내된다."""

    def fake_fetch(market):
        if market == "kosdaq":
            raise RuntimeError("boom")
        return KOSPI_BREADTH

    monkeypatch.setattr(breadth, "_fetch_breadth_blocking", fake_fetch)

    session = _FakeSession()
    rows, message = await breadth.collect_breadth(session, DATE)

    assert rows == 1
    assert len(session.executed) == 1
    assert message is not None
    assert "kosdaq" in message


async def test_collect_breadth_queries_both_markets(monkeypatch):
    calls = []

    def fake_fetch(market):
        calls.append(market)
        return KOSPI_BREADTH if market == "kospi" else KOSDAQ_BREADTH

    monkeypatch.setattr(breadth, "_fetch_breadth_blocking", fake_fetch)

    session = _FakeSession()
    await breadth.collect_breadth(session, DATE)

    assert calls == ["kospi", "kosdaq"]
