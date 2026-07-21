"""Unit tests for GET /api/markets/fx/live (app.routers.markets, PLAN.md §5.5-3
장중 실측 — naver front-api의 "오늘" 행이 고시회차 갱신을 반영함을 60~90초 간격
실호출로 확인해 1분 캐시 라이브 엔드포인트로 편입).

httpx.AsyncClient + ASGITransport against the real FastAPI app. No real network —
the blocking naver_fx call is monkeypatched via markets._fetch_fx_latest_blocking,
and the macro_series DB fallback uses a fake session (같은 no-DB/no-network 철학,
test_markets_breadth_router.py/test_futures_flow_live_router.py와 동일한 패턴).
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import get_session
from app.main import app
from app.routers import markets

TODAY_ROW = {"date": dt.date(2026, 7, 21), "value": 1474.5}


@pytest.fixture(autouse=True)
def _reset_cache():
    markets._fx_live_cache["data"] = None
    markets._fx_live_cache["ts"] = 0.0
    yield
    markets._fx_live_cache["data"] = None
    markets._fx_live_cache["ts"] = 0.0


@pytest.fixture(autouse=True)
def _force_market_open(monkeypatch):
    monkeypatch.setattr(markets, "_market_closed_kst", lambda now_kst: False)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


class _UnusedSession:
    """장중 경로에서는 macro_series DB를 절대 조회하지 않아야 한다(라이브 fetch가
    성공하면 DB 폴백으로 넘어가지 않음) — 실수로 조회하면 즉시 실패시킨다."""

    async def execute(self, stmt):  # pragma: no cover - 불리면 안 됨
        raise AssertionError("session.execute should not be called when live fetch succeeds")


def _unused_session_override():
    async def fake_get_session():
        yield _UnusedSession()

    return fake_get_session


class _FakeScalars:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return _FakeScalars(self._value)


class _FakeSession:
    def __init__(self, value):
        self._value = value

    async def execute(self, stmt):
        return _FakeResult(self._value)


def _session_override(value):
    """`_UnusedSession`/`_unused_session_override`와 동일한 관례 — FastAPI의
    Depends(get_session)은 override 콜러블 자체가 async generator function이어야
    한다(반환값이 아니라). ``value``를 감싼 `_FakeSession`을 yield하는 함수를
    돌려준다."""

    async def fake_get_session():
        yield _FakeSession(value)

    return fake_get_session


async def test_fx_live_returns_today_row(monkeypatch):
    monkeypatch.setattr(markets, "_fetch_fx_latest_blocking", lambda: TODAY_ROW)
    app.dependency_overrides[get_session] = _unused_session_override()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/fx/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["usdkrw"] == {"date": "2026-07-21", "value": 1474.5, "source": "naver"}
    assert body["market_closed"] is False
    assert "cached_at" in body


async def test_fx_live_caches_within_ttl(monkeypatch):
    calls = []

    def fake_fetch():
        calls.append(1)
        return TODAY_ROW

    monkeypatch.setattr(markets, "_fetch_fx_latest_blocking", fake_fetch)
    app.dependency_overrides[get_session] = _unused_session_override()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/api/markets/fx/live")
        r2 = await client.get("/api/markets/fx/live")

    assert r1.json()["cached_at"] == r2.json()["cached_at"]
    assert len(calls) == 1


async def test_fx_live_falls_back_to_macro_series_db_on_naver_failure(monkeypatch):
    def _raise():
        raise RuntimeError("naver 형식이 바뀜")

    monkeypatch.setattr(markets, "_fetch_fx_latest_blocking", _raise)

    class _Row:
        date = dt.date(2026, 7, 18)
        value = 1480.0

    app.dependency_overrides[get_session] = _session_override(_Row())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/fx/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["usdkrw"] == {"date": "2026-07-18", "value": 1480.0, "source": "macro_series_db"}
    assert body["market_closed"] is False


async def test_fx_live_market_closed_skips_naver_uses_db_fallback(monkeypatch):
    monkeypatch.setattr(markets, "_market_closed_kst", lambda now_kst: True)

    def _raise():  # pragma: no cover - 불리면 안 됨
        raise AssertionError("naver_fx should not be called when market is closed")

    monkeypatch.setattr(markets, "_fetch_fx_latest_blocking", _raise)

    class _Row:
        date = dt.date(2026, 7, 18)
        value = 1480.0

    app.dependency_overrides[get_session] = _session_override(_Row())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/fx/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["market_closed"] is True
    assert body["usdkrw"] == {"date": "2026-07-18", "value": 1480.0, "source": "macro_series_db"}


async def test_fx_live_market_closed_no_db_returns_none_not_502(monkeypatch):
    monkeypatch.setattr(markets, "_market_closed_kst", lambda now_kst: True)

    def _raise():  # pragma: no cover - 불리면 안 됨
        raise AssertionError("naver_fx should not be called when market is closed")

    monkeypatch.setattr(markets, "_fetch_fx_latest_blocking", _raise)
    app.dependency_overrides[get_session] = _session_override(None)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/fx/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["market_closed"] is True
    assert body["usdkrw"] is None
