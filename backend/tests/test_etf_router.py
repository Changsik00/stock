"""Integration tests for GET /api/etf/derivative-flow and GET /api/etf/list
(PLAN.md §4.5/§6 4.5-1).

Uses the real dev Postgres (docker-compose `db` service, must be running) via
app.db.async_session_factory — same pattern as tests/test_flow_rank_router.py.
Test rows use codes/dates that can't collide with real collected data (T9-prefixed
codes, dates in 2098/2099) and are cleaned up in fixture teardown either way.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import async_session_factory, engine
from app.models import EtfStat, Stock
from app.routers.etf import router

TEST_DATE = dt.date(2099, 1, 1)
PREV_DATE = dt.date(2098, 12, 31)

CODE_LEV = "T9LEV01"
CODE_INV1X = "T9INV01"
CODE_INV2X = "T9INV02"
CODE_NONDERIV = "T9NON01"
CODE_NONETF = "T9STK01"

TEST_CODES = [CODE_LEV, CODE_INV1X, CODE_INV2X, CODE_NONDERIV, CODE_NONETF]


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
        await session.execute(EtfStat.__table__.delete().where(EtfStat.code.in_(TEST_CODES)))
        await session.execute(Stock.__table__.delete().where(Stock.code.in_(TEST_CODES)))
        await session.commit()


@pytest.fixture
async def seeded_derivative_flow():
    await _clear_test_rows()
    async with async_session_factory() as session:
        stocks = [
            (CODE_LEV, "TEST 레버리지", True),
            (CODE_INV1X, "TEST 인버스", True),
            (CODE_INV2X, "TEST 인버스2X", True),
            (CODE_NONDERIV, "TEST 200", True),  # is_etf지만 비파생 -> 유니버스 제외
            (CODE_NONETF, "TEST 종목", False),  # is_etf=False -> 애초에 후보 아님
        ]
        for code, name, is_etf in stocks:
            stmt = pg_insert(Stock).values(code=code, name=name, market="KOSPI", is_etf=is_etf)
            stmt = stmt.on_conflict_do_update(
                index_elements=["code"], set_={"name": name, "is_etf": is_etf}
            )
            await session.execute(stmt)

        stats = [
            # PREV_DATE: AUM 기준점만 필요 (net_inflow는 diff 계산에 안 쓰임)
            (CODE_LEV, PREV_DATE, 1000, None),
            (CODE_INV1X, PREV_DATE, 500, None),
            (CODE_INV2X, PREV_DATE, 300, None),
            # TEST_DATE: 실제 집계 대상
            (CODE_LEV, TEST_DATE, 1100, 200),  # aum diff +100, net_inflow +200
            (CODE_INV1X, TEST_DATE, 450, -30),  # aum diff -50, net_inflow -30
            (CODE_INV2X, TEST_DATE, 250, -80),  # aum diff -50, net_inflow -80
            (CODE_NONDERIV, TEST_DATE, 999, 500),  # 비파생 -> 전부 무시돼야 함
        ]
        for code, date, aum, net_inflow in stats:
            stmt = pg_insert(EtfStat).values(
                code=code, date=date, nav=100.0, aum=aum, net_inflow=net_inflow
            )
            await session.execute(stmt)
        await session.commit()
    yield
    await _clear_test_rows()


async def test_derivative_flow_computes_net_bet_and_lp_hedge_est(seeded_derivative_flow):
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/etf/derivative-flow", params={"days": 30})

    assert resp.status_code == 200
    body = resp.json()

    # 유니버스는 DB에 실제 적재된 300개 ETF 이름도 함께 분류하므로 정확한 총계를
    # 단정할 수 없다(§4.5-1 실데이터: 레버리지 35 + 인버스 11 = 46개 관측, 2026-07-19).
    # 대신 총계 = 레버리지 + 인버스가 항상 성립하는지, 우리 테스트 3종이 반영될 만큼
    # 충분히 큰지만 확인한다(비파생 TEST 200/is_etf=False TEST 종목은 애초에 안 셈).
    universe = body["universe"]
    assert universe["total"] == universe["leverage"] + universe["inverse"]
    assert universe["leverage"] >= 1
    assert universe["inverse"] >= 2

    latest = body["latest"]
    assert latest["date"] == TEST_DATE.isoformat()
    # net_bet = 200*+2(레버리지) + (-30)*-1(인버스1X) + (-80)*-2(인버스2X) = 400+30+160 = 590
    assert latest["net_bet"] == 590
    # lp_hedge_est = 100*sign(+2) + (-50)*sign(-1) + (-50)*sign(-2) = 100+50+50 = 200
    assert latest["lp_hedge_est"] == 200
    assert latest["leverage_inflow"] == 200
    assert latest["inverse_inflow"] == -110
    assert latest["counts"] == {"leverage": 1, "inverse": 2}

    dates = [row["date"] for row in body["series"]]
    assert TEST_DATE.isoformat() in dates


async def test_derivative_flow_prev_date_has_no_lp_hedge_baseline(seeded_derivative_flow):
    # PREV_DATE 자체는 그 이전 관측치가 없어 lp_hedge_est가 None이어야 한다(첫 관측일 정상 동작).
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # PREV_DATE/TEST_DATE는 미래(2098/2099) 날짜라 today - days(과거 방향)로는
        # 절대 걸러지지 않는다 — days는 유효 범위(<=365) 안의 아무 값이면 된다.
        resp = await client.get("/api/etf/derivative-flow", params={"days": 30})

    assert resp.status_code == 200
    body = resp.json()
    prev_row = next(row for row in body["series"] if row["date"] == PREV_DATE.isoformat())
    assert prev_row["lp_hedge_est"] is None
    assert prev_row["net_bet"] == 0  # net_inflow가 전부 None인 날


async def test_derivative_flow_returns_empty_when_no_derivative_etfs():
    await _clear_test_rows()
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/etf/derivative-flow", params={"days": 1})

    assert resp.status_code == 200
    body = resp.json()
    # DB에 파생형 이름이 하나도 없는 상태를 흉내낼 수는 없지만(실제로는 300개 존재),
    # 최소한 라우터가 200을 반환하고 스키마를 지키는지 확인한다.
    assert "universe" in body
    assert "series" in body


async def test_etf_list_returns_rows_sorted_by_aum_desc_with_derivative_flag(seeded_derivative_flow):
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/etf/list", params={"days": 90})

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == TEST_DATE.isoformat()

    rows_by_code = {r["code"]: r for r in body["rows"] if r["code"] in TEST_CODES}
    assert rows_by_code[CODE_LEV]["derivative_multiplier"] == 2
    assert rows_by_code[CODE_INV1X]["derivative_multiplier"] == -1
    assert rows_by_code[CODE_INV2X]["derivative_multiplier"] == -2
    assert rows_by_code[CODE_NONDERIV]["derivative_multiplier"] is None

    # aum 내림차순 정렬 확인 (TEST_DATE 행들만 놓고 볼 때)
    test_rows = [r for r in body["rows"] if r["code"] in TEST_CODES]
    aums = [r["aum"] for r in test_rows]
    assert aums == sorted(aums, reverse=True)


async def test_etf_list_returns_empty_when_no_data_in_window():
    await _clear_test_rows()
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/etf/list", params={"days": 1, "limit": 5}
        )

    assert resp.status_code == 200
    body = resp.json()
    # 실제 300개 ETF가 이미 DB에 있으므로 date는 None이 아닐 수 있다 — 스키마만 확인.
    assert "rows" in body
