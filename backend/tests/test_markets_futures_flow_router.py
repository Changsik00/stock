"""Unit test for GET /api/markets/futures/series flows — PLAN.md §4.5 4.5-2 확인
기준 "futures 시리즈에 flows가 실리는지 확인". market_flow에는 'k200_futures'로
저장되지만 라우터 경로 파라미터는 'futures'라 DB_MARKET 매핑이 맞물리는지 검증한다
(app/routers/markets.py FLOW_MARKETS/_build_flows).

DB 없이 get_session을 FakeSession으로 오버라이드하고, get_market_series_from_db도
monkeypatch해 가격 시리즈 조회(index_ohlcv)까지 갈 필요가 없게 한다 — 이 테스트는
flows 매핑만 검증한다.
"""

from __future__ import annotations

import datetime as dt

from httpx import ASGITransport, AsyncClient

from app.db import get_session
from app.main import app
from app.models import MarketFlow
from app.routers import markets


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
    """MarketFlow.market == 'k200_futures'로 온 쿼리에만 행을 돌려준다 — 만약
    라우터가 매핑을 빼먹고 'futures'로 그대로 쿼리하면 이 테스트가 빈 flows로
    실패해 회귀를 잡는다."""

    def __init__(self, rows):
        self._rows = rows
        self.queried_markets = []

    async def execute(self, stmt):
        compiled = stmt.compile()
        market_param = compiled.params.get("market_1")
        self.queried_markets.append(market_param)
        if market_param == "k200_futures":
            return _FakeResult(self._rows)
        return _FakeResult([])


async def _clear_overrides():
    app.dependency_overrides.clear()


async def test_futures_series_flows_use_k200_futures_storage_key(monkeypatch):
    rows = [
        MarketFlow(
            market="k200_futures",
            date=dt.date(2026, 7, 16),
            investor="외국인",
            net_value=701_400,
            net_volume=None,
            source="naver",
        ),
        MarketFlow(
            market="k200_futures",
            date=dt.date(2026, 7, 16),
            investor="개인",
            net_value=-344_200,
            net_volume=None,
            source="naver",
        ),
    ]
    fake_session = _FakeSession(rows)

    async def fake_get_session():
        yield fake_session

    async def fake_get_market_series_from_db(session, market, days):
        return []

    app.dependency_overrides[get_session] = fake_get_session
    monkeypatch.setattr(markets, "get_market_series_from_db", fake_get_market_series_from_db)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/markets/futures/series", params={"days": 90})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["market"] == "futures"
    assert "k200_futures" in fake_session.queried_markets

    flows = body["flows"]
    assert set(flows.keys()) == {"외국인", "개인"}
    assert flows["외국인"] == [{"date": "2026-07-16", "net_value": 701_400, "net_volume": None}]
    assert flows["개인"] == [{"date": "2026-07-16", "net_value": -344_200, "net_volume": None}]
