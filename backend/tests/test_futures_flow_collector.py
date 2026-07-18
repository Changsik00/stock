"""Unit tests for app.collectors.futures_flow.collect (네이버 m.stock trend 기반,
PLAN.md §4.5 4.5-2).

No real network/DB involved — the blocking fetch wrapper is monkeypatched (same
pattern as tests/test_market_flow_collector.py) and the DB session is a FakeSession
that captures pg_insert statements.
"""

from __future__ import annotations

import datetime as dt

from app.collectors import futures_flow

DATE = dt.date(2026, 7, 16)


class FakeSession:
    def __init__(self):
        self.executed = []

    async def execute(self, stmt):
        self.executed.append(stmt)


def _patch_fetch(monkeypatch, result):
    def fake_fetch(target_date):
        return result

    monkeypatch.setattr(futures_flow, "_fetch_blocking", fake_fetch)


async def test_collect_upserts_3_investor_rows(monkeypatch):
    _patch_fetch(
        monkeypatch,
        {
            "date": DATE,
            "flows": [
                {"investor": "개인", "net_value": -344_200, "net_volume": None},
                {"investor": "외국인", "net_value": 701_400, "net_volume": None},
                {"investor": "기관계", "net_value": -321_000, "net_volume": None},
            ],
        },
    )

    session = FakeSession()
    rows_written = await futures_flow.collect(session, DATE)

    assert rows_written == 3
    assert len(session.executed) == 3

    params = [stmt.compile().params for stmt in session.executed]
    for p in params:
        assert p["market"] == "k200_futures"
        assert p["date"] == DATE
        assert p["source"] == "naver"
        assert p["net_volume"] is None

    by_investor = {p["investor"]: p["net_value"] for p in params}
    assert by_investor == {"개인": -344_200, "외국인": 701_400, "기관계": -321_000}


async def test_collect_returns_0_when_source_has_no_data(monkeypatch):
    """휴장일 등 소스가 None을 돌려주면(clients/naver_futures_flow.py) 크래시하지
    않고 0행으로 건너뛴다."""
    _patch_fetch(monkeypatch, None)

    session = FakeSession()
    rows_written = await futures_flow.collect(session, DATE)

    assert rows_written == 0
    assert session.executed == []
