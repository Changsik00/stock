"""Unit tests for app.clients.naver_group (m.stock.naver.com industry/theme JSON API,
PLAN.md §4.6/§6 3.6-3).

No real network involved. Payload shapes are trimmed from actual responses captured
2026-10-28 (see clients/naver_group.py module docstring: pageSize cap 100, 거래대금
백만원 단위, 존재하지 않는 테마 no는 200 + 빈 stocks).
"""

from __future__ import annotations

import pytest
import requests

from app.clients import naver_group


class _FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _stock(code, name, rate, value):
    return {
        "itemCode": code,
        "stockName": name,
        "fluctuationsRatio": rate,
        "accumulatedTradingValue": value,
    }


def _patch_get(monkeypatch, handler):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params))
        return handler(url, params)

    monkeypatch.setattr(naver_group.requests, "get", fake_get)
    return calls


GROUPS = {
    "groups": [
        {"no": 332, "name": "문구류", "changeRate": "8.27"},
        {"no": 278, "name": "반도체와반도체장비", "changeRate": "-10.07"},
    ],
    "totalCount": 2,
}


def test_fetch_group_snapshot_parses_name_and_signed_rate(monkeypatch):
    calls = _patch_get(monkeypatch, lambda u, p: _FakeResponse(GROUPS))

    rows = naver_group.fetch_group_snapshot("upjong")

    assert rows == [
        {"name": "문구류", "change_rate": 8.27, "no": 332},
        {"name": "반도체와반도체장비", "change_rate": -10.07, "no": 278},
    ]
    assert calls[0][0] == "https://m.stock.naver.com/api/stocks/industry"
    assert calls[0][1]["pageSize"] == 100


def test_fetch_group_snapshot_uses_theme_path(monkeypatch):
    calls = _patch_get(monkeypatch, lambda u, p: _FakeResponse(GROUPS))

    naver_group.fetch_group_snapshot("theme")

    assert calls[0][0] == "https://m.stock.naver.com/api/stocks/theme"


def test_fetch_group_snapshot_paginates_until_total_count(monkeypatch):
    def handler(url, params):
        page = params["page"]
        start = (page - 1) * 100
        n = min(100, 250 - start)
        groups = [
            {"no": start + i, "name": f"g{start + i}", "changeRate": "1.0"} for i in range(n)
        ]
        return _FakeResponse({"groups": groups, "totalCount": 250})

    calls = _patch_get(monkeypatch, handler)

    rows = naver_group.fetch_group_snapshot("theme")

    assert len(rows) == 250
    assert [c[1]["page"] for c in calls] == [1, 2, 3]


def test_fetch_group_snapshot_rejects_unknown_group_type():
    with pytest.raises(ValueError):
        naver_group.fetch_group_snapshot("bogus")


def test_fetch_group_snapshot_raises_on_no_rows(monkeypatch):
    _patch_get(monkeypatch, lambda u, p: _FakeResponse({"groups": [], "totalCount": 0}))

    with pytest.raises(naver_group.NaverGroupError, match="no rows parsed"):
        naver_group.fetch_group_snapshot("upjong")


def test_fetch_group_snapshot_raises_on_non_json(monkeypatch):
    _patch_get(monkeypatch, lambda u, p: _FakeResponse(None, text="<!DOCTYPE html>"))

    with pytest.raises(naver_group.NaverGroupError, match="non-JSON"):
        naver_group.fetch_group_snapshot("upjong")


STOCKS = {
    "stocks": [
        _stock("000001", "하이딥", "1.5", "122"),
        _stock("000002", "대형주", "-2.25", "8,076"),
        _stock("000003", "거래정지", "0.00", "N/A"),
    ],
    "totalCount": 3,
}


def test_fetch_group_value_sums_trade_value_and_skips_unparsable(monkeypatch):
    calls = _patch_get(monkeypatch, lambda u, p: _FakeResponse(STOCKS))

    assert naver_group.fetch_group_value("upjong", 278) == 122 + 8076
    assert calls[0][0] == "https://m.stock.naver.com/api/stocks/industry/278"


def test_fetch_group_value_raises_on_no_constituents(monkeypatch):
    _patch_get(monkeypatch, lambda u, p: _FakeResponse({"stocks": [], "totalCount": 0}))

    with pytest.raises(naver_group.NaverGroupError, match="no constituent rows"):
        naver_group.fetch_group_value("theme", 999)


def test_fetch_group_value_rejects_unknown_group_type():
    with pytest.raises(ValueError):
        naver_group.fetch_group_value("bogus", 1)


def test_fetch_group_constituents_sorted_desc_and_skips_bad_rows(monkeypatch):
    _patch_get(monkeypatch, lambda u, p: _FakeResponse(STOCKS))

    rows = naver_group.fetch_group_constituents("theme", 55)

    assert rows == [
        {"code": "000002", "name": "대형주", "change_rate": -2.25, "value": 8076},
        {"code": "000001", "name": "하이딥", "change_rate": 1.5, "value": 122},
    ]


def test_fetch_group_constituents_respects_limit(monkeypatch):
    _patch_get(monkeypatch, lambda u, p: _FakeResponse(STOCKS))

    rows = naver_group.fetch_group_constituents("upjong", 278, limit=1)

    assert [r["code"] for r in rows] == ["000002"]


def test_fetch_group_constituents_paginates(monkeypatch):
    def handler(url, params):
        page = params["page"]
        n = 100 if page == 1 else 20
        stocks = [_stock(f"{page}{i:05d}", f"s{page}-{i}", "1.0", str(i + 1)) for i in range(n)]
        return _FakeResponse({"stocks": stocks, "totalCount": 120})

    calls = _patch_get(monkeypatch, handler)

    rows = naver_group.fetch_group_constituents("upjong", 1, limit=500)

    assert len(rows) == 120
    assert len(calls) == 2


def test_fetch_group_constituents_raises_when_nothing_parsable(monkeypatch):
    payload = {"stocks": [_stock("000003", "거래정지", "0.00", "N/A")], "totalCount": 1}
    _patch_get(monkeypatch, lambda u, p: _FakeResponse(payload))

    with pytest.raises(naver_group.NaverGroupError, match="no constituent details"):
        naver_group.fetch_group_constituents("theme", 55)


def test_fetch_group_constituents_rejects_unknown_group_type():
    with pytest.raises(ValueError):
        naver_group.fetch_group_constituents("bogus", 1)
