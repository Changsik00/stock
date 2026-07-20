"""Unit tests for the market_breadth endpoints in app.routers.markets
(GET /api/markets/{market}/breadth, GET /api/markets/breadth/live — PLAN.md §4.6 3.6-2).

Uses httpx.AsyncClient + ASGITransport against the real FastAPI app, with
get_session overridden to a fake AsyncSession (no real DB) and the blocking
naver_breadth fetch monkeypatched (no real network) — same no-DB/no-network
philosophy as the other collector/client tests in this package.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import get_session
from app.main import app
from app.models import MarketBreadth
from app.routers import markets

KOSPI_LIVE = {"adv": 384, "dec": 488, "flat": 40, "limit_up": 6, "limit_down": 0}
KOSDAQ_LIVE = {"adv": 501, "dec": 1182, "flat": 56, "limit_up": 11, "limit_down": 1}

# breadth/live의 happy-path 테스트들은 실제 지금(wall-clock)이 장중인지와 무관하게
# "장중"을 가정한다 — 2026-07-20 버그 수정으로 _warm_breadth_live가 실제로 장 마감
# 여부를 확인해 네이버 호출을 게이트하므로, 이 monkeypatch 없이는 테스트를 CI가
# 언제 돌리느냐(밤에 돌면 장 마감으로 판정)에 따라 결과가 달라진다. 장 마감 케이스는
# 아래 별도 테스트(test_market_breadth_live_market_closed_*)가 명시적으로 다룬다.
_real_market_closed_kst = markets._market_closed_kst


@pytest.fixture(autouse=True)
def _force_market_open(monkeypatch):
    monkeypatch.setattr(markets, "_market_closed_kst", lambda now_kst: False)


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


@pytest.fixture(autouse=True)
def _reset_live_cache():
    """live 엔드포인트는 모듈 전역 캐시를 쓰므로 테스트 간 오염을 막기 위해 매번 리셋."""
    markets._live_cache["data"] = None
    markets._live_cache["ts"] = 0.0
    yield
    markets._live_cache["data"] = None
    markets._live_cache["ts"] = 0.0


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


class _UnusedSession:
    """장중(open-market) 테스트에서 세션이 실제로는 쓰이지 않음을 검증하는 가짜
    세션 — market_breadth_live가 이제 Depends(get_session)을 요구하므로(장 마감
    DB 폴백용, 2026-07-20) 오버라이드는 필요하지만, 장중 경로에서는 절대
    session.execute가 불려선 안 된다."""

    async def execute(self, stmt):  # pragma: no cover - 불리면 안 됨
        raise AssertionError("session.execute should not be called when market is open")


def _unused_session_override():
    async def fake_get_session():
        yield _UnusedSession()

    return fake_get_session


class _FakeScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeQueueSession:
    """execute() 호출 순서대로 미리 짜둔 결과를 하나씩 반환하는 FIFO 큐 —
    `_fetch_breadth_confirmed_for_market`가 max(date) -> rows 순서로 정확히 두 번
    쿼리하므로(market당) test_markets_flow_live_router.py의 동일한 패턴을 쓴다."""

    def __init__(self, results):
        self._results = list(results)

    async def execute(self, stmt):
        return self._results.pop(0)


def _session_with_queue(results):
    async def fake_get_session():
        yield _FakeQueueSession(results)

    return fake_get_session


async def test_market_breadth_series_returns_db_rows():
    rows = [
        MarketBreadth(
            market="kospi", date=dt.date(2026, 7, 17), adv=400, dec=470, flat=38, limit_up=5, limit_down=1
        ),
        MarketBreadth(
            market="kospi", date=dt.date(2026, 7, 18), adv=384, dec=488, flat=40, limit_up=6, limit_down=0
        ),
    ]

    async def fake_get_session():
        yield _FakeSession(rows)

    app.dependency_overrides[get_session] = fake_get_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/kospi/breadth", params={"days": 30})

    assert resp.status_code == 200
    body = resp.json()
    assert body["market"] == "kospi"
    assert body["days"] == 30
    assert body["series"] == [
        {"date": "2026-07-17", "adv": 400, "dec": 470, "flat": 38, "limit_up": 5, "limit_down": 1},
        {"date": "2026-07-18", "adv": 384, "dec": 488, "flat": 40, "limit_up": 6, "limit_down": 0},
    ]


async def test_market_breadth_series_rejects_unknown_market():
    async def fake_get_session():
        yield _FakeSession([])

    app.dependency_overrides[get_session] = fake_get_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/futures/breadth")

    assert resp.status_code == 400


async def test_market_breadth_live_returns_both_markets(monkeypatch):
    def fake_fetch(market):
        return KOSPI_LIVE if market == "kospi" else KOSDAQ_LIVE

    monkeypatch.setattr(markets, "_fetch_breadth_blocking", fake_fetch)
    app.dependency_overrides[get_session] = _unused_session_override()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/breadth/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["kospi"] == KOSPI_LIVE
    assert body["kosdaq"] == KOSDAQ_LIVE
    assert body["market_closed"] is False
    assert "cached_at" in body


async def test_market_breadth_live_caches_within_ttl(monkeypatch):
    calls = []

    def fake_fetch(market):
        calls.append(market)
        return KOSPI_LIVE if market == "kospi" else KOSDAQ_LIVE

    monkeypatch.setattr(markets, "_fetch_breadth_blocking", fake_fetch)
    app.dependency_overrides[get_session] = _unused_session_override()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/api/markets/breadth/live")
        r2 = await client.get("/api/markets/breadth/live")

    assert r1.json()["cached_at"] == r2.json()["cached_at"]
    # 두 번째 호출은 캐시를 썼으므로 소스 fetch가 다시 불리지 않아야 한다.
    assert calls == ["kospi", "kosdaq"]


async def test_market_breadth_live_survives_one_market_failure(monkeypatch):
    def fake_fetch(market):
        if market == "kosdaq":
            raise RuntimeError("boom")
        return KOSPI_LIVE

    monkeypatch.setattr(markets, "_fetch_breadth_blocking", fake_fetch)
    app.dependency_overrides[get_session] = _unused_session_override()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/breadth/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["kospi"] == KOSPI_LIVE
    assert body["kosdaq"] is None


async def test_market_breadth_live_502_when_both_markets_fail(monkeypatch):
    def fake_fetch(market):
        raise RuntimeError("boom")

    monkeypatch.setattr(markets, "_fetch_breadth_blocking", fake_fetch)
    app.dependency_overrides[get_session] = _unused_session_override()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/breadth/live")

    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# 장 마감 게이트 (2026-07-20 버그 수정) — 장 마감이면 naver_breadth를 아예 호출하지
# 않고 market_breadth DB 확정치(있으면)로 폴백한다. 이 절의 테스트는 위
# `_force_market_open` autouse fixture를 명시적으로 되돌려(monkeypatch로 실제
# 게이트 로직을 다시 씌워) "닫힘"을 강제한다.
# ---------------------------------------------------------------------------


async def test_market_breadth_live_market_closed_uses_db_fallback(monkeypatch):
    monkeypatch.setattr(markets, "_market_closed_kst", lambda now_kst: True)

    def fake_fetch(market):  # pragma: no cover - 불리면 안 됨
        raise AssertionError("naver_breadth should not be called when market is closed")

    monkeypatch.setattr(markets, "_fetch_breadth_blocking", fake_fetch)

    kospi_rows = [
        MarketBreadth(market="kospi", date=dt.date(2026, 7, 17), adv=400, dec=470, flat=38, limit_up=5, limit_down=1)
    ]
    # 순서: kospi max(date) -> kospi rows -> kosdaq max(date, 없음).
    results = [
        _FakeScalarResult(dt.date(2026, 7, 17)),
        _FakeResult(kospi_rows),
        _FakeScalarResult(None),
    ]
    app.dependency_overrides[get_session] = _session_with_queue(results)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/breadth/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["market_closed"] is True
    assert body["kospi"] == {"date": "2026-07-17", "adv": 400, "dec": 470, "flat": 38, "limit_up": 5, "limit_down": 1}
    assert body["kosdaq"] is None


async def test_market_breadth_live_market_closed_no_db_returns_empty_not_502(monkeypatch):
    monkeypatch.setattr(markets, "_market_closed_kst", lambda now_kst: True)

    def fake_fetch(market):  # pragma: no cover - 불리면 안 됨
        raise AssertionError("naver_breadth should not be called when market is closed")

    monkeypatch.setattr(markets, "_fetch_breadth_blocking", fake_fetch)

    # 배치가 한 번도 안 돌았다고 가정 — 두 시장 다 max(date)가 None.
    results = [_FakeScalarResult(None), _FakeScalarResult(None)]
    app.dependency_overrides[get_session] = _session_with_queue(results)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/breadth/live")

    # 장 마감 + DB도 없음은 "소스 장애"가 아니라 "아직 없음"이므로 502가 아니다.
    assert resp.status_code == 200
    body = resp.json()
    assert body["market_closed"] is True
    assert body["kospi"] is None
    assert body["kosdaq"] is None
