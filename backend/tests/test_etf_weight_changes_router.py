"""Integration tests for GET /api/markets/etf-weight-changes (PLAN.md §5.25).

Same pattern as tests/test_flow_path_direction_and_sentiment_router.py: real dev
Postgres via app.db.async_session_factory, throwaway FastAPI app including only
routers.flow_rank.router, test rows dated far in the future (2099-05-*) so they
always win the "most recent 2 distinct dates" comparison against real data without
colliding with it, cleaned up in fixture teardown.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db import async_session_factory, engine
from app.models import EtfHolding, Stock
from app.routers.flow_rank import router

PREV_DATE = dt.date(2099, 5, 1)
CURR_DATE = dt.date(2099, 5, 2)

ETF_ACTIVE = "990101"
STOCK_X = "990110"  # 비중확대
STOCK_Y = "990111"  # 신규편입

TEST_CODES = [ETF_ACTIVE, STOCK_X, STOCK_Y]
TEST_DATES = [PREV_DATE, CURR_DATE]


@pytest.fixture(autouse=True)
async def _dispose_engine_per_test():
    yield
    await engine.dispose()


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


async def _clear_test_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(EtfHolding.__table__.delete().where(EtfHolding.date.in_(TEST_DATES)))
        await session.execute(Stock.__table__.delete().where(Stock.code.in_(TEST_CODES)))
        await session.commit()


@pytest.fixture
async def seeded_etf_weight_changes():
    await _clear_test_rows()
    async with async_session_factory() as session:
        session.add(Stock(code=ETF_ACTIVE, name="테스트라우터액티브", market="KOSDAQ", is_etf=True))
        session.add(Stock(code=STOCK_X, name="테스트라우터종목X", market="KOSDAQ", is_etf=False))
        session.add(Stock(code=STOCK_Y, name="테스트라우터종목Y", market="KOSDAQ", is_etf=False))
        session.add(EtfHolding(etf_code=ETF_ACTIVE, date=PREV_DATE, stock_code=STOCK_X, weight=5.0))
        session.add(EtfHolding(etf_code=ETF_ACTIVE, date=CURR_DATE, stock_code=STOCK_X, weight=6.0))
        session.add(EtfHolding(etf_code=ETF_ACTIVE, date=CURR_DATE, stock_code=STOCK_Y, weight=3.0))
        await session.commit()
    yield
    await _clear_test_rows()


async def test_etf_weight_changes_happy_path_shape(seeded_etf_weight_changes):
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/markets/etf-weight-changes", params={"limit": 10})

    assert resp.status_code == 200
    body = resp.json()
    assert body["prev_date"] == PREV_DATE.isoformat()
    assert body["curr_date"] == CURR_DATE.isoformat()
    codes = {c["code"] for c in body["changes"]}
    assert {STOCK_X, STOCK_Y} <= codes

    by_code = {c["code"]: c for c in body["changes"]}
    assert by_code[STOCK_X]["event"] == "비중확대"
    assert by_code[STOCK_X]["etf_name"] == "테스트라우터액티브"
    assert by_code[STOCK_X]["is_active"] is True
    assert by_code[STOCK_Y]["event"] == "신규편입"
    assert by_code[STOCK_Y]["prev_weight"] is None


async def test_etf_weight_changes_code_filter(seeded_etf_weight_changes):
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/markets/etf-weight-changes", params={"code": STOCK_X})

    assert resp.status_code == 200
    body = resp.json()
    assert [c["code"] for c in body["changes"]] == [STOCK_X]


async def test_etf_weight_changes_rejects_unknown_event():
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/markets/etf-weight-changes", params={"event": "매수"})

    assert resp.status_code == 400


async def test_etf_weight_changes_rejects_out_of_range_limit():
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/markets/etf-weight-changes", params={"limit": 0})

    assert resp.status_code == 422  # FastAPI Query(ge=1) validation error
