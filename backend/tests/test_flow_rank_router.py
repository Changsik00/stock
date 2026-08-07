"""Integration tests for GET /api/markets/value-rank and the market passthrough on
GET /api/markets/flow-rank (PLAN.md §4.6 3.6-1).

Uses the real dev Postgres (docker-compose `db` service, must be running) via
app.db.async_session_factory — same pattern as tests/test_groups_router.py. This
router (routers/flow_rank.py) is already registered in main.py, but for test
isolation we still build a throwaway app and include the router directly (avoids
pulling in the rest of the app's startup/lifespan). Test rows use dates far outside
any real backfill (2099-*) so they can't collide with actual collected data, and are
cleaned up in fixture teardown either way.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import async_session_factory, engine
from app.models import AccumulationPick, FlowRank, ValueRank
from app.routers import flow_rank as flow_rank_module
from app.routers.flow_rank import router

TEST_DATE = dt.date(2099, 1, 1)
OLDER_DATE = dt.date(2098, 1, 1)


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
        await session.execute(ValueRank.__table__.delete().where(ValueRank.date.in_([TEST_DATE, OLDER_DATE])))
        await session.execute(FlowRank.__table__.delete().where(FlowRank.date.in_([TEST_DATE, OLDER_DATE])))
        await session.execute(
            AccumulationPick.__table__.delete().where(AccumulationPick.date.in_([TEST_DATE, OLDER_DATE]))
        )
        await session.commit()


@pytest.fixture
async def seeded_value_rank():
    await _clear_test_rows()
    async with async_session_factory() as session:
        rows = [
            # kospi: rank 1 (SK하이닉스, value 700) > rank 2 (삼성전자, value 500)
            (TEST_DATE, "kospi", 1, "000660", "SK하이닉스", 700, -1.5, False, 3.0),
            (TEST_DATE, "kospi", 2, "005930", "삼성전자", 500, 2.0, False, 1.0),
            # kosdaq: rank 1 (KODEX 200, value 900) — highest of all three when combined
            (TEST_DATE, "kosdaq", 1, "069500", "KODEX 200", 900, 0.5, True, 5.0),
            (OLDER_DATE, "kospi", 1, "005930", "삼성전자", 1, 0.0, False, None),
        ]
        for date, market, rank, code, name, value, chg, is_etf, turnover in rows:
            stmt = pg_insert(ValueRank).values(
                date=date,
                market=market,
                rank=rank,
                code=code,
                name=name,
                value=value,
                change_rate=chg,
                is_etf=is_etf,
                turnover=turnover,
            )
            await session.execute(stmt)
        await session.commit()
    yield
    await _clear_test_rows()


@pytest.fixture
async def seeded_flow_rank():
    await _clear_test_rows()
    async with async_session_factory() as session:
        stmt = pg_insert(FlowRank).values(
            date=TEST_DATE,
            investor="foreign",
            side="buy",
            rank=1,
            code="000660",
            name="SK하이닉스",
            net_value=700,
            quantity=3,
            turnover=3.0,
            is_etf=False,
            market="kospi",
        )
        await session.execute(stmt)
        await session.commit()
    yield
    await _clear_test_rows()


async def test_value_rank_kospi_returns_stored_rank_order(seeded_value_rank):
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/markets/value-rank", params={"market": "kospi", "days": 1})

    assert resp.status_code == 200
    body = resp.json()
    assert body["market"] == "kospi"
    assert body["date"] == TEST_DATE.isoformat()
    assert [r["code"] for r in body["rows"]] == ["000660", "005930"]
    assert body["rows"][0] == {
        "rank": 1,
        "market": "kospi",
        "code": "000660",
        "name": "SK하이닉스",
        "value": 700,
        "change_rate": -1.5,
        "is_etf": False,
        "turnover": 3.0,
    }


async def test_value_rank_all_merges_markets_and_reranks_by_value_desc(seeded_value_rank):
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/markets/value-rank", params={"market": "all", "days": 1})

    assert resp.status_code == 200
    body = resp.json()
    # KODEX 200 (900, kosdaq) > SK하이닉스 (700, kospi) > 삼성전자 (500, kospi) —
    # display rank 1..3 is reassigned regardless of each row's stored per-market rank.
    assert [(r["rank"], r["code"], r["market"]) for r in body["rows"]] == [
        (1, "069500", "kosdaq"),
        (2, "000660", "kospi"),
        (3, "005930", "kospi"),
    ]


async def test_value_rank_rejects_unknown_market():
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/markets/value-rank", params={"market": "nyse"})

    assert resp.status_code == 400


async def test_value_rank_returns_empty_rows_when_no_data_in_window(monkeypatch):
    """days 창 안에 매치되는 날짜가 없을 때 빈 응답을 반환하는지 확인한다.

    route가 `dt.date.today()`를 기준으로 창을 계산하므로(routers/flow_rank.py),
    실제 오늘 날짜에는 이제 진짜 배치 데이터가 들어있다(2026-07-21 도커 워커
    분리로 일별 배치가 정상 완주하게 됐다 — PLAN.md §7). "오늘 데이터가 없다"는
    가정으로 짜여 있던 이 테스트가 그 수정으로 깨졌었다 — 이 파일의 다른 테스트들
    처럼(모듈 docstring 참고) 오늘 날짜 대신 TEST_DATE(2099-*) 기준 창을 쓰도록
    `dt.date.today`를 몽키패치해 실제 운영 데이터와 무관하게 만든다.
    """
    await _clear_test_rows()

    class _FixedDate(dt.date):
        @classmethod
        def today(cls):
            return TEST_DATE

    monkeypatch.setattr(flow_rank_module.dt, "date", _FixedDate)

    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/markets/value-rank", params={"market": "kospi", "days": 1})

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] is None
    assert body["rows"] == []


async def test_flow_rank_response_rows_include_market(seeded_flow_rank):
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/markets/flow-rank", params={"investor": "foreign", "side": "buy", "days": 1}
        )

    assert resp.status_code == 200
    body = resp.json()
    matching_dates = [d for d in body["dates"] if d["date"] == TEST_DATE.isoformat()]
    assert len(matching_dates) == 1
    assert matching_dates[0]["rows"][0]["market"] == "kospi"


# -- GET /api/markets/accumulation-screener (2026-08-07 추가) ----------------
#
# 개인 매도/외국인·기관 전환 매집 관찰 스크리너 — DB 전용 조회(collectors/
# accumulation_screener.py가 미리 적재한 accumulation_pick을 그대로 읽는다),
# 참고용 스크리닝이지 매매 신호가 아니다.


@pytest.fixture
async def seeded_accumulation_pick():
    await _clear_test_rows()
    async with async_session_factory() as session:
        rows = [
            (TEST_DATE, "000100", "유한양행", "KOSPI"),
            (OLDER_DATE, "005930", "삼성전자", "KOSPI"),
        ]
        for date, code, name, market in rows:
            stmt = pg_insert(AccumulationPick).values(
                date=date,
                code=code,
                name=name,
                market=market,
                individual_net_10d=-1572.0,
                foreign_inst_net_recent5d=823.0,
                foreign_inst_net_prior5d=-120.0,
                price_return_10d_pct=8.32,
                max_abs_daily_return_10d_pct=3.1,
                reason="개인 10일 순매도 -1,572백만(조건1 충족...). ...",
            )
            await session.execute(stmt)
        await session.commit()
    yield
    await _clear_test_rows()


async def test_accumulation_screener_returns_seeded_row_within_days_window(
    monkeypatch, seeded_accumulation_pick
):
    class _FixedDate(dt.date):
        @classmethod
        def today(cls):
            return TEST_DATE

    monkeypatch.setattr(flow_rank_module.dt, "date", _FixedDate)

    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/markets/accumulation-screener", params={"days": 1})

    assert resp.status_code == 200
    body = resp.json()
    assert body["days"] == 1
    assert "매매 신호" in body["disclaimer"]
    codes = [r["code"] for r in body["rows"]]
    assert codes == ["000100"]  # OLDER_DATE(005930)는 1일 창 밖

    row = body["rows"][0]
    assert row["date"] == TEST_DATE.isoformat()
    assert row["name"] == "유한양행"
    assert row["market"] == "KOSPI"
    assert row["individual_net_10d"] == -1572.0
    assert row["foreign_inst_net_recent5d"] == 823.0
    assert row["foreign_inst_net_prior5d"] == -120.0
    assert row["price_return_10d_pct"] == 8.32
    assert row["max_abs_daily_return_10d_pct"] == 3.1
    assert row["reason"]


async def test_accumulation_screener_wider_window_includes_older_row(
    monkeypatch, seeded_accumulation_pick
):
    class _FixedDate(dt.date):
        @classmethod
        def today(cls):
            return TEST_DATE

    monkeypatch.setattr(flow_rank_module.dt, "date", _FixedDate)

    days = (TEST_DATE - OLDER_DATE).days + 1
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/markets/accumulation-screener", params={"days": days})

    assert resp.status_code == 200
    body = resp.json()
    codes = {r["code"] for r in body["rows"]}
    assert codes == {"000100", "005930"}


async def test_accumulation_screener_returns_empty_rows_when_no_data_in_window(monkeypatch):
    await _clear_test_rows()

    class _FixedDate(dt.date):
        @classmethod
        def today(cls):
            return TEST_DATE

    monkeypatch.setattr(flow_rank_module.dt, "date", _FixedDate)

    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/markets/accumulation-screener", params={"days": 1})

    assert resp.status_code == 200
    body = resp.json()
    assert body["rows"] == []
