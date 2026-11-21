"""GET /api/markets/flow/consistency 통합 테스트 (PLAN.md Phase 5.113).

test_intraday_accumulated_router.py와 같은 방식 — 실 데이터와 겹치지 않는 2099년
날짜에 intraday_sample 행을 직접 심고 응답을 검증한 뒤 지운다.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import async_session_factory, engine
from app.main import app
from app.models import IntradaySample
from app.routers import markets as markets_router

KST = dt.timezone(dt.timedelta(hours=9))
TEST_DAY = dt.date(2099, 1, 5)


def _kst(hour, minute):
    return dt.datetime(TEST_DAY.year, TEST_DAY.month, TEST_DAY.day, hour, minute, tzinfo=KST)


async def _clear() -> None:
    async with async_session_factory() as session:
        await session.execute(IntradaySample.__table__.delete().where(IntradaySample.time >= _kst(0, 0) - dt.timedelta(days=1)))
        await session.commit()


@pytest.fixture(autouse=True)
async def _clean():
    markets_router._consistency_history_cache.update({"ts": 0.0, "date": None, "data": None})
    await _clear()
    yield
    await _clear()
    markets_router._consistency_history_cache.update({"ts": 0.0, "date": None, "data": None})
    await engine.dispose()


async def test_consistency_straight_line_selling():
    async with async_session_factory() as session:
        for i in range(40):  # 09:00부터 10분 간격, 직선 순매도
            t = _kst(9, 0) + dt.timedelta(minutes=10 * i)
            session.add(IntradaySample(series_key="flow_kospi_외국인", time=t, value=-50000.0 * (i + 1), resolution_seconds=0))
            session.add(IntradaySample(series_key="flow_kospi_기관계", time=t, value=1000.0 * (i % 3), resolution_seconds=0))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/flow/consistency")

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == TEST_DAY.isoformat()
    assert body["as_of"] == "15:30"
    foreign = body["markets"]["kospi"]["외국인"]
    assert foreign["label"] == "일관 매도"
    assert foreign["final"] == -2_000_000.0
    assert foreign["down_share"] == 1.0
    assert "size_percentile" in foreign
    # 데이터 없는 시장은 None
    assert body["markets"]["kosdaq"]["외국인"] is None
    assert set(body["history"]) == {"kospi", "kosdaq"}
    assert set(body["program"]) == {"kospi", "kosdaq"}
