"""Integration tests for GET /api/groups (PLAN.md §4.6/§6 3.6-3).

This router is intentionally NOT registered in main.py yet (see routers/groups.py
docstring — avoids a merge conflict with parallel work; wiring happens at
integration time). So we build a throwaway FastAPI app here and include the router
directly, per the task instructions ("TestClient/ASGITransport에 라우터를 직접
include해서 테스트").

Uses the real dev Postgres (docker-compose `db` service, must be running — same DB
the collector writes to) via app.db.async_session_factory, since there is no sqlite
test harness in this repo (aiosqlite isn't installed) and the router's logic (MAX(date)
fallback, ordering, 400 on bad `type`) is only meaningfully exercised against a real
SQL backend. Test rows use a date far outside any real backfill (2099-01-01) so they
can't collide with actual data, and are cleaned up in a fixture teardown either way.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import async_session_factory, engine
from app.models import GroupSnapshot
from app.routers.groups import router

TEST_DATE = dt.date(2099, 1, 1)
OLDER_DATE = dt.date(2098, 1, 1)


@pytest.fixture(autouse=True)
async def _dispose_engine_per_test():
    """pytest-asyncio gives each test function its own event loop by default, but
    app.db.engine is a module-level singleton created at import time — its asyncpg
    connections get bound to whichever loop first used them, so reusing it across
    tests with different loops raises "attached to a different loop". Disposing the
    pool after every test forces a fresh connection (and thus fresh loop binding) on
    the next test."""
    yield
    await engine.dispose()


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


async def _clear_test_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(
            delete(GroupSnapshot).where(GroupSnapshot.date.in_([TEST_DATE, OLDER_DATE]))
        )
        await session.commit()


@pytest.fixture
async def seeded_rows():
    await _clear_test_rows()
    async with async_session_factory() as session:
        for date, group_type, name, rate, value, market_sum in [
            (TEST_DATE, "upjong", "반도체와반도체장비", -10.07, None, None),
            (TEST_DATE, "upjong", "문구류", 8.27, None, None),
            (TEST_DATE, "upjong", "손해보험", 3.84, None, None),
            (TEST_DATE, "theme", "2차전지", -2.5, None, None),
            (OLDER_DATE, "upjong", "옛날업종", 1.0, None, None),
        ]:
            stmt = pg_insert(GroupSnapshot).values(
                date=date,
                group_type=group_type,
                name=name,
                change_rate=rate,
                value=value,
                market_sum=market_sum,
            )
            await session.execute(stmt)
        await session.commit()
    yield
    await _clear_test_rows()


async def test_groups_returns_rows_for_given_date_sorted_by_change_rate_desc(seeded_rows):
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/groups", params={"type": "upjong", "date": TEST_DATE.isoformat()})

    assert resp.status_code == 200
    body = resp.json()
    assert [row["name"] for row in body] == ["문구류", "손해보험", "반도체와반도체장비"]
    assert body[0] == {
        "name": "문구류",
        "change_rate": 8.27,
        "value": None,
        "market_sum": None,
    }


async def test_groups_defaults_to_latest_date_when_date_omitted(seeded_rows):
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/groups", params={"type": "upjong"})

    assert resp.status_code == 200
    body = resp.json()
    # TEST_DATE (2099-01-01) is more recent than OLDER_DATE -> 옛날업종 must not appear.
    names = {row["name"] for row in body}
    assert names == {"반도체와반도체장비", "문구류", "손해보험"}


async def test_groups_filters_by_type(seeded_rows):
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/groups", params={"type": "theme", "date": TEST_DATE.isoformat()})

    assert resp.status_code == 200
    body = resp.json()
    assert [row["name"] for row in body] == ["2차전지"]


async def test_groups_rejects_unknown_type():
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/groups", params={"type": "bogus"})

    assert resp.status_code == 400


async def test_groups_returns_empty_list_when_no_data_for_type():
    await _clear_test_rows()
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # No rows seeded at all right now for this fixtureless test, but other real
        # data may exist for today's date in a dev DB -> use an explicit date instead
        # so this assertion is deterministic regardless of what the collector already
        # wrote for "today".
        resp = await client.get(
            "/api/groups", params={"type": "upjong", "date": dt.date(2001, 1, 1).isoformat()}
        )

    assert resp.status_code == 200
    assert resp.json() == []
