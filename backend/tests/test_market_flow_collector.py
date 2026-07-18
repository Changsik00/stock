"""Unit tests for app.collectors.market_flow.collect (Kiwoom ka10051 기반).

No real network/DB involved — KiwoomClient.sector_investor_net_buy is monkeypatched
(same style as tests/test_kiwoom_client.py's canned response bodies) and the DB
session is a FakeSession that captures pg_insert statements (same pattern as
tests/test_flow_rank_collector.py::test_upsert_rank_rows_persists_market_column).
Pins down the module docstring's design decisions:

1. Only the "종합" summary row (inds_cd == "001_AL"/"101_AL") is used — other 업종
   rows in inds_netprps are ignored.
2. All 13 KA10051_FIELD_TO_INVESTOR fields are upserted per market, with net_volume
   always None (this source only fetches 금액, not 수량 — see module docstring).
3. A missing/non-matching summary row degrades gracefully to 0 rows for that market,
   it does not raise.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.collectors import market_flow

DATE = dt.date(2026, 7, 16)


class FakeSession:
    def __init__(self):
        self.executed = []

    async def execute(self, stmt):
        self.executed.append(stmt)


def _summary_row(inds_cd: str, inds_nm: str) -> dict:
    row = {"inds_cd": inds_cd, "inds_nm": inds_nm}
    # Exercise both plain-int and comma-formatted-string numeric shapes, per the
    # module docstring's note that Kiwoom numeric fields show up as either.
    values = {
        "sc_netprps": "1,234",
        "insrnc_netprps": "-500",
        "invtrt_netprps": 100,
        "bank_netprps": "50",
        "jnsinkm_netprps": "-1,000",
        "endw_netprps": "0",
        "etc_corp_netprps": "10",
        "ind_netprps": "-9,999",
        "frgnr_netprps": "8888",
        "native_trmt_frgnr_netprps": "1",
        "natn_netprps": "0",
        "samo_fund_netprps": "-20",
        "orgn_netprps": "1234",
    }
    row.update(values)
    return row


class FakeKiwoomClient:
    """Stand-in for app.clients.kiwoom.KiwoomClient used via `async with`."""

    def __init__(self, responses):
        self._responses = responses
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def sector_investor_net_buy(self, mrkt_tp, base_dt, **kwargs):
        self.calls.append((mrkt_tp, base_dt))
        data = self._responses.get(mrkt_tp, {"inds_netprps": []})
        return data, {"cont-yn": "N", "next-key": "", "api-id": "ka10051"}


def _patch_client(monkeypatch, responses):
    fake = FakeKiwoomClient(responses)
    monkeypatch.setattr(market_flow, "KiwoomClient", lambda: fake)
    return fake


async def test_collect_upserts_13_rows_per_market_with_kiwoom_source(monkeypatch):
    responses = {
        "0": {"inds_netprps": [_summary_row("001_AL", "종합(KOSPI)")]},
        "1": {"inds_netprps": [_summary_row("101_AL", "종합(KOSDAQ)")]},
    }
    _patch_client(monkeypatch, responses)

    session = FakeSession()
    rows_written = await market_flow.collect(session, DATE)

    assert rows_written == 26  # 2 markets x 13 investors
    assert len(session.executed) == 26

    params = [stmt.compile().params for stmt in session.executed]
    kospi_params = [p for p in params if p["market"] == "kospi"]
    kosdaq_params = [p for p in params if p["market"] == "kosdaq"]
    assert len(kospi_params) == 13
    assert len(kosdaq_params) == 13

    for p in params:
        assert p["source"] == "kiwoom"
        assert p["date"] == DATE
        assert p["net_volume"] is None

    investors = {p["investor"] for p in kospi_params}
    assert investors == set(market_flow.KA10051_FIELD_TO_INVESTOR.values())

    # spot-check the numeric parsing (comma-formatted string, plain int, negative).
    by_investor = {p["investor"]: p["net_value"] for p in kospi_params}
    assert by_investor["금융투자"] == 1234  # "1,234"
    assert by_investor["투신"] == 100  # plain int
    assert by_investor["개인"] == -9999  # "-9,999"
    assert by_investor["보험"] == -500  # "-500"


async def test_collect_skips_market_when_summary_row_missing(monkeypatch):
    """inds_cd가 기대값과 다르면(또는 목록이 비어 있으면) 크래시하지 않고 해당
    시장은 0행으로 건너뛴다."""
    responses = {
        "0": {"inds_netprps": [{"inds_cd": "002", "inds_nm": "다른 업종"}]},  # no match
        "1": {"inds_netprps": [_summary_row("101_AL", "종합(KOSDAQ)")]},
    }
    _patch_client(monkeypatch, responses)

    session = FakeSession()
    rows_written = await market_flow.collect(session, DATE)

    # kospi contributes 0 rows, kosdaq contributes 13.
    assert rows_written == 13
    params = [stmt.compile().params for stmt in session.executed]
    assert all(p["market"] == "kosdaq" for p in params)


async def test_fetch_kiwoom_flow_picks_summary_row_not_first_row(monkeypatch):
    """방어적으로 inds_cd를 명시적으로 찾아야 한다 — 배열 순서가 바뀌어도(종합 행이
    첫 번째가 아니어도) 올바른 행을 골라야 한다."""
    other_row = {"inds_cd": "003", "inds_nm": "기타업종", "ind_netprps": "0"}
    summary = _summary_row("001_AL", "종합(KOSPI)")
    fake = FakeKiwoomClient({"0": {"inds_netprps": [other_row, summary]}})

    flows = await market_flow._fetch_kiwoom_flow(fake, "kospi", DATE)

    assert len(flows) == 13
    by_investor = {f["investor"]: f["net_value"] for f in flows}
    assert by_investor["개인"] == -9999


def test_parse_int_handles_none_empty_and_signed_comma_strings():
    assert market_flow._parse_int(None) is None
    assert market_flow._parse_int("") is None
    assert market_flow._parse_int(0) == 0
    assert market_flow._parse_int("1,234") == 1234
    assert market_flow._parse_int("-1,234") == -1234
    assert market_flow._parse_int("+56") == 56
    assert market_flow._parse_int(789) == 789
