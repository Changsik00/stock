"""Unit tests for GET /api/markets/futures-flow/live (app.routers.markets,
PLAN.md §4.7 3단 갱신 주기 — 2026-07-20 장중 실측으로 5~10분 티어 편입).

httpx.AsyncClient + ASGITransport against the real FastAPI app. No DB session
needed and no real network — the blocking naver_futures_flow call is
monkeypatched via markets._fetch_futures_flow_blocking.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.routers import markets

RESULT = {
    "date": dt.date(2026, 7, 20),
    "flows": [
        {"investor": "개인", "net_value": -92100, "net_volume": None},
        {"investor": "외국인", "net_value": 53300, "net_volume": None},
        {"investor": "기관계", "net_value": 159200, "net_volume": None},
    ],
}


@pytest.fixture(autouse=True)
def _reset_cache():
    markets._futures_flow_live_cache["data"] = None
    markets._futures_flow_live_cache["ts"] = 0.0
    yield
    markets._futures_flow_live_cache["data"] = None
    markets._futures_flow_live_cache["ts"] = 0.0


@pytest.fixture(autouse=True)
def _force_market_open(monkeypatch):
    monkeypatch.setattr(markets, "_market_closed_kst", lambda now_kst: False)


async def test_futures_flow_live_returns_investors(monkeypatch):
    monkeypatch.setattr(markets, "_fetch_futures_flow_blocking", lambda target_date: RESULT)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/futures-flow/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-07-20"
    assert body["investors"]["외국인"]["net_value"] == 53300
    assert body["market_closed"] is False
    assert "cached_at" in body


async def test_futures_flow_live_handles_none_result_as_empty_investors(monkeypatch):
    monkeypatch.setattr(markets, "_fetch_futures_flow_blocking", lambda target_date: None)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/futures-flow/live")

    assert resp.status_code == 200
    assert resp.json()["investors"] == {}


async def test_futures_flow_live_502_on_error(monkeypatch):
    def _raise(target_date):
        raise RuntimeError("boom")

    monkeypatch.setattr(markets, "_fetch_futures_flow_blocking", _raise)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/futures-flow/live")

    assert resp.status_code == 502


async def test_futures_flow_live_caches_within_ttl(monkeypatch):
    calls = []

    def fake_fetch(target_date):
        calls.append(target_date)
        return RESULT

    monkeypatch.setattr(markets, "_fetch_futures_flow_blocking", fake_fetch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/api/markets/futures-flow/live")
        r2 = await client.get("/api/markets/futures-flow/live")

    assert r1.json()["cached_at"] == r2.json()["cached_at"]
    assert len(calls) == 1


async def test_futures_flow_live_market_closed_skips_fetch_no_cache(monkeypatch):
    monkeypatch.setattr(markets, "_market_closed_kst", lambda now_kst: True)

    def _raise(target_date):  # pragma: no cover - 불리면 안 됨
        raise AssertionError("naver_futures_flow should not be called when market is closed")

    monkeypatch.setattr(markets, "_fetch_futures_flow_blocking", _raise)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/futures-flow/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["market_closed"] is True
    assert body["investors"] == {}


async def test_futures_flow_live_market_closed_reuses_last_cache(monkeypatch):
    monkeypatch.setattr(markets, "_fetch_futures_flow_blocking", lambda target_date: RESULT)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/api/markets/futures-flow/live")
    assert r1.json()["market_closed"] is False

    markets._futures_flow_live_cache["ts"] = 0.0

    def _raise(target_date):  # pragma: no cover - 불리면 안 됨
        raise AssertionError("naver_futures_flow should not be called when market is closed")

    monkeypatch.setattr(markets, "_fetch_futures_flow_blocking", _raise)
    monkeypatch.setattr(markets, "_market_closed_kst", lambda now_kst: True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r2 = await client.get("/api/markets/futures-flow/live")

    assert r2.status_code == 200
    body = r2.json()
    assert body["market_closed"] is True
    assert body["investors"]["외국인"]["net_value"] == 53300
