"""Unit tests for the two 1D 누적 조회 endpoints (PLAN.md §5.4-3, §5.10):
GET /api/markets/flow/intraday-accumulated and
GET /api/markets/foreign-position/intraday-accumulated (app.routers.markets).

Same httpx ASGITransport-against-the-real-app house style as
test_markets_flow_live_router.py/test_futures_flow_live_router.py. These two
routes are pure in-memory reads (no DB session, no external call) — the tests
seed app.collectors.intraday_snapshot's module-level buffers directly (the
same object the router imports and calls) and assert the response mirrors
whatever get_flow_series()/get_foreign_position_series() would return, without
needing to go through the 60초/7분 live_refresh jobs.

**PLAN.md §5.10 (2026-07-22)**: `_buffers` now nests kospi/kosdaq under their
own dicts (see test_intraday_snapshot.py for the full unit-level coverage of
that split). This file focuses on proving the two HTTP routes surface the new
nested shape (flow) and the still-flat, still-summed shape (foreign-position,
regression coverage — that modal is out of scope for §5.10).
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from app.collectors import intraday_snapshot as snap
from app.main import app


@pytest.fixture(autouse=True)
def _reset_buffers():
    def _clear_all():
        for value in snap._buffers.values():
            if isinstance(value, list):
                value.clear()
            else:
                for series in value.values():
                    series.clear()

    _clear_all()
    snap._buffer_date = None
    yield
    _clear_all()
    snap._buffer_date = None


async def test_flow_intraday_accumulated_returns_current_buffer_split_by_market(monkeypatch):
    monkeypatch.setattr(snap, "_today_kst", lambda: dt.date(2026, 7, 21))
    monkeypatch.setattr(snap, "is_market_closed", lambda now_kst: False)
    snap._buffers["kospi"]["개인"].append({"time": "10:00", "value": 100})
    snap._buffers["kospi"]["외국인"].append({"time": "10:00", "value": 100})
    snap._buffers["kospi"]["기관계"].append({"time": "10:00", "value": -50})
    snap._buffers["kosdaq"]["개인"].append({"time": "10:00", "value": 20})
    snap._buffers["kosdaq"]["외국인"].append({"time": "10:00", "value": 10})
    snap._buffers["kosdaq"]["기관계"].append({"time": "10:00", "value": -5})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/flow/intraday-accumulated")

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-07-21"
    assert body["series"]["kospi"]["개인"] == [{"time": "10:00", "value": 100}]
    assert body["series"]["kospi"]["외국인"] == [{"time": "10:00", "value": 100}]
    assert body["series"]["kospi"]["기관계"] == [{"time": "10:00", "value": -50}]
    assert body["series"]["kosdaq"]["개인"] == [{"time": "10:00", "value": 20}]
    assert body["series"]["kosdaq"]["외국인"] == [{"time": "10:00", "value": 10}]
    assert body["series"]["kosdaq"]["기관계"] == [{"time": "10:00", "value": -5}]
    assert body["market_closed"] is False


async def test_flow_intraday_accumulated_empty_buffer_returns_empty_lists():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/flow/intraday-accumulated")

    assert resp.status_code == 200
    body = resp.json()
    assert body["series"] == {
        "kospi": {"개인": [], "외국인": [], "기관계": []},
        "kosdaq": {"개인": [], "외국인": [], "기관계": []},
    }
    assert "date" in body
    assert "market_closed" in body


async def test_foreign_position_intraday_accumulated_returns_summed_spot_and_futures(monkeypatch):
    # 회귀 테스트(§5.10): kospi/kosdaq이 분리된 뒤에도 "외인 양손" 모달의 spot은
    # 여전히 두 시장 합산값이어야 한다.
    monkeypatch.setattr(snap, "_today_kst", lambda: dt.date(2026, 7, 21))
    monkeypatch.setattr(snap, "is_market_closed", lambda now_kst: False)
    snap._buffers["kospi"]["외국인"].append({"time": "10:00", "value": 100})
    snap._buffers["kosdaq"]["외국인"].append({"time": "10:00", "value": 10})
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


async def test_breadth_intraday_accumulated_returns_current_buffer(monkeypatch):
    # PLAN.md §5.13 — 등락 종목수 1D 상승비율 추이.
    monkeypatch.setattr(snap, "_today_kst", lambda: dt.date(2026, 7, 21))
    monkeypatch.setattr(snap, "is_market_closed", lambda now_kst: False)
    snap._buffers["등락비율"].append({"time": "10:00", "value": 50.0})
    snap._buffers["등락비율"].append({"time": "10:01", "value": 62.5})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/breadth/intraday-accumulated")

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-07-21"
    assert body["series"] == [
        {"time": "10:00", "value": 50.0},
        {"time": "10:01", "value": 62.5},
    ]
    assert body["market_closed"] is False


async def test_breadth_intraday_accumulated_empty_buffer_returns_empty_list():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/breadth/intraday-accumulated")

    assert resp.status_code == 200
    body = resp.json()
    assert body["series"] == []
    assert "date" in body
    assert "market_closed" in body
