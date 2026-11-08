"""GET /api/markets/vol-regime 라우터 테스트 (PLAN.md §5.95) — DB 미사용(로더 monkeypatch), 실 DB 잔여물 없음."""

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import get_session
from app.main import app
from app.routers import markets


class _FakeSession:
    pass


def _bars(start, n, price=100.0):
    return [{"time": f"{(start + i) // 60:02d}{(start + i) % 60:02d}", "m": start + i, "o": price, "h": price + 0.5, "l": price - 0.5, "c": price + (i % 2)} for i in range(n)]


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    markets._vol_cache.update({"ts": 0.0, "data": None})
    markets._vol_dist_cache.clear()

    async def fake_session():
        yield _FakeSession()

    app.dependency_overrides[get_session] = fake_session
    yield
    app.dependency_overrides.clear()
    markets._vol_cache.update({"ts": 0.0, "data": None})
    markets._vol_dist_cache.clear()


def _patch(monkeypatch, bars_by_code, calls):
    day = dt.date(2026, 11, 6)

    async def latest(session, code):
        calls.append(("bars", code))
        b = bars_by_code[code]
        return (day, b) if b else (None, [])

    async def ctx(session, market):
        return {"__next": {"prev_c": 100.0, "avg20": 2.0}}

    async def dist(session, market, code, target, daily_ctx):
        calls.append(("dist", market))
        return {5: [0.1, 0.2, 0.3], 15: [0.1, 0.2, 0.3], 30: [0.1, 0.2, 0.3]}

    monkeypatch.setattr(markets, "_vol_load_latest_bars", latest)
    monkeypatch.setattr(markets, "_vol_load_daily_ctx", ctx)
    monkeypatch.setattr(markets, "_vol_distribution", dist)


async def _get():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.get("/api/markets/vol-regime")


@pytest.mark.asyncio
async def test_vol_regime_partial_and_high(monkeypatch):
    calls = []
    kospi = _bars(540, 3) + _bars(573, 40)  # 서킷브레이커형 공백
    kosdaq = _bars(540, 40, 95.0)  # 갭다운 + 변동
    _patch(monkeypatch, {"001": kospi, "101": kosdaq}, calls)
    r = await _get()
    assert r.status_code == 200
    j = r.json()
    assert j["kospi"]["label"] == "partial" and j["kospi"]["has_gap"]
    assert j["kosdaq"]["label"] == "high" and j["kosdaq"]["label_basis_minutes"] == 30
    assert "매매 신호가 아닙니다" in j["disclaimer"]
    assert j["thresholds"] == {"calm_below": 40.0, "high_from": 80.0}
    assert {w["minutes"] for w in j["kosdaq"]["windows"]} == {5, 15, 30}


@pytest.mark.asyncio
async def test_vol_regime_no_data_and_cache(monkeypatch):
    calls = []
    _patch(monkeypatch, {"001": [], "101": []}, calls)
    j = (await _get()).json()
    assert j["kospi"]["label"] == "nodata" and j["kosdaq"]["bar_date"] is None
    n = len(calls)
    await _get()  # 60초 캐시 -> 로더 재호출 없음
    assert len(calls) == n
