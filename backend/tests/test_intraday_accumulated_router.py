"""Integration tests for the three 1D 누적 조회 endpoints (PLAN.md §5.4-3, §5.10,
§5.13, §5.14): GET /api/markets/flow/intraday-accumulated,
GET /api/markets/foreign-position/intraday-accumulated,
GET /api/markets/breadth/intraday-accumulated (app.routers.markets).

Same httpx ASGITransport-against-the-real-app house style as
test_markets_flow_live_router.py/test_futures_flow_live_router.py. §5.14 moved
these three routes off the old in-memory buffer onto ``intraday_sample`` (real
dev Postgres) — tests now seed rows directly via
app.collectors.intraday_snapshot's record_* functions (same DB session pattern
as tests/test_intraday_snapshot.py) and assert the HTTP response mirrors what
get_flow_series()/get_foreign_position_series()/get_breadth_series() return.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.collectors import intraday_snapshot as snap
from app.db import async_session_factory, engine
from app.main import app
from app.models import IntradaySample

KST = dt.timezone(dt.timedelta(hours=9))
TEST_DAY = dt.date(2099, 1, 5)  # 실 데이터와 겹치지 않는 먼 미래, 월요일(장중 검증용)


def _kst(hour, minute):
    return dt.datetime(TEST_DAY.year, TEST_DAY.month, TEST_DAY.day, hour, minute, tzinfo=KST)


def _cleanup_floor():
    # 며칠 전 날짜(days>1 창 테스트가 TEST_DAY-1에 행을 쓴다)까지 넉넉히 덮어야
    # 그 행도 다음 테스트로 새지 않고 지워진다.
    return _kst(0, 0) - dt.timedelta(days=3)


async def _clear_test_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(IntradaySample.__table__.delete().where(IntradaySample.time >= _cleanup_floor()))
        await session.commit()


@pytest.fixture(autouse=True)
async def _clean_intraday_sample():
    await _clear_test_rows()
    yield
    await _clear_test_rows()
    await engine.dispose()


async def test_flow_intraday_accumulated_returns_rows_split_by_market(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    payload = {
        "kospi": {"investors": {"개인": {"net_value": 100}, "외국인": {"net_value": 100}, "기관계": {"net_value": -50}}},
        "kosdaq": {"investors": {"개인": {"net_value": 20}, "외국인": {"net_value": 10}, "기관계": {"net_value": -5}}},
        "market_closed": False,
    }
    async with async_session_factory() as session:
        await snap.record_flow_snapshot(session, payload)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/flow/intraday-accumulated")

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == TEST_DAY.isoformat()
    assert body["series"]["kospi"]["개인"] == [{"time": "10:00", "value": 100.0}]
    assert body["series"]["kospi"]["외국인"] == [{"time": "10:00", "value": 100.0}]
    assert body["series"]["kospi"]["기관계"] == [{"time": "10:00", "value": -50.0}]
    assert body["series"]["kosdaq"]["개인"] == [{"time": "10:00", "value": 20.0}]
    assert body["series"]["kosdaq"]["외국인"] == [{"time": "10:00", "value": 10.0}]
    assert body["series"]["kosdaq"]["기관계"] == [{"time": "10:00", "value": -5.0}]
    assert isinstance(body["market_closed"], bool)


async def test_flow_intraday_accumulated_empty_returns_empty_lists(monkeypatch):
    # 2026-07-23 수정 — 장중에는 실제 worker의 60초 잡이 오늘 날짜에 진짜 행을
    # 계속 써서(§5.14가 의도한 정상 동작) "오늘"을 그대로 조회하면 더 이상
    # 비어있지 않다. 이 테스트는 "행이 하나도 없을 때"를 검증하려는 것이므로,
    # 실 데이터와 절대 겹치지 않는 TEST_DAY(2099년)로 "지금"을 고정한다.
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/flow/intraday-accumulated")

    assert resp.status_code == 200
    body = resp.json()
    assert body["series"] == {
        "kospi": {"개인": [], "외국인": [], "기관계": []},
        "kosdaq": {"개인": [], "외국인": [], "기관계": []},
    }
    assert "date" in body
    assert "market_closed" in body


async def test_flow_intraday_accumulated_days_query_param_extends_window(monkeypatch):
    # 하루 전(days=1 창 밖) 찍힌 점은 기본(days=1)으로는 안 보이고 days=2를 주면
    # 보여야 한다 — §5.14 핵심 신규 기능(과거 조회).
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(23, 0) - dt.timedelta(days=1))
    payload = {"kospi": {"investors": {"개인": {"net_value": 42}}}, "kosdaq": None, "market_closed": False}
    async with async_session_factory() as session:
        await snap.record_flow_snapshot(session, payload)
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(9, 0))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp_1d = await client.get("/api/markets/flow/intraday-accumulated", params={"days": 1})
        resp_2d = await client.get("/api/markets/flow/intraday-accumulated", params={"days": 2})

    assert resp_1d.json()["series"]["kospi"]["개인"] == []
    assert len(resp_2d.json()["series"]["kospi"]["개인"]) == 1
    assert resp_2d.json()["series"]["kospi"]["개인"][0]["time"] == "01/04 23:00"


async def test_flow_intraday_accumulated_days_out_of_range_returns_422():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp_low = await client.get("/api/markets/flow/intraday-accumulated", params={"days": 0})
        resp_high = await client.get("/api/markets/flow/intraday-accumulated", params={"days": 31})

    assert resp_low.status_code == 422
    assert resp_high.status_code == 422


async def test_foreign_position_intraday_accumulated_returns_summed_spot_and_futures(monkeypatch):
    # 회귀 테스트(§5.10): kospi/kosdaq이 분리 저장된 뒤에도 "외인 양손" 모달의
    # spot은 여전히 두 시장 합산값이어야 한다.
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    flow_payload = {
        "kospi": {"investors": {"외국인": {"net_value": 100}}},
        "kosdaq": {"investors": {"외국인": {"net_value": 10}}},
        "market_closed": False,
    }
    futures_payload = {"investors": {"외국인": {"net_value": 456}}, "market_closed": False}
    async with async_session_factory() as session:
        await snap.record_flow_snapshot(session, flow_payload)
        await snap.record_futures_flow_snapshot(session, futures_payload)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/foreign-position/intraday-accumulated")

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == TEST_DAY.isoformat()
    assert body["spot"] == [{"time": "10:00", "value": 110.0}]
    assert body["futures"] == [{"time": "10:00", "value": 456.0}]


async def test_foreign_position_intraday_accumulated_empty_returns_empty_lists(monkeypatch):
    # 위와 동일한 이유(장중 실데이터와 안 겹치게 TEST_DAY로 "지금" 고정).
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/foreign-position/intraday-accumulated")

    assert resp.status_code == 200
    body = resp.json()
    assert body["spot"] == []
    assert body["futures"] == []


async def test_breadth_intraday_accumulated_returns_written_points(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    async with async_session_factory() as session:
        await snap.record_breadth_snapshot(
            session,
            {"kospi": {"adv": 600, "dec": 400, "flat": 0}, "kosdaq": {"adv": 400, "dec": 600, "flat": 0}, "market_closed": False},
        )
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 1))
    async with async_session_factory() as session:
        await snap.record_breadth_snapshot(
            session,
            {"kospi": {"adv": 750, "dec": 250, "flat": 0}, "kosdaq": {"adv": 250, "dec": 750, "flat": 0}, "market_closed": False},
        )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/breadth/intraday-accumulated")

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == TEST_DAY.isoformat()
    assert body["series"] == [
        {"time": "10:00", "value": 50.0},
        {"time": "10:01", "value": 50.0},
    ]


async def test_breadth_intraday_accumulated_empty_returns_empty_list(monkeypatch):
    # 위와 동일한 이유(장중 실데이터와 안 겹치게 TEST_DAY로 "지금" 고정).
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/breadth/intraday-accumulated")

    assert resp.status_code == 200
    body = resp.json()
    assert body["series"] == []
    assert "date" in body
    assert "market_closed" in body


async def test_intraday_sample_rows_survive_a_fresh_query_after_write(monkeypatch):
    """§5.14의 핵심 요구사항(재배포에도 데이터가 살아남아야 한다)을 라우터
    레벨에서도 확인한다 — 실제 프로세스 재시작은 못 흉내내지만, 쓰기에 쓰인
    세션과 완전히 무관한 새 세션/새 HTTP 요청으로 다시 읽어도 데이터가 그대로
    보여야 한다는 것으로 "메모리 휘발이 아니라 DB에 실제로 남았는지"를
    검증한다."""
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(11, 30))
    async with async_session_factory() as session:
        await snap.record_futures_flow_snapshot(session, {"investors": {"외국인": {"net_value": 789}}, "market_closed": False})

    # 별도 세션으로 직접 DB를 읽어 행이 실제로 커밋됐는지 먼저 확인. 장중에는
    # 실제 worker가 같은 series_key로 계속 실데이터를 쓰고 있으니(§5.14 정상
    # 동작), TEST_DAY 하루로 범위를 좁혀야 이번에 쓴 행만 걸린다.
    day_start = _kst(0, 0)
    day_end = day_start + dt.timedelta(days=1)
    async with async_session_factory() as verify_session:
        rows = (
            await verify_session.execute(
                select(IntradaySample).where(
                    IntradaySample.series_key == "futures_외국인",
                    IntradaySample.time >= day_start,
                    IntradaySample.time < day_end,
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert float(rows[0].value) == 789

    # 그 다음 HTTP 경로로도 동일한 값이 보이는지 확인(완전히 새 요청 스코프 세션).
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/foreign-position/intraday-accumulated")

    assert resp.json()["futures"] == [{"time": "11:30", "value": 789.0}]
