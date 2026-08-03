"""Integration tests for GET /api/markets/positioning-hitrate (app.routers.markets,
PLAN.md §5.52 — §5.50 포지셔닝 프레임 사후 검증).

Same house pattern as tests/test_scalp_tracker.py's `/track-record` API test:
this endpoint queries `positioning_snapshot` directly (no reusable warm
function to monkeypatch), so real dev Postgres via app.db.async_session_factory
is used, with test rows dated far in the future (2099-01 ~ 2099-02) cleaned up
in teardown. A throwaway FastAPI app including only the markets router is used
so `get_session` resolves to the real dev DB with no dependency override
needed (same reasoning as test_paper_trades_router.py's docstring).

The grouping/stat math itself is already covered by
tests/test_positioning_backtest.py's pure-function tests — this file only
verifies the router's SQL filtering (next_day_change_rate IS NOT NULL) and
the total_days_collected count wiring.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.db import async_session_factory, engine
from app.models import PositioningSnapshot
from app.routers.markets import router as markets_router

RANGE_START = dt.date(2099, 1, 1)
RANGE_END = dt.date(2099, 3, 1)  # exclusive upper bound, buffer past inserted rows


async def _clear_test_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(
            PositioningSnapshot.__table__.delete().where(
                PositioningSnapshot.date >= RANGE_START, PositioningSnapshot.date < RANGE_END
            )
        )
        await session.commit()


@pytest.fixture(autouse=True)
async def _clean_positioning_snapshot():
    await _clear_test_rows()
    yield
    await _clear_test_rows()
    await engine.dispose()


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(markets_router)
    return app


async def _seed_rows(session, start: dt.date, count: int, **kwargs) -> None:
    for i in range(count):
        session.add(
            PositioningSnapshot(
                date=start + dt.timedelta(days=i),
                snapshot_at=dt.datetime(2099, 1, 1, tzinfo=dt.timezone.utc),
                **kwargs,
            )
        )
    await session.commit()


async def test_positioning_hitrate_groups_and_hides_small_samples():
    async with async_session_factory() as session:
        baseline_total = await session.scalar(select(func.count()).select_from(PositioningSnapshot))

        # 25건: n>=MIN_SAMPLES(20) -> 평균/상승확률 노출.
        await _seed_rows(
            session,
            dt.date(2099, 1, 1),
            25,
            regime="코스피우세",
            relative_strength_pct=0.5,
            foreign_spot_cum=100.0,
            foreign_futures_cum=-100.0,
            nasdaq_futures_change_pct=0.2,
            next_day_change_rate=1.0,
        )
        # 5건: n<MIN_SAMPLES -> 표본수만.
        await _seed_rows(
            session,
            dt.date(2099, 2, 1),
            5,
            regime="코스닥우세",
            relative_strength_pct=-0.5,
            foreign_spot_cum=-50.0,
            foreign_futures_cum=50.0,
            nasdaq_futures_change_pct=-0.1,
            next_day_change_rate=-1.0,
        )
        # 2건: next_day_change_rate가 아직 None -> 집계 대상에서 자동 제외되지만
        # total_days_collected에는 포함돼야 한다.
        await _seed_rows(session, dt.date(2099, 2, 10), 2, regime="중립")

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/positioning-hitrate")

    assert resp.status_code == 200
    body = resp.json()

    assert body["total_days_collected"] == baseline_total + 32
    assert body["min_samples"] == 20
    assert "computed_at" in body

    assert body["by_regime"]["코스피우세"] == {
        "n": 25,
        "avg_next_day_change_rate": 1.0,
        "positive_rate_pct": 100.0,
    }
    assert body["by_regime"]["코스닥우세"] == {
        "n": 5,
        "avg_next_day_change_rate": None,
        "positive_rate_pct": None,
    }
    # "중립" 2건은 next_day_change_rate가 없어 집계에서 아예 빠진다 -> 키 없음.
    assert "중립" not in body["by_regime"]

    assert body["by_relative_strength_sign"]["positive"]["n"] == 25
    assert body["by_relative_strength_sign"]["negative"]["n"] == 5
    assert body["by_foreign_spot_sign"]["positive"]["n"] == 25
    assert body["by_foreign_spot_sign"]["negative"]["n"] == 5
    assert body["by_foreign_futures_sign"]["positive"]["n"] == 5
    assert body["by_foreign_futures_sign"]["negative"]["n"] == 25
    assert body["by_nasdaq_futures_sign"]["positive"]["n"] == 25
    assert body["by_nasdaq_futures_sign"]["negative"]["n"] == 5


async def test_positioning_hitrate_no_rows_returns_empty_groups_and_true_count():
    async with async_session_factory() as session:
        baseline_total = await session.scalar(select(func.count()).select_from(PositioningSnapshot))

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/positioning-hitrate")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_days_collected"] == baseline_total
    assert body["by_regime"] == {}
    assert body["by_relative_strength_sign"] == {}
    assert body["min_samples"] == 20
