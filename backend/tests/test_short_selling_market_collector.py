"""Unit tests for app.collectors.short_selling_market.collect (PLAN.md §5.32-2).

No real network/DB involved — clients.krx_short_selling.fetch_market_short_selling
is monkeypatched (same style as tests/test_program_flow_collector.py) and the DB
session is a FakeSession that captures pg_insert statements. Pins down the
module docstring's design decisions:

1. kospi/kosdaq are each fetched and upserted independently into
   short_selling_market (not macro_series — this data has 6 fields per day,
   richer than macro_series' single value).
2. A market whose fetch raises is skipped (logged, not propagated) so the
   other market's rows still get upserted — same isolation policy as
   collectors/macro.py's per-series try/except around KOFIA.
3. Every upserted row carries all 6 parsed fields for that market/date.
"""

from __future__ import annotations

import datetime as dt

from app.collectors import short_selling_market

DATE = dt.date(2026, 7, 27)


class FakeSession:
    def __init__(self):
        self.executed = []

    async def execute(self, stmt):
        self.executed.append(stmt)


def _row(date: dt.date, short_value: int, ratio: float) -> dict:
    return {
        "date": date,
        "short_volume": short_value * 10,
        "short_value": short_value,
        "total_volume": short_value * 200,
        "total_value": short_value * 20,
        "volume_ratio": ratio,
        "value_ratio": ratio + 0.5,
    }


async def test_collect_upserts_rows_for_both_markets(monkeypatch):
    responses = {
        "kospi": [_row(dt.date(2026, 7, 24), 1_708_956, 4.24), _row(dt.date(2026, 7, 23), 1_995_708, 5.59)],
        "kosdaq": [_row(dt.date(2026, 7, 24), 289_162, 4.66)],
    }

    def fake_fetch(market, start, end, timeout=15):
        return responses[market]

    monkeypatch.setattr(short_selling_market.krx_short_selling, "fetch_market_short_selling", fake_fetch)

    session = FakeSession()
    rows_written = await short_selling_market.collect(session, DATE)

    assert rows_written == 3
    assert len(session.executed) == 3

    params = [stmt.compile().params for stmt in session.executed]
    by_market = {}
    for p in params:
        by_market.setdefault(p["market"], []).append(p)

    assert set(by_market) == {"kospi", "kosdaq"}
    assert len(by_market["kospi"]) == 2
    assert len(by_market["kosdaq"]) == 1

    kosdaq_row = by_market["kosdaq"][0]
    assert kosdaq_row["date"] == dt.date(2026, 7, 24)
    assert kosdaq_row["short_value"] == 289_162
    assert kosdaq_row["value_ratio"] == 5.16


async def test_collect_skips_market_that_raises_without_failing_the_other(monkeypatch):
    def fake_fetch(market, start, end, timeout=15):
        if market == "kospi":
            raise RuntimeError("krx unavailable")
        return [_row(dt.date(2026, 7, 24), 100, 1.0)]

    monkeypatch.setattr(short_selling_market.krx_short_selling, "fetch_market_short_selling", fake_fetch)

    session = FakeSession()
    rows_written = await short_selling_market.collect(session, DATE)

    assert rows_written == 1
    params = [stmt.compile().params for stmt in session.executed]
    assert all(p["market"] == "kosdaq" for p in params)
