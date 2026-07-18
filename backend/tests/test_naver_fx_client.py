"""Unit tests for app.clients.naver_fx (m.stock.naver.com USD/KRW 시세 + FRED 폴백).

No real network involved. Fixtures reproduce the shape of the actual
m.stock.naver.com/front-api/marketIndex/prices response captured on 2026-07-18
(PLAN.md §3) — trimmed to the fields this client parses (localTradedAt, closePrice).
"""

from __future__ import annotations

import datetime as dt

import pytest
import requests

from app.clients import naver_fx


class _FakeResponse:
    def __init__(self, json_data: dict, status_code: int = 200):
        self._json_data = json_data
        self.status_code = status_code
        self.text = str(json_data)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._json_data


def _page(rows: list[tuple[str, str]]) -> dict:
    """rows: list of (localTradedAt, closePrice) -> naver response envelope."""
    return {
        "isSuccess": True,
        "detailCode": "",
        "message": "",
        "result": [{"localTradedAt": d, "closePrice": c} for d, c in rows],
    }


def test_fetch_usdkrw_naver_single_page_parses_and_sorts_ascending(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return _FakeResponse(
            _page(
                [
                    ("2026-07-16", "1,490.00"),
                    ("2026-07-15", "1,488.50"),
                    ("2026-07-14", "1,491.00"),
                ]
            )
        )

    monkeypatch.setattr(naver_fx.requests, "get", fake_get)

    rows = naver_fx.fetch_usdkrw_naver(dt.date(2026, 7, 14), dt.date(2026, 7, 16))

    assert captured["url"] == naver_fx.PRICES_URL
    assert captured["params"]["category"] == "exchange"
    assert captured["params"]["reutersCode"] == "FX_USDKRW"
    assert captured["params"]["pageSize"] >= 10

    assert rows == [
        {"date": dt.date(2026, 7, 14), "value": 1491.00},
        {"date": dt.date(2026, 7, 15), "value": 1488.50},
        {"date": dt.date(2026, 7, 16), "value": 1490.00},
    ]


def test_fetch_usdkrw_naver_user_agent_header_set(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        assert headers is not None
        assert headers.get("User-Agent") == naver_fx.USER_AGENT
        return _FakeResponse(_page([("2026-07-16", "1,490.00")]))

    monkeypatch.setattr(naver_fx.requests, "get", fake_get)

    naver_fx.fetch_usdkrw_naver(dt.date(2026, 7, 16), dt.date(2026, 7, 16))


def test_fetch_usdkrw_naver_pages_backward_and_stops_when_out_of_range(monkeypatch):
    calls = []

    page1 = _page(
        [
            ("2026-07-16", "1,490.00"),
            ("2026-07-15", "1,488.50"),
            ("2026-07-14", "1,491.00"),
        ]
    )
    # page2's oldest row (2026-07-10) is older than requested start(2026-07-13) ->
    # loop should stop after this page, keeping only in-range rows from it.
    page2 = _page(
        [
            ("2026-07-13", "1,499.50"),
            ("2026-07-10", "1,500.00"),
        ]
    )

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(dict(params))
        page = params["page"]
        if page == 1:
            return _FakeResponse(page1)
        elif page == 2:
            return _FakeResponse(page2)
        raise AssertionError(f"unexpected page requested: {page}")

    monkeypatch.setattr(naver_fx.requests, "get", fake_get)

    rows = naver_fx.fetch_usdkrw_naver(dt.date(2026, 7, 13), dt.date(2026, 7, 16))

    assert [c["page"] for c in calls] == [1, 2]
    assert all(c["pageSize"] >= 10 for c in calls)

    # 2026-07-10 is out of [start, end] and excluded; loop stopped after page 2.
    assert rows == [
        {"date": dt.date(2026, 7, 13), "value": 1499.50},
        {"date": dt.date(2026, 7, 14), "value": 1491.00},
        {"date": dt.date(2026, 7, 15), "value": 1488.50},
        {"date": dt.date(2026, 7, 16), "value": 1490.00},
    ]


def test_fetch_usdkrw_naver_stops_on_empty_result_page(monkeypatch):
    calls = []

    page1 = _page([("2026-07-16", "1,490.00")])
    empty_page = {"isSuccess": True, "detailCode": "", "message": "", "result": []}

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(params["page"])
        if params["page"] == 1:
            return _FakeResponse(page1)
        return _FakeResponse(empty_page)

    monkeypatch.setattr(naver_fx.requests, "get", fake_get)

    rows = naver_fx.fetch_usdkrw_naver(dt.date(2020, 1, 1), dt.date(2026, 7, 16))

    assert calls == [1, 2]
    assert rows == [{"date": dt.date(2026, 7, 16), "value": 1490.00}]


def test_fetch_usdkrw_tags_source_naver_on_success(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse(_page([("2026-07-16", "1,490.00")]))

    monkeypatch.setattr(naver_fx.requests, "get", fake_get)

    rows = naver_fx.fetch_usdkrw(dt.date(2026, 7, 16), dt.date(2026, 7, 16))

    assert rows == [{"date": dt.date(2026, 7, 16), "value": 1490.00, "source": "naver"}]


def test_fetch_usdkrw_falls_back_to_fred_on_naver_failure(monkeypatch):
    def fake_naver(start, end, timeout=15):
        raise naver_fx.NaverFxError("boom")

    monkeypatch.setattr(naver_fx, "fetch_usdkrw_naver", fake_naver)

    fred_calls = []

    def fake_fetch_fred(series_id, start, end, timeout=15):
        fred_calls.append(series_id)
        return [{"date": dt.date(2026, 7, 16), "value": 1491.0}]

    monkeypatch.setattr(naver_fx.commodities, "_fetch_fred", fake_fetch_fred)

    rows = naver_fx.fetch_usdkrw(dt.date(2026, 7, 16), dt.date(2026, 7, 16))

    assert fred_calls == [naver_fx.FRED_SERIES_ID]
    assert rows == [{"date": dt.date(2026, 7, 16), "value": 1491.0, "source": "fred"}]


def test_fetch_usdkrw_naver_raises_on_unexpected_result_shape(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse(
            {"isSuccess": False, "detailCode": "", "message": "bad", "result": "not a list"}
        )

    monkeypatch.setattr(naver_fx.requests, "get", fake_get)

    with pytest.raises(naver_fx.NaverFxError):
        naver_fx.fetch_usdkrw_naver(dt.date(2026, 7, 16), dt.date(2026, 7, 16))
