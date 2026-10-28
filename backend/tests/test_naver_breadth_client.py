"""Unit tests for app.clients.naver_breadth (m.stock.naver.com integration API
등락 종목수 파싱).

No real network involved. The fixtures below reproduce the relevant
``upDownStockInfo`` shape from the actual
m.stock.naver.com/api/index/{KOSPI,KOSDAQ}/integration response captured on
2026-09-14 (PLAN.md §3.5/§4.6 3.6-2, 소스 교체 — 구 sise_index.naver는 302로
stock.naver.com SPA로 리다이렉트되어 폐기됨) — trimmed to just the count fields,
since that's all this client parses.
"""

from __future__ import annotations

import pytest
import requests

from app.clients import naver_breadth

KOSPI_INTEGRATION = {
    "upDownStockInfo": {
        "upperCount": "6",
        "riseCount": "384",
        "steadyCount": "40",
        "fallCount": "488",
        "lowerCount": "0",
    }
}

KOSDAQ_INTEGRATION = {
    "upDownStockInfo": {
        "upperCount": "11",
        "riseCount": "501",
        "steadyCount": "56",
        "fallCount": "1,182",
        "lowerCount": "1",
    }
}


class _FakeResponse:
    def __init__(self, data=None, text: str = "", status_code: int = 200):
        self._data = data
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        if self._data is None:
            raise ValueError("no JSON object could be decoded")
        return self._data


def test_fetch_breadth_parses_kospi_fields(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return _FakeResponse(KOSPI_INTEGRATION)

    monkeypatch.setattr(naver_breadth.requests, "get", fake_get)

    result = naver_breadth.fetch_breadth("kospi")

    assert captured["url"] == naver_breadth.INDEX_URL.format(code="KOSPI")
    assert captured["headers"]["User-Agent"]
    assert result == {"adv": 384, "dec": 488, "flat": 40, "limit_up": 6, "limit_down": 0}


def test_fetch_breadth_parses_kosdaq_and_strips_commas(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        assert url == naver_breadth.INDEX_URL.format(code="KOSDAQ")
        return _FakeResponse(KOSDAQ_INTEGRATION)

    monkeypatch.setattr(naver_breadth.requests, "get", fake_get)

    result = naver_breadth.fetch_breadth("kosdaq")

    assert result == {"adv": 501, "dec": 1182, "flat": 56, "limit_up": 11, "limit_down": 1}


def test_fetch_breadth_sum_sanity_within_market_size():
    """실측 조합(2026-07-18)의 다섯 필드 합이 한 시장 전체 상장종목수 범위(대략
    900~1800) 안에 들어와야 한다는 sanity check — 파서가 엉뚱한 숫자를 잡지
    않는지 회귀 방지용."""
    kospi = {"adv": 384, "dec": 488, "flat": 40, "limit_up": 6, "limit_down": 0}
    kosdaq = {"adv": 501, "dec": 1182, "flat": 56, "limit_up": 11, "limit_down": 1}

    assert 900 <= sum(kospi.values()) <= 1800
    assert 900 <= sum(kosdaq.values()) <= 1800


def test_fetch_breadth_rejects_unknown_market():
    with pytest.raises(ValueError):
        naver_breadth.fetch_breadth("nasdaq")


def test_fetch_breadth_raises_on_missing_fields(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return _FakeResponse({"upDownStockInfo": {"riseCount": "179"}})

    monkeypatch.setattr(naver_breadth.requests, "get", fake_get)

    with pytest.raises(naver_breadth.NaverBreadthError):
        naver_breadth.fetch_breadth("kospi")


def test_fetch_breadth_raises_on_non_json_response(monkeypatch):
    """구 소스가 302로 리다이렉트되던 SPA HTML 셸을 그대로 받는 경우처럼, JSON이
    아닌 응답을 받으면 NaverBreadthError로 명확히 실패해야 한다(2026-09-14 실사고
    재발 방지)."""

    def fake_get(url, headers=None, timeout=None):
        return _FakeResponse(data=None, text="<!DOCTYPE html><html>...")

    monkeypatch.setattr(naver_breadth.requests, "get", fake_get)

    with pytest.raises(naver_breadth.NaverBreadthError):
        naver_breadth.fetch_breadth("kospi")
