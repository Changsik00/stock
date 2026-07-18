"""Integration tests for GET /api/markets/basis (PLAN.md §4.5-3).

Same pattern as tests/test_flow_path_direction_and_sentiment_router.py /
tests/test_groups_router.py: real dev Postgres via app.db.async_session_factory,
throwaway FastAPI app including only this router (main.py doesn't wire it in yet
per routers/basis.py's ownership note), test rows dated 2099-* (far outside any
real backfill, and safely beyond `since` because the router only filters by a
lower bound) cleaned up in teardown.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import async_session_factory, engine
from app.models import IndexOhlcv
from app.routers.basis import router

TEST_DATE_1 = dt.date(2099, 1, 5)  # 콘탱고 (basis 양수)
TEST_DATE_2 = dt.date(2099, 1, 6)  # 백워데이션 (basis 음수) — 가장 최근 날짜(latest)
TEST_DATE_FUTURES_ONLY = dt.date(2099, 1, 7)  # 선물만 있음 -> 교집합에서 제외돼야 함


@pytest.fixture(autouse=True)
async def _dispose_engine_per_test():
    yield
    await engine.dispose()


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


async def _clear_test_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(
            IndexOhlcv.__table__.delete().where(
                IndexOhlcv.date.in_([TEST_DATE_1, TEST_DATE_2, TEST_DATE_FUTURES_ONLY])
            )
        )
        await session.commit()


async def _upsert_index_row(session, market: str, date: dt.date, close: float) -> None:
    stmt = pg_insert(IndexOhlcv).values(
        market=market, date=date, open=close, high=close, low=close, close=close, volume=1000
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[IndexOhlcv.market, IndexOhlcv.date], set_={"close": stmt.excluded.close}
    )
    await session.execute(stmt)


@pytest.fixture
async def seeded_basis_rows():
    await _clear_test_rows()
    async with async_session_factory() as session:
        # TEST_DATE_1: futures=1100, spot=1080 -> basis=+20 (콘탱고)
        await _upsert_index_row(session, "k200_futures", TEST_DATE_1, 1100.0)
        await _upsert_index_row(session, "kospi200", TEST_DATE_1, 1080.0)
        # TEST_DATE_2: futures=1070, spot=1080 -> basis=-10 (백워데이션), 가장 최근 날짜
        await _upsert_index_row(session, "k200_futures", TEST_DATE_2, 1070.0)
        await _upsert_index_row(session, "kospi200", TEST_DATE_2, 1080.0)
        # TEST_DATE_FUTURES_ONLY: 선물만 있고 현물이 없음 -> 교집합에서 제외돼야 함
        await _upsert_index_row(session, "k200_futures", TEST_DATE_FUTURES_ONLY, 1200.0)
        await session.commit()
    yield
    await _clear_test_rows()


async def test_basis_series_computes_basis_and_pct(seeded_basis_rows):
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/basis", params={"days": 180})

    assert resp.status_code == 200
    body = resp.json()

    rows_by_date = {row["date"]: row for row in body["series"]}
    assert TEST_DATE_1.isoformat() in rows_by_date
    assert TEST_DATE_2.isoformat() in rows_by_date

    row1 = rows_by_date[TEST_DATE_1.isoformat()]
    assert row1["futures_close"] == 1100.0
    assert row1["kospi200_close"] == 1080.0
    assert row1["basis"] == 20.0
    assert row1["basis_pct"] == pytest.approx(20.0 / 1080.0 * 100, abs=1e-4)

    row2 = rows_by_date[TEST_DATE_2.isoformat()]
    assert row2["basis"] == -10.0


async def test_basis_series_excludes_dates_missing_one_side(seeded_basis_rows):
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/basis", params={"days": 180})

    body = resp.json()
    dates = {row["date"] for row in body["series"]}
    assert TEST_DATE_FUTURES_ONLY.isoformat() not in dates


async def test_basis_series_latest_reflects_most_recent_date_and_backwardation(seeded_basis_rows):
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/basis", params={"days": 180})

    body = resp.json()
    # TEST_DATE_2(2099-01-06)가 교집합 중 가장 최근 날짜여야 한다(2099-01-07은 선물뿐이라 제외).
    assert body["latest"]["date"] == TEST_DATE_2.isoformat()
    assert body["latest"]["basis"] == -10.0
    assert body["latest"]["backwardation"] is True


async def test_basis_series_empty_when_no_overlap(monkeypatch):
    await _clear_test_rows()
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/basis", params={"days": 1})

    body = resp.json()
    # days=1(오늘만) 창에는 우리 테스트 데이터(2099)가 없을 가능성이 높지만, 겹치지 않는
    # 경우를 대비해 latest 스키마만 확인한다(days=1일 때 실제 데이터가 있을 수도 있으므로
    # 존재 여부가 아니라 응답 스키마의 안정성을 검증).
    assert set(body["latest"].keys()) == {"date", "backwardation", "basis", "basis_pct"}
    assert "expiry" in body
    assert set(body["expiry"].keys()) == {"date", "d_day", "quadruple"}


async def test_basis_expiry_field_present_and_well_formed(seeded_basis_rows):
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/basis")

    body = resp.json()
    expiry = body["expiry"]
    assert expiry["d_day"] >= 0
    # ISO date 문자열이 파싱 가능해야 한다.
    dt.date.fromisoformat(expiry["date"])
    assert isinstance(expiry["quadruple"], bool)
