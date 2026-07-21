"""Unit tests for app.clients.us_indices (전일 미국장 4대 지수, PLAN.md §5.8).

No real network involved — commodities._fetch_yfinance/_fetch_fred (재사용되는
공용 헬퍼) are monkeypatched, mirroring test_naver_fx_client.py's pattern for
pinning down the yfinance -> FRED fallback order.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.clients import commodities, us_indices

START = dt.date(2026, 7, 12)
END = dt.date(2026, 7, 21)


def test_fetch_us_index_series_tags_source_yfinance_on_success(monkeypatch):
    calls = []

    def fake_yfinance(symbol, start, end):
        calls.append(symbol)
        return [{"date": dt.date(2026, 7, 21), "value": 5555.0}]

    monkeypatch.setattr(commodities, "_fetch_yfinance", fake_yfinance)

    rows = us_indices.fetch_us_index_series("us_sp500", START, END)

    assert calls == ["^GSPC"]
    assert rows == [{"date": dt.date(2026, 7, 21), "value": 5555.0, "source": "yfinance"}]


def test_fetch_us_index_series_sorts_ascending(monkeypatch):
    def fake_yfinance(symbol, start, end):
        return [
            {"date": dt.date(2026, 7, 21), "value": 2.0},
            {"date": dt.date(2026, 7, 18), "value": 1.0},
        ]

    monkeypatch.setattr(commodities, "_fetch_yfinance", fake_yfinance)

    rows = us_indices.fetch_us_index_series("us_nasdaq", START, END)

    assert [r["date"] for r in rows] == [dt.date(2026, 7, 18), dt.date(2026, 7, 21)]


@pytest.mark.parametrize(
    ("series", "fred_id"),
    [("us_sp500", "SP500"), ("us_nasdaq", "NASDAQCOM")],
)
def test_fetch_us_index_series_falls_back_to_fred_when_available(monkeypatch, series, fred_id):
    def fake_yfinance(symbol, start, end):
        raise RuntimeError("HTTP 429")

    fred_calls = []

    def fake_fred(series_id, start, end):
        fred_calls.append(series_id)
        return [{"date": dt.date(2026, 7, 21), "value": 9.0}]

    monkeypatch.setattr(commodities, "_fetch_yfinance", fake_yfinance)
    monkeypatch.setattr(commodities, "_fetch_fred", fake_fred)

    rows = us_indices.fetch_us_index_series(series, START, END)

    assert fred_calls == [fred_id]
    assert rows == [{"date": dt.date(2026, 7, 21), "value": 9.0, "source": "fred"}]


@pytest.mark.parametrize("series", ["us_dow", "us_sox"])
def test_fetch_us_index_series_no_fred_fallback_propagates_error(monkeypatch, series):
    """다우/SOX는 FRED에 무료 대체 시리즈가 없어(SYMBOLS[...]["fred"] is None)
    yfinance 실패 시 폴백 없이 원래 예외를 그대로 전파해야 한다."""

    def fake_yfinance(symbol, start, end):
        raise RuntimeError("HTTP 429")

    fred_calls = []

    def fake_fred(series_id, start, end):
        fred_calls.append(series_id)
        raise AssertionError("FRED should not be called for dow/sox")

    monkeypatch.setattr(commodities, "_fetch_yfinance", fake_yfinance)
    monkeypatch.setattr(commodities, "_fetch_fred", fake_fred)

    with pytest.raises(RuntimeError, match="HTTP 429"):
        us_indices.fetch_us_index_series(series, START, END)

    assert fred_calls == []


def test_fetch_us_index_series_unknown_series_raises_value_error():
    with pytest.raises(ValueError, match="unknown us index series"):
        us_indices.fetch_us_index_series("us_ftse", START, END)


def test_symbols_cover_expected_four_series():
    assert set(us_indices.SYMBOLS) == {"us_sp500", "us_nasdaq", "us_dow", "us_sox"}
    assert us_indices.SYMBOLS["us_dow"]["fred"] is None
    assert us_indices.SYMBOLS["us_sox"]["fred"] is None
    assert us_indices.SYMBOLS["us_sp500"]["fred"] == "SP500"
    assert us_indices.SYMBOLS["us_nasdaq"]["fred"] == "NASDAQCOM"
