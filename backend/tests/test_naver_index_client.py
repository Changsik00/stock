"""Unit tests for app.clients.naver_index (fchart siseJson parsing).

No real network involved. The fixture response text below is a trimmed sample of
the actual `fchart.stock.naver.com/siseJson.naver` response captured via curl for
symbol=KOSPI/FUT on 2026-07-17 (see PLAN.md §5.4/§7 — this replaced the KRX Open
API after it started returning 403). The response is *not* strict JSON (mixed
quoting), which is why the client parses it with a regex instead of json.loads.
"""

from __future__ import annotations

import datetime as dt

import pytest
import requests

from app.clients import naver_index

KOSPI_BODY = """
 [['날짜', '시가', '고가', '저가', '종가', '거래량', '외국인소진율'],

\t\t
\t\t\t["20260714", 6769.06, 6979.92, 6448.86, 6856.83, 491600, 0.0],
\t\t
\t\t\t["20260715", 7082.91, 7424.18, 7082.91, 7284.41, 397900, 0.0],
\t\t
\t\t\t["20260716", 6960.5, 6995.93, 6730.87, 6820.6, 424300, 0.0],
\t\t
]
"""

FUT_BODY = """
 [['날짜', '시가', '고가', '저가', '종가', '거래량', '외국인소진율'],

\t\t
\t\t\t["20260716", 1105.0, 1120.35, 1072.9, 1096.35, 156414, 0.0],
\t\t
]
"""


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


def test_fetch_index_series_parses_rows_ascending(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return _FakeResponse(KOSPI_BODY)

    monkeypatch.setattr(naver_index.requests, "get", fake_get)

    rows = naver_index.fetch_index_series("kospi", dt.date(2026, 7, 14), dt.date(2026, 7, 16))

    assert captured["url"] == naver_index.FCHART_URL
    assert captured["params"] == {
        "symbol": "KOSPI",
        "requestType": 1,
        "startTime": "20260714",
        "endTime": "20260716",
        "timeframe": "day",
    }
    assert captured["headers"]["User-Agent"]

    assert rows == [
        {
            "date": dt.date(2026, 7, 14),
            "open": 6769.06,
            "high": 6979.92,
            "low": 6448.86,
            "close": 6856.83,
            "volume": 491600,
        },
        {
            "date": dt.date(2026, 7, 15),
            "open": 7082.91,
            "high": 7424.18,
            "low": 7082.91,
            "close": 7284.41,
            "volume": 397900,
        },
        {
            "date": dt.date(2026, 7, 16),
            "open": 6960.5,
            "high": 6995.93,
            "low": 6730.87,
            "close": 6820.6,
            "volume": 424300,
        },
    ]


def test_fetch_index_series_maps_k200_futures_to_fut_symbol(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["params"] = params
        return _FakeResponse(FUT_BODY)

    monkeypatch.setattr(naver_index.requests, "get", fake_get)

    rows = naver_index.fetch_index_series(
        "k200_futures", dt.date(2026, 7, 16), dt.date(2026, 7, 16)
    )

    assert captured["params"]["symbol"] == "FUT"
    assert rows == [
        {
            "date": dt.date(2026, 7, 16),
            "open": 1105.0,
            "high": 1120.35,
            "low": 1072.9,
            "close": 1096.35,
            "volume": 156414,
        }
    ]


def test_fetch_index_series_raises_on_empty_response(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse(" [['날짜', '시가', '고가', '저가', '종가', '거래량', '외국인소진율'],\n]\n")

    monkeypatch.setattr(naver_index.requests, "get", fake_get)

    with pytest.raises(naver_index.NaverIndexError):
        naver_index.fetch_index_series("kospi", dt.date(2026, 7, 14), dt.date(2026, 7, 16))


def test_fetch_index_series_rejects_unknown_market():
    with pytest.raises(ValueError):
        naver_index.fetch_index_series("nasdaq", dt.date(2026, 7, 14), dt.date(2026, 7, 16))
