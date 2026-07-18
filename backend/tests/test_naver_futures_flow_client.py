"""Unit tests for app.clients.naver_futures_flow (m.stock.naver.com/api/index/FUT/trend).

No real network involved. Fixture payloads are trimmed to the actual shape captured via
real curl calls (2026-07-19, PLAN.md §4.5 4.5-2) — see the module docstring for the
2024-05-07 "역대 최대 순매수 2조3,447억원" cross-check that pinned down the 억원 unit.
"""

from __future__ import annotations

import datetime as dt

import pytest
import requests

from app.clients import naver_futures_flow

DATE = dt.date(2026, 7, 16)


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._payload


def test_fetch_futures_flow_parses_and_converts_eokwon_to_million(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return _FakeResponse(
            {
                "bizdate": "20260716",
                "personalValue": "-3,442",
                "foreignValue": "+7,014",
                "institutionalValue": "-3,210",
            }
        )

    monkeypatch.setattr(naver_futures_flow.requests, "get", fake_get)

    result = naver_futures_flow.fetch_futures_flow(DATE)

    assert captured["url"] == naver_futures_flow.TREND_URL
    assert captured["params"] == {"bizdate": "20260716"}
    assert captured["headers"]["User-Agent"]

    assert result["date"] == DATE
    # 억원 -> 백만원 (x100): -3,442억 -> -344,200백만, +7,014억 -> 701,400백만
    assert result["flows"] == [
        {"investor": "개인", "net_value": -344_200, "net_volume": None},
        {"investor": "외국인", "net_value": 701_400, "net_volume": None},
        {"investor": "기관계", "net_value": -321_000, "net_volume": None},
    ]


def test_fetch_futures_flow_matches_2024_05_07_record_day(monkeypatch):
    """모듈 docstring의 단위 확정 근거 — 다음뉴스 보도 "2조3,447억원"과 정확히
    일치하는지 회귀 테스트로 고정."""

    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse(
            {
                "bizdate": "20240507",
                "personalValue": "-8,551",
                "foreignValue": "+23,447",
                "institutionalValue": "-14,677",
            }
        )

    monkeypatch.setattr(naver_futures_flow.requests, "get", fake_get)

    result = naver_futures_flow.fetch_futures_flow(dt.date(2024, 5, 7))

    foreign = next(f for f in result["flows"] if f["investor"] == "외국인")
    assert foreign["net_value"] == 23_447 * 100  # 2조3,447억원 -> 2,344,700백만원


def test_fetch_futures_flow_returns_none_for_all_zero_response(monkeypatch):
    """휴장일(주말/공휴일)은 세 값 모두 "0"으로 오며 예외가 아니라 None으로 취급된다."""

    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse(
            {
                "bizdate": "20260718",
                "personalValue": "0",
                "foreignValue": "0",
                "institutionalValue": "0",
            }
        )

    monkeypatch.setattr(naver_futures_flow.requests, "get", fake_get)

    result = naver_futures_flow.fetch_futures_flow(dt.date(2026, 7, 18))

    assert result is None


def test_fetch_futures_flow_raises_on_missing_fields(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse({"bizdate": "20260716"})

    monkeypatch.setattr(naver_futures_flow.requests, "get", fake_get)

    with pytest.raises(naver_futures_flow.NaverFuturesFlowError):
        naver_futures_flow.fetch_futures_flow(DATE)


def test_fetch_futures_flow_raises_http_error(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse({}, status_code=500)

    monkeypatch.setattr(naver_futures_flow.requests, "get", fake_get)

    with pytest.raises(requests.HTTPError):
        naver_futures_flow.fetch_futures_flow(DATE)
