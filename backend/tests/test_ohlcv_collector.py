"""Unit tests for app.collectors.ohlcv.fetch_market_rows (yfinance-first, 네이버 폴백).

No real network/DB involved — _fetch_yfinance and naver_index.fetch_index_series are
monkeypatched. This pins down the fallback order described in PLAN.md §5.4/§7: KRX
Open API 403 -> yfinance(kospi/kosdaq) 1차, 실패 시 네이버; k200_futures는 yfinance에
심볼이 없어 네이버로 직행.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.collectors import ohlcv

START = dt.date(2026, 7, 1)
END = dt.date(2026, 7, 16)

FAKE_YF_ROWS = [
    {"date": dt.date(2026, 7, 16), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100}
]
FAKE_NAVER_ROWS = [
    {"date": dt.date(2026, 7, 16), "open": 9.0, "high": 9.5, "low": 8.5, "close": 9.1, "volume": 999}
]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    # Skip the real NAVER_REQUEST_DELAY_SECONDS sleep so tests run instantly.
    monkeypatch.setattr(ohlcv.time, "sleep", lambda _seconds: None)


def test_kospi_uses_yfinance_when_it_succeeds(monkeypatch):
    calls = {"yf": 0, "naver": 0}

    def fake_yf(ticker, start, end):
        calls["yf"] += 1
        assert ticker == "^KS11"
        return FAKE_YF_ROWS

    def fake_naver(market, start, end):
        calls["naver"] += 1
        return FAKE_NAVER_ROWS

    monkeypatch.setattr(ohlcv, "_fetch_yfinance", fake_yf)
    monkeypatch.setattr(ohlcv.naver_index, "fetch_index_series", fake_naver)

    rows = ohlcv.fetch_market_rows("kospi", START, END)

    assert rows == FAKE_YF_ROWS
    assert calls == {"yf": 1, "naver": 0}


def test_kosdaq_falls_back_to_naver_when_yfinance_raises(monkeypatch):
    calls = {"yf": 0, "naver": 0}

    def fake_yf(ticker, start, end):
        calls["yf"] += 1
        raise RuntimeError("HTTP 429")

    def fake_naver(market, start, end):
        calls["naver"] += 1
        assert market == "kosdaq"
        return FAKE_NAVER_ROWS

    monkeypatch.setattr(ohlcv, "_fetch_yfinance", fake_yf)
    monkeypatch.setattr(ohlcv.naver_index, "fetch_index_series", fake_naver)

    rows = ohlcv.fetch_market_rows("kosdaq", START, END)

    assert rows == FAKE_NAVER_ROWS
    assert calls == {"yf": 1, "naver": 1}


def test_kospi_falls_back_to_naver_when_yfinance_returns_empty(monkeypatch):
    def fake_yf(ticker, start, end):
        return []

    def fake_naver(market, start, end):
        return FAKE_NAVER_ROWS

    monkeypatch.setattr(ohlcv, "_fetch_yfinance", fake_yf)
    monkeypatch.setattr(ohlcv.naver_index, "fetch_index_series", fake_naver)

    rows = ohlcv.fetch_market_rows("kospi", START, END)

    assert rows == FAKE_NAVER_ROWS


def test_k200_futures_goes_straight_to_naver_no_yfinance_ticker(monkeypatch):
    calls = {"yf": 0, "naver": 0}

    def fake_yf(ticker, start, end):
        calls["yf"] += 1
        return FAKE_YF_ROWS

    def fake_naver(market, start, end):
        calls["naver"] += 1
        assert market == "k200_futures"
        return FAKE_NAVER_ROWS

    monkeypatch.setattr(ohlcv, "_fetch_yfinance", fake_yf)
    monkeypatch.setattr(ohlcv.naver_index, "fetch_index_series", fake_naver)

    rows = ohlcv.fetch_market_rows("k200_futures", START, END)

    assert rows == FAKE_NAVER_ROWS
    assert calls == {"yf": 0, "naver": 1}
