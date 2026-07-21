"""Unit tests for the two 1D 누적 조회 endpoints (PLAN.md §5.4-3):
GET /api/markets/flow/intraday-accumulated and
GET /api/markets/foreign-position/intraday-accumulated (app.routers.markets).

Same httpx ASGITransport-against-the-real-app house style as
test_markets_flow_live_router.py/test_futures_flow_live_router.py. These two
routes are pure in-memory reads (no DB session, no external call) — the tests
seed app.collectors.intraday_snapshot's module-level buffers directly (the
same object the router imports and calls) and assert the response mirrors
whatever get_flow_series()/get_foreign_position_series() would return, without
needing to go through the 60초/7분 live_refresh jobs.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from app.collectors import intraday_snapshot as snap
from app.main import app


@pytest.fixture(autouse=True)
def _reset_buffers():
    for series in snap._buffers.values():
        series.clear()
    snap._buffer_date = None
    yield
    for series in snap._buffers.values():
        series.clear()
    snap._buffer_date = None


async def test_flow_intraday_accumulated_returns_current_buffer(monkeypatch):
    monkeypatch.setattr(snap, "_today_kst", lambda: dt.date(2026, 7, 21))
    monkeypatch.setattr(snap, "is_market_closed", lambda now_kst: False)
    snap._buffers["개인"].append({"time": "10:00", "value": 120})
    snap._buffers["외국인"].append({"time": "10:00", "value": 110})
    snap._buffers["기관계"].append({"time": "10:00", "value": -55})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/flow/intraday-accumulated")

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-07-21"
    assert body["series"]["개인"] == [{"time": "10:00", "value": 120}]
    assert body["series"]["외국인"] == [{"time": "10:00", "value": 110}]
    assert body["series"]["기관계"] == [{"time": "10:00", "value": -55}]
    assert body["market_closed"] is False


async def test_flow_intraday_accumulated_empty_buffer_returns_empty_lists():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/flow/intraday-accumulated")

    assert resp.status_code == 200
    body = resp.json()
    assert body["series"] == {"개인": [], "외국인": [], "기관계": []}
    assert "date" in body
    assert "market_closed" in body


async def test_foreign_position_intraday_accumulated_returns_spot_and_futures(monkeypatch):
    monkeypatch.setattr(snap, "_today_kst", lambda: dt.date(2026, 7, 21))
    monkeypatch.setattr(snap, "is_market_closed", lambda now_kst: False)
    snap._buffers["외국인"].append({"time": "10:00", "value": 110})
    snap._buffers["외인선물"].append({"time": "10:07", "value": 456})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/foreign-position/intraday-accumulated")

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-07-21"
    assert body["spot"] == [{"time": "10:00", "value": 110}]
    assert body["futures"] == [{"time": "10:07", "value": 456}]
    assert body["market_closed"] is False


async def test_foreign_position_intraday_accumulated_empty_buffer_returns_empty_lists():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/foreign-position/intraday-accumulated")

    assert resp.status_code == 200
    body = resp.json()
    assert body["spot"] == []
    assert body["futures"] == []
