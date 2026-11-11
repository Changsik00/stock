"""Unit tests for GET /api/groups/live (app.routers.groups, PLAN.md §4.7 3단
갱신 주기 — 2026-07-20 장중 실측으로 change_rate만 5~10분 티어 편입, 거래대금
합산은 EOD 전용으로 유지).

httpx.AsyncClient + ASGITransport against the real FastAPI app (groups.router is
already wired into main.py). No DB session needed and no real network — the
blocking naver_group list call is monkeypatched via groups._fetch_group_list_blocking.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.market_hours import KST
from app.routers import groups

ROWS = [{"name": "반도체와반도체장비", "change_rate": 0.68, "no": 278}]


@pytest.fixture(autouse=True)
def _reset_cache():
    groups._groups_live_cache["upjong"] = {"ts": 0.0, "data": None, "fetched_at": None}
    groups._groups_live_cache["theme"] = {"ts": 0.0, "data": None, "fetched_at": None}
    yield
    groups._groups_live_cache["upjong"] = {"ts": 0.0, "data": None, "fetched_at": None}
    groups._groups_live_cache["theme"] = {"ts": 0.0, "data": None, "fetched_at": None}


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


# ---- 장 마감 분기(2026-11-11 이슈 #3) ----

NEW_ROWS = [{"name": "반도체와반도체장비", "change_rate": 9.12, "no": 278}]
OLD_PAYLOAD = {
    "type": "upjong",
    "rows": [{"name": "반도체와반도체장비", "change_rate": 8.40, "value": None, "market_sum": None}],
    "market_closed": False,
    "cached_at": "x",
}


def _at(day, hour, minute):
    return dt.datetime(2026, 11, day, hour, minute, tzinfo=KST)


def _go_closed(monkeypatch, now_kst):
    monkeypatch.setattr(groups, "is_market_closed", lambda n: True)
    monkeypatch.setattr(groups, "kst_now", lambda: now_kst)


def _counting_fetch(monkeypatch, rows):
    calls = []

    def fake(group_type):
        calls.append(group_type)
        return rows

    monkeypatch.setattr(groups, "_fetch_group_list_blocking", fake)
    return calls


async def _get(group_type="upjong"):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get("/api/groups/live", params={"type": group_type})


async def test_closed_no_cache_fetches_from_source(monkeypatch):
    _go_closed(monkeypatch, _at(11, 15, 47))
    calls = _counting_fetch(monkeypatch, NEW_ROWS)
    body = (await _get()).json()
    assert calls == ["upjong"]
    assert body["market_closed"] is True
    assert body["rows"][0]["change_rate"] == 9.12


async def test_closed_cache_fetched_before_1550_is_refetched(monkeypatch):
    _go_closed(monkeypatch, _at(11, 15, 47))
    groups._groups_live_cache["upjong"] = {"ts": 0.0, "data": OLD_PAYLOAD, "fetched_at": _at(11, 15, 28)}
    calls = _counting_fetch(monkeypatch, NEW_ROWS)
    body = (await _get()).json()
    assert len(calls) == 1
    assert body["rows"][0]["change_rate"] == 9.12
    assert body["market_closed"] is True


async def test_closed_cache_fetched_after_1550_skips_source(monkeypatch):
    _go_closed(monkeypatch, _at(11, 19, 0))
    groups._groups_live_cache["upjong"] = {"ts": 0.0, "data": OLD_PAYLOAD, "fetched_at": _at(11, 18, 10)}
    calls = _counting_fetch(monkeypatch, NEW_ROWS)
    body = (await _get()).json()
    assert calls == []
    assert body["market_closed"] is True
    assert body["rows"][0]["change_rate"] == 8.40


async def test_next_morning_with_previous_evening_cache_skips_source(monkeypatch):
    _go_closed(monkeypatch, _at(12, 8, 0))
    groups._groups_live_cache["theme"] = {"ts": 0.0, "data": OLD_PAYLOAD, "fetched_at": _at(11, 18, 10)}
    calls = _counting_fetch(monkeypatch, NEW_ROWS)
    body = (await _get("theme")).json()
    assert calls == []
    assert body["market_closed"] is True


async def test_closed_refetch_failure_falls_back_to_cache_and_retries(monkeypatch):
    _go_closed(monkeypatch, _at(11, 15, 47))
    old_fetched = _at(11, 15, 28)
    groups._groups_live_cache["upjong"] = {"ts": 0.0, "data": OLD_PAYLOAD, "fetched_at": old_fetched}

    def _raise(group_type):
        raise RuntimeError("boom")

    monkeypatch.setattr(groups, "_fetch_group_list_blocking", _raise)
    resp = await _get()
    assert resp.status_code == 200
    body = resp.json()
    assert body["market_closed"] is True
    assert body["rows"][0]["change_rate"] == 8.40
    assert groups._groups_live_cache["upjong"]["fetched_at"] == old_fetched

    groups._groups_live_cache["upjong"]["ts"] = 0.0
    calls = _counting_fetch(monkeypatch, NEW_ROWS)
    body = (await _get()).json()
    assert len(calls) == 1
    assert body["rows"][0]["change_rate"] == 9.12


async def test_closed_refetch_failure_no_cache_returns_empty(monkeypatch):
    _go_closed(monkeypatch, _at(11, 15, 47))

    def _raise(group_type):
        raise RuntimeError("boom")

    monkeypatch.setattr(groups, "_fetch_group_list_blocking", _raise)
    resp = await _get("theme")
    assert resp.status_code == 200
    body = resp.json()
    assert body["rows"] == []
    assert body["market_closed"] is True
    assert groups._groups_live_cache["theme"]["fetched_at"] is None
