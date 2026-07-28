"""Unit tests for GET /api/markets/{market}/volume-profile-history (PLAN.md §5.35-4).

Uses httpx.AsyncClient + ASGITransport against the real FastAPI app, with
get_session overridden to a fake AsyncSession (no real DB) — same no-DB/
no-network philosophy as tests/test_markets_short_selling_router.py (the
closest sibling: DB-only read of a per-market daily table, a collector fills
it in batch, this endpoint just returns rows verbatim).
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import get_session
from app.main import app
from app.models import VolumeProfileDaily


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


async def test_market_volume_profile_history_returns_db_rows_verbatim():
    rows = [
        VolumeProfileDaily(
            entity_type="index",
            entity_code="kospi",
            date=dt.date(2026, 7, 27),
            poc_price=3123.45,
            levels=[{"price_low": 3100.0, "price_high": 3150.0, "price_mid": 3125.0, "volume": 500.0, "is_poc": True}],
            bar_count=180,
            total_volume=90000.0,
            lookback_days=180,
        ),
        VolumeProfileDaily(
            entity_type="index",
            entity_code="kospi",
            date=dt.date(2026, 7, 28),
            poc_price=None,
            levels=[],
            bar_count=0,
            total_volume=None,
            lookback_days=180,
        ),
    ]

    async def fake_get_session():
        yield _FakeSession(rows)

    app.dependency_overrides[get_session] = fake_get_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/kospi/volume-profile-history", params={"days": 90})

    assert resp.status_code == 200
    body = resp.json()
    assert body["market"] == "kospi"
    assert body["days"] == 90
    assert body["series"] == [
        {
            "date": "2026-07-27",
            "poc_price": 3123.45,
            "levels": [
                {
                    "price_low": 3100.0,
                    "price_high": 3150.0,
                    "price_mid": 3125.0,
                    "volume": 500.0,
                    "is_poc": True,
                }
            ],
            "bar_count": 180,
            "total_volume": 90000.0,
            "lookback_days": 180,
        },
        {
            "date": "2026-07-28",
            "poc_price": None,
            "levels": [],
            "bar_count": 0,
            "total_volume": None,
            "lookback_days": 180,
        },
    ]


async def test_market_volume_profile_history_rejects_unknown_market():
    async def fake_get_session():
        yield _FakeSession([])

    app.dependency_overrides[get_session] = fake_get_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/nasdaq/volume-profile-history")

    assert resp.status_code == 400
