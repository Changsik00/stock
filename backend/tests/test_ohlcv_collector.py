"""Unit tests for app.collectors.ohlcv.fetch_market_rows (네이버 1차, yfinance 폴백).

No real network/DB involved — _fetch_yfinance and naver_index.fetch_index_series are
monkeypatched. This pins down the fallback order described in PLAN.md §5.4/§7: KRX
Open API 403 -> 네이버(clients/naver_index.py) 1차(kospi/kosdaq/k200_futures 공통),
실패 시 kospi/kosdaq만 yfinance로 폴백(k200_futures는 yfinance 심볼이 없어 예외
전파). 2026-07-17: 코스닥 volume이 yfinance 1차였을 때 대부분 기간 쓰레기 값이었던
게 발견돼 우선순위를 뒤집었다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.collectors import ohlcv

START = dt.date(2026, 7, 1)
END = dt.date(2026, 7, 16)

FAKE_NAVER_ROWS = [
    {"date": dt.date(2026, 7, 16), "open": 9.0, "high": 9.5, "low": 8.5, "close": 9.1, "volume": 999}
]
FAKE_YF_ROWS = [
    {"date": dt.date(2026, 7, 16), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100}
]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    # Skip the real NAVER_REQUEST_DELAY_SECONDS sleep so tests run instantly.
    monkeypatch.setattr(ohlcv.time, "sleep", lambda _seconds: None)


def test_kospi_uses_naver_when_it_succeeds(monkeypatch):
    calls = {"yf": 0, "naver": 0}

    def fake_naver(market, start, end):
        calls["naver"] += 1
        assert market == "kospi"
        return FAKE_NAVER_ROWS

    def fake_yf(ticker, start, end):
        calls["yf"] += 1
        return FAKE_YF_ROWS

    monkeypatch.setattr(ohlcv.naver_index, "fetch_index_series", fake_naver)
    monkeypatch.setattr(ohlcv, "_fetch_yfinance", fake_yf)

    rows, source = ohlcv.fetch_market_rows("kospi", START, END)

    assert rows == FAKE_NAVER_ROWS
    assert source == "naver"
    assert calls == {"yf": 0, "naver": 1}


def test_kosdaq_falls_back_to_yfinance_when_naver_raises(monkeypatch):
    calls = {"yf": 0, "naver": 0}

    def fake_naver(market, start, end):
        calls["naver"] += 1
        raise RuntimeError("HTTP 429")

    def fake_yf(ticker, start, end):
        calls["yf"] += 1
        assert ticker == "^KQ11"
        return FAKE_YF_ROWS

    monkeypatch.setattr(ohlcv.naver_index, "fetch_index_series", fake_naver)
    monkeypatch.setattr(ohlcv, "_fetch_yfinance", fake_yf)

    rows, source = ohlcv.fetch_market_rows("kosdaq", START, END)

    assert rows == FAKE_YF_ROWS
    assert source == "yfinance-fallback"
    assert calls == {"yf": 1, "naver": 1}


def test_kospi_falls_back_to_yfinance_when_naver_returns_empty(monkeypatch):
    def fake_naver(market, start, end):
        return []

    def fake_yf(ticker, start, end):
        return FAKE_YF_ROWS

    monkeypatch.setattr(ohlcv.naver_index, "fetch_index_series", fake_naver)
    monkeypatch.setattr(ohlcv, "_fetch_yfinance", fake_yf)

    rows, source = ohlcv.fetch_market_rows("kospi", START, END)

    assert rows == FAKE_YF_ROWS
    assert source == "yfinance-fallback"


def test_k200_futures_uses_naver_only_no_yfinance_fallback(monkeypatch):
    calls = {"yf": 0, "naver": 0}

    def fake_naver(market, start, end):
        calls["naver"] += 1
        assert market == "k200_futures"
        return FAKE_NAVER_ROWS

    def fake_yf(ticker, start, end):
        calls["yf"] += 1
        return FAKE_YF_ROWS

    monkeypatch.setattr(ohlcv.naver_index, "fetch_index_series", fake_naver)
    monkeypatch.setattr(ohlcv, "_fetch_yfinance", fake_yf)

    rows, source = ohlcv.fetch_market_rows("k200_futures", START, END)

    assert rows == FAKE_NAVER_ROWS
    assert source == "naver"
    assert calls == {"yf": 0, "naver": 1}


def test_k200_futures_raises_when_naver_fails_no_fallback_symbol(monkeypatch):
    def fake_naver(market, start, end):
        raise RuntimeError("network error")

    monkeypatch.setattr(ohlcv.naver_index, "fetch_index_series", fake_naver)

    with pytest.raises(ValueError, match="yfinance 폴백 심볼이 없습니다"):
        ohlcv.fetch_market_rows("k200_futures", START, END)


async def test_collect_ohlcv_message_notes_fallback(monkeypatch):
    """collect_ohlcv는 폴백이 있으면 (rows, message) 튜플을 반환한다."""

    async def fake_upsert_rows(session, market, rows):
        return len(rows)

    def fake_fetch(market, start, end):
        if market == "kosdaq":
            return FAKE_YF_ROWS, "yfinance-fallback"
        return FAKE_NAVER_ROWS, "naver"

    monkeypatch.setattr(ohlcv, "_upsert_rows", fake_upsert_rows)
    monkeypatch.setattr(ohlcv, "fetch_market_rows", fake_fetch)

    total, message = await ohlcv.collect_ohlcv(session=None, target_date=END)

    # kosdaq만 야후 폴백(FAKE_YF_ROWS), 나머지 MARKETS는 전부 네이버(FAKE_NAVER_ROWS) —
    # MARKETS 길이에 하드코딩하지 않아 kospi200 추가(§4.5-3) 같은 향후 확장에도 안전하다.
    naver_markets = len(ohlcv.MARKETS) - 1
    assert total == len(FAKE_NAVER_ROWS) * naver_markets + len(FAKE_YF_ROWS)
    assert message == "폴백 사용: kosdaq=yfinance-fallback"
