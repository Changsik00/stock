"""Unit tests for app.collectors.program_flow.collect (Kiwoom ka90010 기반).

No real network/DB involved — KiwoomClient.program_trading_by_date is
monkeypatched (same style as tests/test_market_flow_collector.py) and the DB
session is a FakeSession that captures pg_insert statements. Pins down the
module docstring's design decisions:

1. One API call per market returns *multiple* days (unlike ka10051's one-call-
   per-day) — collect() upserts every parsed row from that single page, not
   just target_date.
2. arb (차익) rows go to series `prog_arb_{market}`, non-arb (비차익) rows go
   to `prog_nonarb_{market}` — both derived from the same response rows.
3. `cntr_tm` (YYYYMMDDHHmmss, always midnight) is parsed down to a plain date.
4. A market with no rows degrades to 0 rows for that market, it does not raise.
5. mrkt_tp sent to the client is the empirically-corrected KOSDAQ code
   (P101_AL02), not the documented-but-wrong P001_AL02 (see module docstring).
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.collectors import program_flow

DATE = dt.date(2026, 7, 19)


class FakeSession:
    def __init__(self):
        self.executed = []

    async def execute(self, stmt):
        self.executed.append(stmt)


def _row(date_str: str, dfrt_net: str, ndiffpro_net: str) -> dict:
    return {
        "cntr_tm": f"{date_str}000000",
        "dfrt_trde_sel": "0",
        "dfrt_trde_buy": "0",
        "dfrt_trde_netprps": dfrt_net,
        "ndiffpro_trde_sel": "0",
        "ndiffpro_trde_buy": "0",
        "ndiffpro_trde_netprps": ndiffpro_net,
        "all_sel": "0",
        "all_buy": "0",
        "all_netprps": "0",
        "kospi200": "+1080.36",
        "basis": "15.99",
    }


class FakeKiwoomClient:
    """Stand-in for app.clients.kiwoom.KiwoomClient used via `async with`."""

    def __init__(self, responses):
        self._responses = responses
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def program_trading_by_date(self, mrkt_tp, date, **kwargs):
        self.calls.append((mrkt_tp, date))
        data = self._responses.get(mrkt_tp, {"prm_trde_trnsn": []})
        return data, {"cont-yn": "N", "next-key": "", "api-id": "ka90010"}


def _patch_client(monkeypatch, responses):
    fake = FakeKiwoomClient(responses)
    monkeypatch.setattr(program_flow, "KiwoomClient", lambda: fake)
    return fake


async def test_collect_upserts_both_series_for_every_row_in_the_page(monkeypatch):
    responses = {
        "P001_AL01": {
            "prm_trde_trnsn": [
                _row("20260717", "+572604", "+1380323"),
                _row("20260716", "-222204", "-1696493"),
            ]
        },
        "P101_AL02": {
            "prm_trde_trnsn": [
                _row("20260717", "+1407", "-386600"),
            ]
        },
    }
    fake = _patch_client(monkeypatch, responses)

    session = FakeSession()
    rows_written = await program_flow.collect(session, DATE)

    # kospi: 2 dates x 2 series = 4, kosdaq: 1 date x 2 series = 2 -> 6 total.
    assert rows_written == 6
    assert len(session.executed) == 6

    params = [stmt.compile().params for stmt in session.executed]
    by_series = {}
    for p in params:
        by_series.setdefault(p["series"], []).append(p)

    assert set(by_series) == {
        "prog_arb_kospi",
        "prog_nonarb_kospi",
        "prog_arb_kosdaq",
        "prog_nonarb_kosdaq",
    }
    assert len(by_series["prog_arb_kospi"]) == 2
    assert len(by_series["prog_nonarb_kospi"]) == 2
    assert len(by_series["prog_arb_kosdaq"]) == 1
    assert len(by_series["prog_nonarb_kosdaq"]) == 1

    for p in params:
        assert p["source"] == "kiwoom"

    arb_by_date = {p["date"]: p["value"] for p in by_series["prog_arb_kospi"]}
    assert arb_by_date[dt.date(2026, 7, 17)] == 572604
    assert arb_by_date[dt.date(2026, 7, 16)] == -222204

    nonarb_kosdaq = by_series["prog_nonarb_kosdaq"][0]
    assert nonarb_kosdaq["value"] == -386600
    assert nonarb_kosdaq["date"] == dt.date(2026, 7, 17)

    # The client must be called with the empirically-corrected KOSDAQ code.
    called_mrkt_tps = {c[0] for c in fake.calls}
    assert called_mrkt_tps == {"P001_AL01", "P101_AL02"}


async def test_collect_skips_market_with_no_rows(monkeypatch):
    responses = {
        "P001_AL01": {"prm_trde_trnsn": []},
        "P101_AL02": {"prm_trde_trnsn": [_row("20260717", "+1", "-1")]},
    }
    _patch_client(monkeypatch, responses)

    session = FakeSession()
    rows_written = await program_flow.collect(session, DATE)

    assert rows_written == 2  # only kosdaq contributes (1 date x 2 series)
    params = [stmt.compile().params for stmt in session.executed]
    assert all(p["series"].endswith("kosdaq") for p in params)


async def test_fetch_page_parses_cntr_tm_and_skips_malformed_rows(monkeypatch):
    responses = {
        "P001_AL01": {
            "prm_trde_trnsn": [
                _row("20260717", "+5", "-6"),
                {"cntr_tm": "bad", "dfrt_trde_netprps": "0", "ndiffpro_trde_netprps": "0"},
                {"dfrt_trde_netprps": "0", "ndiffpro_trde_netprps": "0"},  # missing cntr_tm
            ]
        }
    }
    fake = FakeKiwoomClient(responses)

    parsed, headers = await program_flow._fetch_page(fake, "kospi", DATE)

    assert len(parsed) == 1
    assert parsed[0] == {"date": dt.date(2026, 7, 17), "arb_net": 5, "nonarb_net": -6}
    assert headers["cont-yn"] == "N"


def test_parse_int_handles_signed_and_comma_strings():
    assert program_flow._parse_int(None) is None
    assert program_flow._parse_int("") is None
    assert program_flow._parse_int("0") == 0
    assert program_flow._parse_int("-0") == 0
    assert program_flow._parse_int("+512") == 512
    assert program_flow._parse_int("-222204") == -222204
    assert program_flow._parse_int("1,234") == 1234
    assert program_flow._parse_int("-1,234") == -1234
    assert program_flow._parse_int(789) == 789
    assert program_flow._parse_int("not-a-number") is None
