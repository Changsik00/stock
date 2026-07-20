"""Unit tests for GET /api/groups/live (app.routers.groups, PLAN.md §4.7 3단
갱신 주기 — 2026-07-20 장중 실측으로 change_rate만 5~10분 티어 편입, 거래대금
합산은 EOD 전용으로 유지).

httpx.AsyncClient + ASGITransport against the real FastAPI app (groups.router is
already wired into main.py). No DB session needed and no real network — the
blocking naver_group list call is monkeypatched via groups._fetch_group_list_blocking.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.routers import groups

ROWS = [{"name": "반도체와반도체장비", "change_rate": 0.68, "no": 278}]


@pytest.fixture(autouse=True)
def _reset_cache():
    groups._groups_live_cache["upjong"] = {"ts": 0.0, "data": None}
    groups._groups_live_cache["theme"] = {"ts": 0.0, "data": None}
    yield
    groups._groups_live_cache["upjong"] = {"ts": 0.0, "data": None}
    groups._groups_live_cache["theme"] = {"ts": 0.0, "data": None}


@pytest.fixture(autouse=True)
def _force_market_open(monkeypatch):
    monkeypatch.setattr(groups, "is_market_closed", lambda now_kst: False)


async def test_groups_live_returns_change_rate_only(monkeypatch):
    monkeypatch.setattr(groups, "_fetch_group_list_blocking", lambda group_type: ROWS)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/groups/live", params={"type": "upjong"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "upjong"
    assert body["market_closed"] is False
    assert body["rows"] == [{"name": "반도체와반도체장비", "change_rate": 0.68, "value": None, "market_sum": None}]
    assert "cached_at" in body


async def test_groups_live_rejects_unknown_type():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/groups/live", params={"type": "bogus"})
    assert resp.status_code == 400


async def test_groups_live_caches_within_ttl(monkeypatch):
    calls = []

    def fake_fetch(group_type):
        calls.append(group_type)
        return ROWS

    monkeypatch.setattr(groups, "_fetch_group_list_blocking", fake_fetch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/api/groups/live", params={"type": "upjong"})
        r2 = await client.get("/api/groups/live", params={"type": "upjong"})

    assert r1.json()["cached_at"] == r2.json()["cached_at"]
    assert calls == ["upjong"]


async def test_groups_live_independent_cache_per_type(monkeypatch):
    calls = []

    def fake_fetch(group_type):
        calls.append(group_type)
        return ROWS

    monkeypatch.setattr(groups, "_fetch_group_list_blocking", fake_fetch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/api/groups/live", params={"type": "upjong"})
        await client.get("/api/groups/live", params={"type": "theme"})

    assert calls == ["upjong", "theme"]


async def test_groups_live_502_on_fetch_failure(monkeypatch):
    def fake_fetch(group_type):
        raise RuntimeError("boom")

    monkeypatch.setattr(groups, "_fetch_group_list_blocking", fake_fetch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/groups/live", params={"type": "upjong"})

    assert resp.status_code == 502


async def test_groups_live_market_closed_skips_fetch_no_cache(monkeypatch):
    monkeypatch.setattr(groups, "is_market_closed", lambda now_kst: True)

    def _raise(group_type):  # pragma: no cover - 불리면 안 됨
        raise AssertionError("naver_group should not be called when market is closed")

    monkeypatch.setattr(groups, "_fetch_group_list_blocking", _raise)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/groups/live", params={"type": "theme"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["market_closed"] is True
    assert body["rows"] == []


async def test_groups_live_market_closed_reuses_last_cache(monkeypatch):
    monkeypatch.setattr(groups, "_fetch_group_list_blocking", lambda group_type: ROWS)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/api/groups/live", params={"type": "upjong"})
    assert r1.json()["market_closed"] is False

    groups._groups_live_cache["upjong"]["ts"] = 0.0

    def _raise(group_type):  # pragma: no cover - 불리면 안 됨
        raise AssertionError("naver_group should not be called when market is closed")

    monkeypatch.setattr(groups, "_fetch_group_list_blocking", _raise)
    monkeypatch.setattr(groups, "is_market_closed", lambda now_kst: True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r2 = await client.get("/api/groups/live", params={"type": "upjong"})

    assert r2.status_code == 200
    body = r2.json()
    assert body["market_closed"] is True
    assert body["rows"] == r1.json()["rows"]
