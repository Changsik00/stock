"""GET /api/markets/nq-comovement 통합 테스트 (PLAN.md Phase 5.116).

test_flow_consistency_router.py와 같은 방식 — 실 데이터와 겹치지 않는 2099년 날짜에 행을 심고 지운다.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import async_session_factory, engine
from app.main import app
from app.models import IndexIntradayBar, IntradaySample
from app.routers import comovement as router_mod

KST = dt.timezone(dt.timedelta(hours=9))
DAY = dt.date(2099, 1, 5)
PREV = dt.date(2099, 1, 2)


def _kst(day, hour, minute):
    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=KST)


async def _clear() -> None:
    async with async_session_factory() as session:
        await session.execute(IndexIntradayBar.__table__.delete().where(IndexIntradayBar.date >= dt.date(2098, 12, 1)))
        await session.execute(IntradaySample.__table__.delete().where(IntradaySample.time >= _kst(dt.date(2098, 12, 1), 0, 0)))
        await session.commit()


@pytest.fixture(autouse=True)
async def _clean():
    router_mod._history_cache.update({"ts": 0.0, "date": None, "data": None})
    await _clear()
    yield
    await _clear()
    router_mod._history_cache.update({"ts": 0.0, "date": None, "data": None})
    await engine.dispose()


async def test_comovement_perfect_beta_and_gap():
    async with async_session_factory() as session:
        # 전 거래일: 코스피 15:30 종가 1000, NQ 15:25 봉 종가 20000
        session.add(IndexIntradayBar(code="001", date=PREV, time="1530", open=1000, close=1000))
        session.add(IntradaySample(series_key="nq_futures", time=_kst(PREV, 15, 25), value=20000.0, resolution_seconds=300))
        # 오늘: NQ 08:55 봉 = 20400(갭 +2%), 코스피 시가 1010(갭 +1%)
        session.add(IntradaySample(series_key="nq_futures", time=_kst(DAY, 8, 55), value=20400.0, resolution_seconds=300))
        nq, k = 20400.0, 1010.0
        for i in range(20):  # 09:00~10:35 버킷, 코스피 수익률 = 0.5 × NQ 수익률
            r = ((i * 7) % 11 - 5) * 0.1  # %
            nq *= 1 + r / 100
            k *= 1 + 0.5 * r / 100
            minute = 9 * 60 + 5 * i
            session.add(IntradaySample(series_key="nq_futures", time=_kst(DAY, minute // 60, minute % 60), value=nq, resolution_seconds=300))
            m4 = minute + 4
            session.add(
                IndexIntradayBar(code="001", date=DAY, time=f"{m4 // 60:02d}{m4 % 60:02d}", open=1010 if i == 0 else k, close=k)
            )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/nq-comovement")

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == DAY.isoformat()
    assert body["today"]["n"] == 20
    assert body["today"]["corr"] == pytest.approx(1.0, abs=1e-3)
    assert body["today"]["beta"] == pytest.approx(0.5, abs=1e-2)
    # 과거 분포는 실 DB의 이전 거래일들이 채운다(테스트 날짜가 2099년이라 전부 과거) — corr 1.0은 최상위
    assert body["history"]["days"] >= 0
    if body["history"]["days"] >= 10:
        assert body["label"] == "동조 강함"
        assert body["percentile"] == 100.0
    assert body["overnight"]["nq_pct"] == pytest.approx(2.0)
    assert body["overnight"]["kospi_gap_pct"] == pytest.approx(1.0)
    dec = body["decomposition"]
    assert dec["explained_pct"] + dec["unique_pct"] == pytest.approx(dec["kospi_change_pct"])
