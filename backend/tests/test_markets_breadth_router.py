"""Unit tests for the market_breadth endpoints in app.routers.markets
(GET /api/markets/{market}/breadth, GET /api/markets/breadth/live — PLAN.md §4.6 3.6-2).

Uses httpx.AsyncClient + ASGITransport against the real FastAPI app, with
get_session overridden to a fake AsyncSession (no real DB) and the blocking
naver_breadth fetch monkeypatched (no real network) — same no-DB/no-network
philosophy as the other collector/client tests in this package.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import get_session
from app.main import app
from app.models import MarketBreadth
from app.routers import markets

KOSPI_LIVE = {"adv": 384, "dec": 488, "flat": 40, "limit_up": 6, "limit_down": 0}
KOSDAQ_LIVE = {"adv": 501, "dec": 1182, "flat": 56, "limit_up": 11, "limit_down": 1}


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, stmt):
        return _FakeResult(self._rows)


@pytest.fixture(autouse=True)
def _reset_live_cache():
    """live 엔드포인트는 모듈 전역 캐시를 쓰므로 테스트 간 오염을 막기 위해 매번 리셋."""
    markets._live_cache["data"] = None
    markets._live_cache["ts"] = 0.0
    yield
    markets._live_cache["data"] = None
    markets._live_cache["ts"] = 0.0


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


async def test_market_breadth_series_returns_db_rows():
    rows = [
        MarketBreadth(
            market="kospi", date=dt.date(2026, 7, 17), adv=400, dec=470, flat=38, limit_up=5, limit_down=1
        ),
        MarketBreadth(
            market="kospi", date=dt.date(2026, 7, 18), adv=384, dec=488, flat=40, limit_up=6, limit_down=0
        ),
    ]

    async def fake_get_session():
        yield _FakeSession(rows)

    app.dependency_overrides[get_session] = fake_get_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/kospi/breadth", params={"days": 30})

    assert resp.status_code == 200
    body = resp.json()
    assert body["market"] == "kospi"
    assert body["days"] == 30
    assert body["series"] == [
        {"date": "2026-07-17", "adv": 400, "dec": 470, "flat": 38, "limit_up": 5, "limit_down": 1},
        {"date": "2026-07-18", "adv": 384, "dec": 488, "flat": 40, "limit_up": 6, "limit_down": 0},
    ]


async def test_market_breadth_series_rejects_unknown_market():
    async def fake_get_session():
        yield _FakeSession([])

    app.dependency_overrides[get_session] = fake_get_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/futures/breadth")

    assert resp.status_code == 400


async def test_market_breadth_live_returns_both_markets(monkeypatch):
    def fake_fetch(market):
        return KOSPI_LIVE if market == "kospi" else KOSDAQ_LIVE

    monkeypatch.setattr(markets, "_fetch_breadth_blocking", fake_fetch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/breadth/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["kospi"] == KOSPI_LIVE
    assert body["kosdaq"] == KOSDAQ_LIVE
    assert "cached_at" in body


async def test_market_breadth_live_caches_within_ttl(monkeypatch):
    calls = []

    def fake_fetch(market):
        calls.append(market)
        return KOSPI_LIVE if market == "kospi" else KOSDAQ_LIVE

    monkeypatch.setattr(markets, "_fetch_breadth_blocking", fake_fetch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/api/markets/breadth/live")
        r2 = await client.get("/api/markets/breadth/live")

    assert r1.json()["cached_at"] == r2.json()["cached_at"]
    # 두 번째 호출은 캐시를 썼으므로 소스 fetch가 다시 불리지 않아야 한다.
    assert calls == ["kospi", "kosdaq"]


async def test_market_breadth_live_survives_one_market_failure(monkeypatch):
    def fake_fetch(market):
        if market == "kosdaq":
            raise RuntimeError("boom")
        return KOSPI_LIVE

    monkeypatch.setattr(markets, "_fetch_breadth_blocking", fake_fetch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/breadth/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["kospi"] == KOSPI_LIVE
    assert body["kosdaq"] is None


async def test_market_breadth_live_502_when_both_markets_fail(monkeypatch):
    def fake_fetch(market):
        raise RuntimeError("boom")

    monkeypatch.setattr(markets, "_fetch_breadth_blocking", fake_fetch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/breadth/live")

    assert resp.status_code == 502
