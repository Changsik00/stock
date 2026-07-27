"""Unit tests for GET /api/markets/{market}/short-selling (PLAN.md §5.32-2).

Uses httpx.AsyncClient + ASGITransport against the real FastAPI app, with
get_session overridden to a fake AsyncSession (no real DB) — same
no-DB/no-network philosophy as tests/test_markets_breadth_router.py (the
closest sibling: DB-only read of a per-market daily table, collector fills it
in batch).
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import get_session
from app.main import app
from app.models import ShortSellingMarket


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, stmt):
        return _FakeResult(self._rows)


async def test_market_short_selling_series_returns_db_rows():
    rows = [
        ShortSellingMarket(
            market="kospi",
            date=dt.date(2026, 7, 23),
            short_volume=17131931,
            short_value=1995708,
            total_volume=438180105,
            total_value=35728405,
            volume_ratio=3.91,
            value_ratio=5.59,
        ),
        ShortSellingMarket(
            market="kospi",
            date=dt.date(2026, 7, 24),
            short_volume=17728078,
            short_value=1708956,
            total_volume=438777893,
            total_value=40282837,
            volume_ratio=4.04,
            value_ratio=4.24,
        ),
    ]

    async def fake_get_session():
        yield _FakeSession(rows)

    app.dependency_overrides[get_session] = fake_get_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/kospi/short-selling", params={"days": 30})

    assert resp.status_code == 200
    body = resp.json()
    assert body["market"] == "kospi"
    assert body["days"] == 30
    assert body["series"] == [
        {
            "date": "2026-07-23",
            "short_volume": 17131931,
            "short_value": 1995708,
            "total_volume": 438180105,
            "total_value": 35728405,
            "volume_ratio": 3.91,
            "value_ratio": 5.59,
        },
        {
            "date": "2026-07-24",
            "short_volume": 17728078,
            "short_value": 1708956,
            "total_volume": 438777893,
            "total_value": 40282837,
            "volume_ratio": 4.04,
            "value_ratio": 4.24,
        },
    ]


async def test_market_short_selling_series_rejects_unknown_market():
    async def fake_get_session():
        yield _FakeSession([])

    app.dependency_overrides[get_session] = fake_get_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/futures/short-selling")

    assert resp.status_code == 400
