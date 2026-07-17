"""Unit tests for app.collectors.flow_rank.collect_flow_rank.

No real network/DB involved — naver_rank fetch functions and the DB upsert helper are
monkeypatched (same pattern as tests/test_ohlcv_collector.py). Pins down the two
deliberate design decisions documented in collectors/flow_rank.py's module docstring:

1. target_date is not sent to the source (it doesn't support a date query) — whatever
   dates the source returns get written, and target_date only affects the message text.
2. kospi + kosdaq candidates are merged and re-ranked by net_value descending into a
   single per-investor rank space (flow_rank has no market column).
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.collectors import flow_rank

DATE1 = dt.date(2026, 7, 15)
DATE2 = dt.date(2026, 7, 16)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(flow_rank.time, "sleep", lambda _seconds: None)


def _blocks_for(market: str) -> list[dict]:
    # kospi has the bigger net_value rows so the merge/re-rank order is verifiable.
    if market == "kospi":
        return [
            {"date": DATE1, "rows": [{"code": "000660", "name": "SK하이닉스", "net_value": 700}]},
            {"date": DATE2, "rows": [{"code": "005930", "name": "삼성전자", "net_value": 500}]},
        ]
    return [
        {"date": DATE1, "rows": [{"code": "069500", "name": "KODEX 200", "net_value": 900}]},
        {"date": DATE2, "rows": [{"code": "122630", "name": "KODEX 레버리지", "net_value": 100}]},
    ]


async def test_collect_flow_rank_merges_markets_and_reranks_by_net_value(monkeypatch):
    upserted: list[tuple] = []

    def fake_fetch_deal_rank(market, investor):
        return _blocks_for(market)

    def fake_fetch_etf_codes():
        return {"069500", "122630"}

    async def fake_upsert(session, date, investor, rows, etf_codes):
        upserted.append((date, investor, list(rows), etf_codes))
        return len(rows)

    monkeypatch.setattr(flow_rank, "_fetch_deal_rank_blocking", fake_fetch_deal_rank)
    monkeypatch.setattr(flow_rank, "_fetch_etf_codes_blocking", fake_fetch_etf_codes)
    monkeypatch.setattr(flow_rank, "_upsert_rank_rows", fake_upsert)

    total, message = await flow_rank.collect_flow_rank(session=None, target_date=DATE2)

    # 2 investors x 2 dates = 4 upsert calls, 2 rows each (kospi+kosdaq each contributed
    # exactly one row per date in the fixture) => total rows = 8.
    assert total == 8
    assert len(upserted) == 4

    foreign_date1 = next(u for u in upserted if u[1] == "foreign" and u[0] == DATE1)
    # KODEX 200 (900) outranks SK하이닉스 (700) once kospi+kosdaq are merged.
    assert foreign_date1[2] == [
        {"code": "069500", "name": "KODEX 200", "net_value": 900},
        {"code": "000660", "name": "SK하이닉스", "net_value": 700},
    ]
    assert foreign_date1[3] == {"069500", "122630"}

    # target_date (DATE2) is one of the dates actually returned -> no "ignored" note.
    assert message is not None
    assert "무시됨" not in message
    assert DATE1.isoformat() in message
    assert DATE2.isoformat() in message


async def test_collect_flow_rank_notes_when_target_date_not_returned(monkeypatch):
    monkeypatch.setattr(flow_rank, "_fetch_deal_rank_blocking", lambda market, investor: _blocks_for(market))
    monkeypatch.setattr(flow_rank, "_fetch_etf_codes_blocking", lambda: set())

    async def fake_upsert(session, date, investor, rows, etf_codes):
        return len(rows)

    monkeypatch.setattr(flow_rank, "_upsert_rank_rows", fake_upsert)

    other_date = dt.date(2099, 1, 1)
    _total, message = await flow_rank.collect_flow_rank(session=None, target_date=other_date)

    assert message is not None
    assert "무시됨" in message
    assert other_date.isoformat() in message


async def test_collect_flow_rank_queries_both_markets_for_both_investors(monkeypatch):
    calls = []

    def fake_fetch(market, investor):
        calls.append((market, investor))
        return _blocks_for(market)

    async def fake_upsert(session, date, investor, rows, etf_codes):
        return len(rows)

    monkeypatch.setattr(flow_rank, "_fetch_deal_rank_blocking", fake_fetch)
    monkeypatch.setattr(flow_rank, "_fetch_etf_codes_blocking", lambda: set())
    monkeypatch.setattr(flow_rank, "_upsert_rank_rows", fake_upsert)

    await flow_rank.collect_flow_rank(session=None, target_date=DATE2)

    assert sorted(calls) == [
        ("kosdaq", "foreign"),
        ("kosdaq", "institution"),
        ("kospi", "foreign"),
        ("kospi", "institution"),
    ]
