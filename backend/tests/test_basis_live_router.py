"""Unit tests for GET /api/markets/basis/live (app.routers.basis, PLAN.md §4.7
3단 갱신 주기 — 2026-07-20 장중 실측으로 5~10분 티어 편입).

httpx.AsyncClient + ASGITransport against the real FastAPI app (basis.router is
already wired into main.py). No DB session needed (_warm_basis_live doesn't take
one — no DB fallback, only last-successful-cache reuse) and no real network
(the blocking naver_index call is monkeypatched via basis._fetch_index_series_blocking)
— same no-DB/no-network philosophy as test_markets_breadth_router.py.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.routers import basis

FUT_ROWS = [
    {"date": dt.date(2026, 7, 20), "open": 1040.0, "high": 1051.0, "low": 1033.0, "close": 1049.85, "volume": 9866}
]
SPOT_ROWS = [
    {"date": dt.date(2026, 7, 20), "open": 1051.0, "high": 1083.0, "low": 1027.0, "close": 1075.96, "volume": 21579}
]


@pytest.fixture(autouse=True)
def _reset_cache():
    basis._basis_live_cache["data"] = None
    basis._basis_live_cache["ts"] = 0.0
    yield
    basis._basis_live_cache["data"] = None
    basis._basis_live_cache["ts"] = 0.0


# 이 파일의 happy-path 테스트는 실제 wall-clock과 무관하게 "장중"을 가정한다 —
# 장 마감 케이스는 아래 별도 절이 다룬다(2026-07-20 신규 5~10분 티어 게이트 원칙).
@pytest.fixture(autouse=True)
def _force_market_open(monkeypatch):
    monkeypatch.setattr(basis, "is_market_closed", lambda now_kst: False)


async def test_basis_live_computes_from_today_bar(monkeypatch):
    def fake_fetch(market, start, end):
        return FUT_ROWS if market == basis.FUTURES_MARKET else SPOT_ROWS

    monkeypatch.setattr(basis, "_fetch_index_series_blocking", fake_fetch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/basis/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-07-20"
    assert body["futures_close"] == 1049.85
    assert body["kospi200_close"] == 1075.96
    assert body["basis"] == round(1049.85 - 1075.96, 2)
    assert body["backwardation"] is True
    assert body["market_closed"] is False
    assert "expiry" in body
    assert "cached_at" in body


async def test_basis_live_caches_within_ttl(monkeypatch):
    calls = []

    def fake_fetch(market, start, end):
        calls.append(market)
        return FUT_ROWS if market == basis.FUTURES_MARKET else SPOT_ROWS

    monkeypatch.setattr(basis, "_fetch_index_series_blocking", fake_fetch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/api/markets/basis/live")
        r2 = await client.get("/api/markets/basis/live")

    assert r1.json()["cached_at"] == r2.json()["cached_at"]
    assert calls == [basis.FUTURES_MARKET, basis.SPOT_MARKET]


async def test_basis_live_502_when_both_fail(monkeypatch):
    def fake_fetch(market, start, end):
        raise RuntimeError("boom")

    monkeypatch.setattr(basis, "_fetch_index_series_blocking", fake_fetch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/basis/live")

    assert resp.status_code == 502


async def test_basis_live_market_closed_skips_fetch_no_cache(monkeypatch):
    monkeypatch.setattr(basis, "is_market_closed", lambda now_kst: True)

    def _raise(market, start, end):  # pragma: no cover - 불리면 안 됨
        raise AssertionError("naver_index should not be called when market is closed")

    monkeypatch.setattr(basis, "_fetch_index_series_blocking", _raise)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/basis/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["market_closed"] is True
    assert body["basis"] is None


async def test_basis_live_market_closed_reuses_last_cache(monkeypatch):
    def fake_fetch(market, start, end):
        return FUT_ROWS if market == basis.FUTURES_MARKET else SPOT_ROWS

    monkeypatch.setattr(basis, "_fetch_index_series_blocking", fake_fetch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/api/markets/basis/live")
    assert r1.json()["market_closed"] is False

    basis._basis_live_cache["ts"] = 0.0  # TTL 만료 시뮬레이션

    def _raise(market, start, end):  # pragma: no cover - 불리면 안 됨
        raise AssertionError("naver_index should not be called when market is closed")

    monkeypatch.setattr(basis, "_fetch_index_series_blocking", _raise)
    monkeypatch.setattr(basis, "is_market_closed", lambda now_kst: True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r2 = await client.get("/api/markets/basis/live")

    assert r2.status_code == 200
    body = r2.json()
    assert body["market_closed"] is True
    assert body["basis"] == r1.json()["basis"]
