"""Unit tests for GET /api/groups/top-stocks (app.routers.groups, PLAN.md §5.12
"업종·테마 트리맵 클릭 → 대장 종목 TOP10").

httpx.AsyncClient + ASGITransport against the real FastAPI app (groups.router is
already wired into main.py). No DB session needed and no real network — the
blocking naver_group calls are monkeypatched via groups._fetch_group_list_blocking
and groups._fetch_group_constituents_blocking (mirrors test_groups_live_router.py).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.clients import naver_group
from app.main import app
from app.routers import groups

NAME_TO_NO_ROWS = [
    {"name": "반도체와반도체장비", "change_rate": -10.07, "no": 278},
    {"name": "문구류", "change_rate": 8.27, "no": 332},
]

CONSTITUENT_ROWS = [
    {"code": "413300", "name": "티엘엔지니어링", "change_rate": 10.19, "value": 8076},
    {"code": "365590", "name": "하이딥", "change_rate": 10.56, "value": 122},
]


@pytest.fixture(autouse=True)
def _reset_caches():
    groups._group_name_to_no_cache["upjong"] = {"ts": 0.0, "data": None}
    groups._group_name_to_no_cache["theme"] = {"ts": 0.0, "data": None}
    groups._group_top_stocks_cache.clear()
    yield
    groups._group_name_to_no_cache["upjong"] = {"ts": 0.0, "data": None}
    groups._group_name_to_no_cache["theme"] = {"ts": 0.0, "data": None}
    groups._group_top_stocks_cache.clear()


async def test_top_stocks_returns_rows_for_known_group(monkeypatch):
    monkeypatch.setattr(groups, "_fetch_group_list_blocking", lambda group_type: NAME_TO_NO_ROWS)
    monkeypatch.setattr(
        groups, "_fetch_group_constituents_blocking", lambda group_type, no, limit: CONSTITUENT_ROWS
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/groups/top-stocks", params={"type": "upjong", "name": "반도체와반도체장비"}
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "upjong"
    assert body["name"] == "반도체와반도체장비"
    assert body["rows"] == CONSTITUENT_ROWS
    assert "cached_at" in body


async def test_top_stocks_rejects_unknown_type():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/groups/top-stocks", params={"type": "bogus", "name": "x"})
    assert resp.status_code == 400


async def test_top_stocks_404_for_unknown_name(monkeypatch):
    monkeypatch.setattr(groups, "_fetch_group_list_blocking", lambda group_type: NAME_TO_NO_ROWS)

    def _raise(group_type, no, limit):  # pragma: no cover - 불리면 안 됨
        raise AssertionError("constituents fetch should not run for an unknown name")

    monkeypatch.setattr(groups, "_fetch_group_constituents_blocking", _raise)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/groups/top-stocks", params={"type": "upjong", "name": "존재하지않는업종"}
        )

    assert resp.status_code == 404


async def test_top_stocks_caches_name_to_no_mapping(monkeypatch):
    calls = []

    def fake_list(group_type):
        calls.append(group_type)
        return NAME_TO_NO_ROWS

    monkeypatch.setattr(groups, "_fetch_group_list_blocking", fake_list)
    monkeypatch.setattr(
        groups, "_fetch_group_constituents_blocking", lambda group_type, no, limit: CONSTITUENT_ROWS
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get(
            "/api/groups/top-stocks", params={"type": "upjong", "name": "문구류"}
        )
        r2 = await client.get(
            "/api/groups/top-stocks", params={"type": "upjong", "name": "반도체와반도체장비"}
        )

    assert r1.status_code == 200
    assert r2.status_code == 200
    # 두 요청 모두 같은 group_type이라 이름→no 목록은 한 번만 재조회돼야 한다.
    assert calls == ["upjong"]


async def test_top_stocks_caches_constituents_within_ttl(monkeypatch):
    calls = []

    def fake_constituents(group_type, no, limit):
        calls.append((group_type, no, limit))
        return CONSTITUENT_ROWS

    monkeypatch.setattr(groups, "_fetch_group_list_blocking", lambda group_type: NAME_TO_NO_ROWS)
    monkeypatch.setattr(groups, "_fetch_group_constituents_blocking", fake_constituents)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get(
            "/api/groups/top-stocks", params={"type": "upjong", "name": "문구류"}
        )
        r2 = await client.get(
            "/api/groups/top-stocks", params={"type": "upjong", "name": "문구류"}
        )

    assert r1.json()["cached_at"] == r2.json()["cached_at"]
    assert calls == [("upjong", 332, 10)]


async def test_top_stocks_502_on_constituents_fetch_failure(monkeypatch):
    def _raise(group_type, no, limit):
        raise naver_group.NaverGroupError("boom")

    monkeypatch.setattr(groups, "_fetch_group_list_blocking", lambda group_type: NAME_TO_NO_ROWS)
    monkeypatch.setattr(groups, "_fetch_group_constituents_blocking", _raise)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/groups/top-stocks", params={"type": "upjong", "name": "문구류"}
        )

    assert resp.status_code == 502


async def test_top_stocks_respects_limit_param(monkeypatch):
    calls = []

    def fake_constituents(group_type, no, limit):
        calls.append(limit)
        return CONSTITUENT_ROWS[:limit]

    monkeypatch.setattr(groups, "_fetch_group_list_blocking", lambda group_type: NAME_TO_NO_ROWS)
    monkeypatch.setattr(groups, "_fetch_group_constituents_blocking", fake_constituents)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/groups/top-stocks",
            params={"type": "upjong", "name": "문구류", "limit": 1},
        )

    assert resp.status_code == 200
    assert calls == [1]
    assert len(resp.json()["rows"]) == 1
