"""Unit tests for GET /api/stocks/{code}/volume-profile-history (PLAN.md §5.35-4).

Same no-DB fake-session pattern as tests/test_markets_volume_profile_history_router.py
(the markets-side sibling) — DB-only read of volume_profile_daily, no real DB
needed to verify the endpoint's query/serialization shape.
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


async def test_stock_volume_profile_history_returns_db_rows_verbatim():
    rows = [
        VolumeProfileDaily(
            entity_type="stock",
            entity_code="005930",
            date=dt.date(2026, 7, 27),
            poc_price=71500.0,
            levels=[{"price_low": 71000.0, "price_high": 72000.0, "price_mid": 71500.0, "volume": 12345.0, "is_poc": True}],
            bar_count=180,
            total_volume=987654.0,
            lookback_days=180,
        ),
    ]

    async def fake_get_session():
        yield _FakeSession(rows)

    app.dependency_overrides[get_session] = fake_get_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/stocks/005930/volume-profile-history", params={"days": 90})

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == "005930"
    assert body["days"] == 90
    assert body["series"] == [
        {
            "date": "2026-07-27",
            "poc_price": 71500.0,
            "levels": [
                {
                    "price_low": 71000.0,
                    "price_high": 72000.0,
                    "price_mid": 71500.0,
                    "volume": 12345.0,
                    "is_poc": True,
                }
            ],
            "bar_count": 180,
            "total_volume": 987654.0,
            "lookback_days": 180,
        }
    ]


async def test_stock_volume_profile_history_returns_empty_series_when_no_snapshots():
    async def fake_get_session():
        yield _FakeSession([])

    app.dependency_overrides[get_session] = fake_get_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/stocks/999999/volume-profile-history")

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == "999999"
    assert body["series"] == []
