"""Unit tests for GET /api/markets/value-rank/live (app.routers.flow_rank,
PLAN.md §4.7 3단 갱신 주기 — 2026-07-20 장중 실측으로 5~10분 티어 편입).

httpx.AsyncClient + ASGITransport against the real FastAPI app (flow_rank.router
is already wired into main.py). No DB session needed and no real network — the
blocking naver_value_rank/naver_rank calls are monkeypatched via
flow_rank._fetch_value_rank_market_blocking / _fetch_etf_codes_blocking.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.routers import flow_rank

KOSPI_RESULT = {
    "date": dt.date(2026, 7, 20),
    "rows": [
        {
            "code": "000660",
            "name": "SK하이닉스",
            "value_million": 2_825_522,
            "market_value_million": 500_000_000,
            "change_rate": 1.09,
            "stock_end_type": "stock",
        }
    ],
}
KOSDAQ_RESULT = {
    "date": dt.date(2026, 7, 20),
    "rows": [
        {
            "code": "247540",
            "name": "에코프로비엠",
            "value_million": 500_000,
            "market_value_million": 10_000_000,
            "change_rate": -0.5,
            "stock_end_type": "stock",
        }
    ],
}


@pytest.fixture(autouse=True)
def _reset_cache():
    flow_rank._value_rank_live_cache["data"] = None
    flow_rank._value_rank_live_cache["ts"] = 0.0
    yield
    flow_rank._value_rank_live_cache["data"] = None
    flow_rank._value_rank_live_cache["ts"] = 0.0


@pytest.fixture(autouse=True)
def _force_market_open(monkeypatch):
    monkeypatch.setattr(flow_rank, "is_market_closed", lambda now_kst: False)
    monkeypatch.setattr(flow_rank, "_fetch_etf_codes_blocking", lambda: set())


async def test_value_rank_live_merges_markets_and_sorts_by_value(monkeypatch):
    def fake_fetch(market):
        return KOSPI_RESULT if market == "kospi" else KOSDAQ_RESULT

    monkeypatch.setattr(flow_rank, "_fetch_value_rank_market_blocking", fake_fetch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/value-rank/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-07-20"
    assert body["market_closed"] is False
    assert [r["code"] for r in body["rows"]] == ["000660", "247540"]
    assert body["rows"][0]["rank"] == 1
    assert body["rows"][0]["turnover"] == round(2_825_522 / 500_000_000 * 100, 4)
    assert "cached_at" in body


async def test_value_rank_live_caches_within_ttl(monkeypatch):
    calls = []

    def fake_fetch(market):
        calls.append(market)
        return KOSPI_RESULT if market == "kospi" else KOSDAQ_RESULT

    monkeypatch.setattr(flow_rank, "_fetch_value_rank_market_blocking", fake_fetch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/api/markets/value-rank/live")
        r2 = await client.get("/api/markets/value-rank/live")

    assert r1.json()["cached_at"] == r2.json()["cached_at"]
    assert calls == ["kospi", "kosdaq"]


async def test_value_rank_live_502_when_both_markets_fail(monkeypatch):
    def fake_fetch(market):
        raise RuntimeError("boom")

    monkeypatch.setattr(flow_rank, "_fetch_value_rank_market_blocking", fake_fetch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/value-rank/live")

    assert resp.status_code == 502


async def test_value_rank_live_market_closed_skips_fetch_no_cache(monkeypatch):
    monkeypatch.setattr(flow_rank, "is_market_closed", lambda now_kst: True)

    def _raise(market):  # pragma: no cover - 불리면 안 됨
        raise AssertionError("naver_value_rank should not be called when market is closed")

    monkeypatch.setattr(flow_rank, "_fetch_value_rank_market_blocking", _raise)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/value-rank/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["market_closed"] is True
    assert body["rows"] == []


async def test_value_rank_live_flow_rank_live_route_removed():
    """모듈 docstring "flow-rank/live는 만들지 않는다" — 실측 근거로 라이브
    엔드포인트를 아예 추가하지 않았으므로 404가 정상이다(EOD `/flow-rank`만 유지)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/flow-rank/live")

    assert resp.status_code == 404
