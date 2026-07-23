"""Unit tests for GET /api/markets/{market}/series's futures "오늘 잠정치" 합성 행
(app.routers.markets._build_prices / _append_futures_provisional_row, PLAN.md §5.21).

Same no-DB/no-network philosophy as the other live-endpoint tests in this
package (e.g. test_markets_index_tiles_router.py): `get_market_series_from_db`
is monkeypatched directly (no real DB), and `basis_router._warm_basis_live` is
monkeypatched instead of hitting the real naver_index/basis live cache. A
minimal fake session is used only so `_build_flows`'s `session.execute(...)`
(always called by `market_series`, regardless of market) doesn't blow up —
it returns an empty flow list, which is fine since these tests only assert on
`prices`.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import get_session
from app.main import app
from app.routers import markets

DB_ROWS_FUTURES = [
    {
        "date": "20260722",
        "open": 400.0,
        "high": 405.0,
        "low": 398.0,
        "close": 402.0,
        "changeRate": 0.5,
        "volume": 100,
        "value": 0,
    }
]

DB_ROWS_KOSPI = [
    {
        "date": "20260722",
        "open": 3000.0,
        "high": 3050.0,
        "low": 2990.0,
        "close": 3020.0,
        "changeRate": 0.3,
        "volume": 1000,
        "value": 0,
    }
]


class _FakeEmptyScalars:
    def all(self):
        return []


class _FakeEmptyResult:
    def scalars(self):
        return _FakeEmptyScalars()


class _FakeFlowSession:
    """market_flow 조회(`_build_flows`)를 항상 빈 결과로 응답 — 이 테스트 파일은
    prices만 검증하므로 flows는 신경쓰지 않는다."""

    async def execute(self, stmt):
        return _FakeEmptyResult()


async def _fake_flow_session():
    yield _FakeFlowSession()


def _fake_db_series(rows_by_market: dict):
    async def fake_get_market_series_from_db(session, market, days):
        return [dict(r) for r in rows_by_market.get(market, [])]

    return fake_get_market_series_from_db


def _fake_basis_live(futures_today: dict | None, market_closed: bool = False):
    async def fake_warm_basis_live():
        return {
            "date": futures_today["date"] if futures_today else None,
            "futures_close": futures_today["close"] if futures_today else None,
            "kospi200_close": None,
            "basis": None,
            "basis_pct": None,
            "backwardation": None,
            "futures_today": futures_today,
            "expiry": {"date": "2099-01-01", "d_day": 1, "quadruple": False},
            "market_closed": market_closed,
            "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }

    return fake_warm_basis_live


@pytest.fixture(autouse=True)
def _clear_overrides():
    app.dependency_overrides[get_session] = _fake_flow_session
    yield
    app.dependency_overrides.clear()


async def test_futures_series_appends_provisional_row_when_newer_than_db(monkeypatch):
    monkeypatch.setattr(markets, "get_market_series_from_db", _fake_db_series({"futures": DB_ROWS_FUTURES}))
    futures_today = {
        "date": "2026-07-23",
        "open": 403.0,
        "high": 410.0,
        "low": 401.0,
        "close": 408.0,
        "volume": 55,
    }
    monkeypatch.setattr(markets.basis_router, "_warm_basis_live", _fake_basis_live(futures_today))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/futures/series")

    assert resp.status_code == 200
    body = resp.json()
    prices = body["prices"]
    assert len(prices) == 2
    last = prices[-1]
    assert last["date"] == "20260723"
    assert last["open"] == 403.0
    assert last["high"] == 410.0
    assert last["low"] == 401.0
    assert last["close"] == 408.0
    assert last["volume"] == 55
    assert last["value"] == 0
    assert last["provisional"] is True
    assert last["changeRate"] == round((408.0 - 402.0) / 402.0 * 100, 4)
    # DB 확정 행에는 provisional 키 자체가 없어야 한다(백필 금지).
    assert "provisional" not in prices[0]


async def test_futures_series_skips_provisional_row_when_not_newer_than_db(monkeypatch):
    """EOD 배치가 이미 오늘 날짜를 확정 행으로 채웠으면(또는 그보다 최신이 아니면)
    합성 행을 붙이지 않는다 — 중복 방지."""
    monkeypatch.setattr(markets, "get_market_series_from_db", _fake_db_series({"futures": DB_ROWS_FUTURES}))
    futures_today = {
        "date": "2026-07-22",  # DB 마지막 행과 같은 날짜 -> 더 최신이 아님
        "open": 400.0,
        "high": 405.0,
        "low": 398.0,
        "close": 402.0,
        "volume": 100,
    }
    monkeypatch.setattr(markets.basis_router, "_warm_basis_live", _fake_basis_live(futures_today))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/futures/series")

    body = resp.json()
    prices = body["prices"]
    assert prices == DB_ROWS_FUTURES
    assert "provisional" not in prices[-1]


async def test_futures_series_skips_provisional_row_when_market_closed(monkeypatch):
    monkeypatch.setattr(markets, "get_market_series_from_db", _fake_db_series({"futures": DB_ROWS_FUTURES}))
    futures_today = {
        "date": "2026-07-23",
        "open": 403.0,
        "high": 410.0,
        "low": 401.0,
        "close": 408.0,
        "volume": 55,
    }
    monkeypatch.setattr(
        markets.basis_router, "_warm_basis_live", _fake_basis_live(futures_today, market_closed=True)
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/futures/series")

    body = resp.json()
    assert body["prices"] == DB_ROWS_FUTURES


async def test_futures_series_degrades_gracefully_when_basis_live_raises(monkeypatch):
    """/series는 "DB 전용, 항상 성공" 계약 — 라이브 basis 조회가 실패해도 200 +
    DB 행만 반환해야 한다(500이면 안 됨)."""
    monkeypatch.setattr(markets, "get_market_series_from_db", _fake_db_series({"futures": DB_ROWS_FUTURES}))

    async def _raise():
        raise RuntimeError("boom")

    monkeypatch.setattr(markets.basis_router, "_warm_basis_live", _raise)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/futures/series")

    assert resp.status_code == 200
    assert resp.json()["prices"] == DB_ROWS_FUTURES


async def test_kospi_series_unaffected_by_basis_live(monkeypatch):
    """코스피/코스닥은 이 워크어라운드 대상이 아니다 — basis/live가 어떤 값을
    주든(심지어 호출되더라도) prices에 provisional 행이 붙으면 안 된다."""
    monkeypatch.setattr(markets, "get_market_series_from_db", _fake_db_series({"kospi": DB_ROWS_KOSPI}))

    async def _unexpected():  # pragma: no cover - 불리면 안 됨(호출되더라도 provisional 미반영 검증)
        return {
            "futures_today": {
                "date": "2026-07-23",
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1,
            },
            "market_closed": False,
        }

    monkeypatch.setattr(markets.basis_router, "_warm_basis_live", _unexpected)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/kospi/series")

    body = resp.json()
    assert body["prices"] == DB_ROWS_KOSPI
    assert all("provisional" not in row for row in body["prices"])
