"""Unit tests for app.clients.naver_value_rank (거래량 상위 API 전 종목 순회 +
거래대금 재정렬 — PLAN.md §4.6 3.6-1).

No real network involved. The fixture rows below are a trimmed shape of the real
``m.stock.naver.com/api/stocks/quantTop/{KOSPI|KOSDAQ}`` response captured via curl
on 2026-07-18 (see naver_value_rank.py module docstring for the full field-unit
investigation — accumulatedTradingValueRaw/marketValueRaw are 원 단위 strings that
this client divides by 1e6 to get 백만원).
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.clients import naver_value_rank


def _stock(
    code: str,
    name: str,
    value_raw: str,
    market_value_raw: str,
    fluct: str,
    traded_at: str = "2026-07-16T16:10:19+09:00",
    end_type: str = "stock",
):
    return {
        "itemCode": code,
        "stockName": name,
        "stockEndType": end_type,
        "accumulatedTradingValueRaw": value_raw,
        "marketValueRaw": market_value_raw,
        "fluctuationsRatio": fluct,
        "localTradedAt": traded_at,
    }


def test_fetch_quant_top_page_rejects_page_size_over_100():
    with pytest.raises(ValueError):
        naver_value_rank.fetch_quant_top_page("kospi", page=1, page_size=101)


def test_fetch_quant_top_page_rejects_unknown_market():
    with pytest.raises(ValueError):
        naver_value_rank.fetch_quant_top_page("nasdaq", page=1)


def test_fetch_quant_top_page_hits_expected_url_and_params(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"stocks": [], "totalCount": 0, "page": 1, "pageSize": 100}

        return R()

    monkeypatch.setattr(naver_value_rank.requests, "get", fake_get)
    naver_value_rank.fetch_quant_top_page("kosdaq", page=2, page_size=50)

    assert captured["url"] == "https://m.stock.naver.com/api/stocks/quantTop/KOSDAQ"
    assert captured["params"] == {"page": 2, "pageSize": 50}
    assert captured["headers"]["User-Agent"]


def test_fetch_all_paginates_until_empty_page_and_resorts_by_value(monkeypatch):
    """3페이지(pageSize=2)로 나눠 오는 걸 흉내내고, 실제 소스처럼 거래량 순서로
    오는 걸 이 클라이언트가 거래대금 내림차순으로 재정렬하는지 검증한다 (모듈
    docstring "채택한 접근" — SK하이닉스가 거래량 순위로는 뒤에 있어도 거래대금은
    1위인 사례를 그대로 재현)."""
    pages = {
        1: {
            "totalCount": 5,
            "stocks": [
                _stock("252670", "KODEX 200선물인버스2X", "796130000000", "50000000000", "13.64"),
                _stock("114800", "KODEX 인버스", "998297000000", "60000000000", "6.85"),
            ],
        },
        2: {
            "totalCount": 5,
            "stocks": [
                _stock("069500", "KODEX 200", "1938809000000", "24377850000000", "-1.20"),
                _stock("005930", "삼성전자", "6838413000000", "1490801045040000", "-8.77"),
            ],
        },
        3: {
            "totalCount": 5,
            "stocks": [
                _stock("000660", "SK하이닉스", "10261279000000", "312000000000000", "-22.42"),
            ],
        },
    }
    calls = []

    def fake_fetch_page(market, page, page_size=100, timeout=15):
        calls.append(page)
        return pages.get(page, {"totalCount": 5, "stocks": []})

    monkeypatch.setattr(naver_value_rank, "fetch_quant_top_page", fake_fetch_page)

    result = naver_value_rank.fetch_all("kospi", page_size=2)

    assert calls == [1, 2, 3]
    codes_in_order = [r["code"] for r in result["rows"]]
    assert codes_in_order == ["000660", "005930", "069500", "114800", "252670"]

    sk = result["rows"][0]
    assert sk["value_million"] == 10261279
    assert sk["market_value_million"] == 312000000
    assert sk["change_rate"] == pytest.approx(-22.42)

    assert result["date"] == dt.date(2026, 7, 16)


def test_fetch_all_stops_early_when_a_page_comes_back_empty(monkeypatch):
    """totalCount가 부풀려져 있어도(혹은 소스가 마지막 페이지 이후 빈 리스트를
    주는 실측 동작대로) 빈 stocks가 오면 더 순회하지 않는다."""
    calls = []

    def fake_fetch_page(market, page, page_size=100, timeout=15):
        calls.append(page)
        if page == 1:
            return {
                "totalCount": 999,
                "stocks": [_stock("005930", "삼성전자", "6838413000000", "1490801045040000", "-8.77")],
            }
        return {"totalCount": 999, "stocks": []}

    monkeypatch.setattr(naver_value_rank, "fetch_quant_top_page", fake_fetch_page)

    result = naver_value_rank.fetch_all("kospi", page_size=1)

    assert calls == [1, 2]
    assert [r["code"] for r in result["rows"]] == ["005930"]


def test_fetch_all_raises_when_first_page_is_empty(monkeypatch):
    def fake_fetch_page(market, page, page_size=100, timeout=15):
        return {"totalCount": 0, "stocks": []}

    monkeypatch.setattr(naver_value_rank, "fetch_quant_top_page", fake_fetch_page)

    with pytest.raises(naver_value_rank.NaverValueRankError):
        naver_value_rank.fetch_all("kosdaq")


def test_fetch_all_sleeps_between_pages_when_requested(monkeypatch):
    sleep_calls = []
    monkeypatch.setattr(naver_value_rank.time, "sleep", lambda s: sleep_calls.append(s))

    def fake_fetch_page(market, page, page_size=100, timeout=15):
        if page == 1:
            return {
                "totalCount": 2,
                "stocks": [_stock("005930", "삼성전자", "100000000", "1000000000", "1.0")],
            }
        return {
            "totalCount": 2,
            "stocks": [_stock("000660", "SK하이닉스", "200000000", "2000000000", "2.0")],
        }

    monkeypatch.setattr(naver_value_rank, "fetch_quant_top_page", fake_fetch_page)

    naver_value_rank.fetch_all("kospi", page_size=1, sleep_seconds=0.5)

    # page 1 -> no sleep before it; sleep only before page 2.
    assert sleep_calls == [0.5]
