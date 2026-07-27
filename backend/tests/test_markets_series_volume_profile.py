"""Unit tests for GET /api/markets/{market}/series's `include_volume_profile`
query param (app.routers.markets._build_prices, PLAN.md §5.34).

Same no-DB/no-network philosophy as test_markets_series_futures_provisional.py:
`get_market_series_from_db` is monkeypatched directly, and a fake session
satisfies `_build_flows`'s `session.execute(...)` with an empty flow list (this
file only asserts on `volume_profile`).
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import get_session
from app.main import app
from app.routers import markets

_BASE_DATE = dt.date(2026, 7, 1)


def _row(offset_days: int, price: float, volume: int) -> dict:
    """저가=고가=price인 "단일 가격" 봉 — quant/volume_profile.py의 균등분배가
    한 bin에만 담기게 해 국소 최댓값(피크) 판정이 뚜렷해지도록 한다(같은 이유가
    tests/test_stocks_router.py::test_series_include_volume_profile_query_param
    에도 있음)."""
    d = _BASE_DATE + dt.timedelta(days=offset_days)
    return {
        "date": d.strftime("%Y%m%d"),
        "open": price,
        "high": price,
        "low": price,
        "close": price,
        "changeRate": 0.0,
        "volume": volume,
        "value": 0,
    }


# 20일은 평범한(작은) 거래량으로 3000pt에, 마지막 5일은 압도적으로 큰 거래량으로
# 3500pt에 몰아 넣는다 — POC가 3500 근처에서 잡혀야 한다.
KOSPI_ROWS = [_row(i, 3000.0, 100) for i in range(20)] + [
    _row(20 + i, 3500.0, 100_000) for i in range(5)
]


class _FakeEmptyScalars:
    def all(self):
        return []


class _FakeEmptyResult:
    def scalars(self):
        return _FakeEmptyScalars()


class _FakeFlowSession:
    async def execute(self, stmt):
        return _FakeEmptyResult()


async def _fake_flow_session():
    yield _FakeFlowSession()


def _fake_db_series(rows: list[dict]):
    async def fake_get_market_series_from_db(session, market, days):
        return [dict(r) for r in rows]

    return fake_get_market_series_from_db


@pytest.fixture(autouse=True)
def _clear_overrides():
    app.dependency_overrides[get_session] = _fake_flow_session
    yield
    app.dependency_overrides.clear()


async def test_market_series_omits_volume_profile_by_default(monkeypatch):
    monkeypatch.setattr(markets, "get_market_series_from_db", _fake_db_series(KOSPI_ROWS))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/kospi/series")

    assert resp.status_code == 200
    assert "volume_profile" not in resp.json()


async def test_market_series_include_volume_profile_true_returns_poc_and_levels(monkeypatch):
    monkeypatch.setattr(markets, "get_market_series_from_db", _fake_db_series(KOSPI_ROWS))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/markets/kospi/series", params={"include_volume_profile": "true"}
        )

    assert resp.status_code == 200
    body = resp.json()
    vp = body["volume_profile"]
    assert vp["bar_count"] == 25
    assert vp["poc"] is not None
    # 가격 범위는 3000(저가 최솟값)~3500(고가 최댓값) — POC는 스파이크가 심어진
    # 3500 근처(마지막 bin)에 있어야 한다.
    assert vp["poc"]["price_mid"] >= 3490.0
    assert len(vp["levels"]) >= 1
    assert vp["levels"][0]["is_poc"] is True
