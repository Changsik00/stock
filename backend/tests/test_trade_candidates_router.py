"""Integration tests for /api/trade-candidates (PLAN.md §5.89-1). Real dev Postgres;
test rows use code prefix 'ZT' and are cleaned up (pattern: test_etf_pair_router.py)."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db import async_session_factory, engine
from app.market_hours import KST
from app.models import TradeCandidate
from app.routers import trade_candidates as mod


async def _clear() -> None:
    async with async_session_factory() as session:
        await session.execute(TradeCandidate.__table__.delete().where(TradeCandidate.code.like("ZT%")))
        await session.commit()


@pytest.fixture
async def clean():
    await _clear()
    yield
    await _clear()
    await engine.dispose()


def _client() -> AsyncClient:
    app = FastAPI()
    app.include_router(mod.router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_invalid_code(clean):
    async with _client() as c:
        for bad in ["12345", "1234567", "ZT 123"]:
            r = await c.post("/api/trade-candidates/", json={"code": bad, "name": "x"})
            assert r.status_code == 400


async def test_duplicate_409_and_delete(clean):
    async with _client() as c:
        r = await c.post("/api/trade-candidates/", json={"code": "ZT0001", "name": "테스트", "multiple": 2})
        assert r.status_code == 200 and r.json()["multiple"] == 2
        r2 = await c.post("/api/trade-candidates/", json={"code": "ZT0001", "name": "테스트"})
        assert r2.status_code == 409
        lst = (await c.get("/api/trade-candidates/")).json()
        assert any(i["code"] == "ZT0001" for i in lst["manual"])
        assert (await c.delete(f"/api/trade-candidates/{r.json()['id']}")).status_code == 200
        assert (await c.delete(f"/api/trade-candidates/{r.json()['id']}")).status_code == 404


async def test_name_lookup_failure_400(clean, monkeypatch):
    async def none(code):
        return None

    monkeypatch.setattr(mod, "_lookup_name", none)
    async with _client() as c:
        r = await c.post("/api/trade-candidates/", json={"code": "ZT0002"})
    assert r.status_code == 400


async def test_delete_recommended_400_and_today_filter(clean):
    today = dt.datetime.now(KST).date()
    async with async_session_factory() as s:
        a = TradeCandidate(code="ZT0003", name="추천", multiple=1, source="recommended", valid_date=today)
        b = TradeCandidate(
            code="ZT0004", name="옛추천", multiple=1, source="recommended", valid_date=today - dt.timedelta(days=1)
        )
        s.add_all([a, b])
        await s.commit()
        aid = a.id
    async with _client() as c:
        lst = (await c.get("/api/trade-candidates/")).json()
        codes = [i["code"] for i in lst["recommended"]]
        assert "ZT0003" in codes and "ZT0004" not in codes
        assert (await c.delete(f"/api/trade-candidates/{aid}")).status_code == 400
