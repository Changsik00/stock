"""GET /api/markets/flow-anomaly 통합 테스트 (PLAN.md Phase 5.117).

실 데이터와 겹치지 않는 2099년 날짜·가짜 종목코드 행을 심고 지운다. 오늘 거래대금은 value-rank/live 캐시를 읽으므로
캐시에 가짜 payload를 넣는다(외부 호출 없음).
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import async_session_factory, engine
from app.main import app
from app.models import ShortSellingStock, Stock, StockFlow, StockOhlcv
from app.routers import anomaly as router_mod
from app.routers import flow_rank as flow_rank_router

DAY = dt.date(2099, 1, 5)
HIST = [dt.date(2098, 12, d) for d in range(10, 22)]  # 직전 거래일 12일
SELL, BUY, QUIET = "T117S", "T117B", "T117Q"
CODES = [SELL, BUY, QUIET]


async def _clear() -> None:
    async with async_session_factory() as session:
        for model in (StockOhlcv, StockFlow, ShortSellingStock):
            await session.execute(model.__table__.delete().where(model.code.in_(CODES)))
        await session.execute(Stock.__table__.delete().where(Stock.code.in_(CODES)))
        await session.commit()


@pytest.fixture(autouse=True)
async def _clean(monkeypatch):
    monkeypatch.setattr(router_mod, "FULL_DAY_MIN_ROWS", 1)
    saved = dict(flow_rank_router._value_rank_live_cache)
    router_mod._cache.update({"ts": 0.0, "data": None})
    await _clear()
    yield
    await _clear()
    flow_rank_router._value_rank_live_cache.update(saved)
    router_mod._cache.update({"ts": 0.0, "data": None})
    await engine.dispose()


async def test_flow_anomaly_splits_sell_buy_and_skips_quiet():
    async with async_session_factory() as session:
        for code in CODES:
            session.add(Stock(code=code, name=f"가짜{code}", market="KOSPI", is_etf=False))
        await session.flush()
        for d in HIST:
            for code in CODES:
                # 평균 거래대금 = 10,000원 x 1,000,000주 / 1e6 = 10,000백만원
                session.add(StockOhlcv(code=code, date=d, open=10_000, high=10_000, low=10_000, close=10_000, volume=1_000_000))
            session.add(ShortSellingStock(code=SELL, date=d, volume=1, value=1_000))
        # 오늘 수급: 매도 -11,000(거래대금 30,000의 37%), 매수 +11,000, 조용 +200
        session.add(StockFlow(code=SELL, date=DAY, investor="외국인", net_value=-2_000))
        session.add(StockFlow(code=SELL, date=DAY, investor="기관계", net_value=-9_000))
        session.add(StockFlow(code=BUY, date=DAY, investor="외국인", net_value=8_000))
        session.add(StockFlow(code=BUY, date=DAY, investor="기관계", net_value=3_000))
        session.add(StockFlow(code=QUIET, date=DAY, investor="외국인", net_value=100))
        session.add(StockFlow(code=QUIET, date=DAY, investor="기관계", net_value=100))
        session.add(ShortSellingStock(code=SELL, date=DAY, volume=1, value=3_000))
        await session.commit()

    flow_rank_router._value_rank_live_cache.update(
        ts=0.0,
        data={
            "date": DAY.isoformat(),
            "market_closed": False,
            "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "rows": [
                {"market": "kospi", "code": SELL, "name": "가짜매도", "value": 30_000, "change_rate": -5.0, "is_etf": False},
                {"market": "kospi", "code": BUY, "name": "가짜매수", "value": 25_000, "change_rate": 4.0, "is_etf": False},
                {"market": "kospi", "code": QUIET, "name": "가짜조용", "value": 10_000, "change_rate": 0.1, "is_etf": False},
            ],
        },
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/markets/flow-anomaly")
    assert res.status_code == 200
    body = res.json()
    assert body["date"] == DAY.isoformat() and body["provisional"] is True
    assert [r["code"] for r in body["sell"]] == [SELL]
    assert [r["code"] for r in body["buy"]] == [BUY]
    sell = body["sell"][0]
    assert sell["conditions"] == {"value_surge": True, "flow_large": True, "short_surge": True}
    assert sell["net_flow"] == -11_000 and sell["short_ratio"] == 3.0
    assert body["buy"][0]["short_available"] is False
    assert body["evaluated"] == 3
