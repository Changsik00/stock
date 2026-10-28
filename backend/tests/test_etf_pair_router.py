"""Integration tests for GET /api/etf-pair/samples (PLAN.md §5.88-1).

Real dev Postgres, test rows dated 2099-01-05/06 cleaned up in teardown (same
pattern as tests/test_etf_router.py)."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db import async_session_factory, engine
from app.models import EtfPairSample
from app.routers.etf_pair import router

KST = dt.timezone(dt.timedelta(hours=9))
DAY = dt.date(2099, 1, 5)
RANGE_START = dt.datetime(2099, 1, 5, tzinfo=KST)
RANGE_END = dt.datetime(2099, 1, 8, tzinfo=KST)


async def _clear() -> None:
    async with async_session_factory() as session:
        await session.execute(
            EtfPairSample.__table__.delete().where(
                EtfPairSample.time >= RANGE_START, EtfPairSample.time < RANGE_END
            )
        )
        await session.commit()


def _sample(minute: int, code: str, lag, premium, day: int = 5) -> EtfPairSample:
    return EtfPairSample(
        time=dt.datetime(2099, 1, day, 10, minute, tzinfo=KST),
        code=code,
        multiple=2 if code == "0193T0" else -2,
        price=7000,
        base_price=6000,
        nav=None if premium is None else 7000,
        underlying_price=1000000,
        underlying_change_rate=5,
        theory_price=7000,
        premium_pct=premium,
        lag_pct=lag,
    )


@pytest.fixture
async def seeded():
    await _clear()
    async with async_session_factory() as session:
        session.add_all(
            [
                _sample(0, "0193T0", 0.1, 0.2),
                _sample(1, "0193T0", 0.3, 0.4),
                _sample(2, "0193T0", 0.5, None),
                _sample(0, "0197X0", -0.2, 0.0),
                _sample(0, "0193T0", 9.0, 9.0, day=6),
            ]
        )
        await session.commit()
    yield
    await _clear()
    await engine.dispose()


def _client() -> AsyncClient:
    app = FastAPI()
    app.include_router(router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_samples_summary_for_date(seeded):
    async with _client() as c:
        resp = await c.get("/api/etf-pair/samples", params={"date": DAY.isoformat()})
    body = resp.json()
    assert body["date"] == "2099-01-05"
    assert len(body["rows"]) == 4
    assert [r["time"] for r in body["rows"]] == sorted(r["time"] for r in body["rows"])
    lev = body["summary"]["0193T0"]
    assert lev["count"] == 3
    assert lev["lag_pct"]["mean"] == pytest.approx(0.3)
    assert lev["lag_pct"]["min"] == 0.1 and lev["lag_pct"]["max"] == 0.5
    assert lev["premium_pct"]["mean"] == pytest.approx(0.3)  # None 제외
    assert body["summary"]["0197X0"]["count"] == 1


async def test_samples_code_filter_and_latest_default(seeded):
    async with _client() as c:
        resp = await c.get("/api/etf-pair/samples", params={"date": "2099-01-05", "code": "0197X0"})
        assert [r["code"] for r in resp.json()["rows"]] == ["0197X0"]
        # date 생략 시 최신 적재일(2099-01-06)
        latest = (await c.get("/api/etf-pair/samples")).json()
    assert latest["date"] == "2099-01-06"
    assert latest["summary"]["0193T0"]["count"] == 1


async def test_samples_empty_day(seeded):
    async with _client() as c:
        body = (await c.get("/api/etf-pair/samples", params={"date": "2099-01-07"})).json()
    assert body["rows"] == [] and body["summary"] == {} and body["date"] == "2099-01-07"
