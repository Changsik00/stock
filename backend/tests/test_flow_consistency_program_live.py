"""Phase 5.123: /api/markets/flow/consistency 의 program — 장중 잠정(ka90010 오늘 행) vs DB 확정.

가짜로 바꾸는 것은 외부 소스(키움 KiwoomClient/_fetch_page)와 시계(kst_now)뿐이다.
엔드포인트 선택 로직과 DB 조회(_program_from_db)는 실제 코드·운영 DB 읽기 전용으로 돈다.
시간대: 평일 12:00(장중) / 평일 21:00(마감 후) / 일요일 12:00(휴장) / 20:04·20:05 경계.
"""
import datetime as dt

import pytest
import pytest_asyncio

from app.collectors import program_flow
from app.db import async_session_factory, engine
from app.market_hours import KST
from app.routers import markets as m

pytestmark = pytest.mark.asyncio

WED = dt.date(2026, 11, 25)  # 평일 거래일


def at(day, hh, mm=0):
    return dt.datetime.combine(day, dt.time(hh, mm), tzinfo=KST)


class FakeClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest_asyncio.fixture(autouse=True)
async def _env(monkeypatch):
    m._program_live_cache.clear()
    monkeypatch.setattr(m, "KiwoomClient", FakeClient)
    yield
    m._program_live_cache.clear()
    await engine.dispose()


def _fake_fetch(calls, rows_for=None, raises=False):
    async def fake(client, market, anchor):
        calls.append((market, anchor))
        if raises:
            raise RuntimeError("kiwoom down")
        if rows_for is not None:
            return rows_for(market, anchor), {}
        base = 1000 if market == "kospi" else 10
        return [{"date": anchor, "arb_net": base + 1, "nonarb_net": -base}], {}
    return fake


async def _program(monkeypatch, now):
    monkeypatch.setattr(m, "kst_now", lambda: now)
    async with async_session_factory() as session:
        return await m._consistency_program(session, now)


async def _db_expected(market):
    async with async_session_factory() as session:
        return await m._program_from_db(session, market)


async def test_midday_returns_provisional_today(monkeypatch):
    calls = []
    monkeypatch.setattr(program_flow, "_fetch_page", _fake_fetch(calls))
    prog = await _program(monkeypatch, at(WED, 12, 0))
    assert prog["kospi"] == {"date": "2026-11-25", "non_arb": -1000.0, "arb": 1001.0, "provisional": True}
    assert prog["kosdaq"]["non_arb"] == -10.0 and prog["kosdaq"]["provisional"] is True
    assert calls == [("kospi", WED), ("kosdaq", WED)]


async def test_after_2005_uses_db_without_fetch(monkeypatch):
    calls = []
    monkeypatch.setattr(program_flow, "_fetch_page", _fake_fetch(calls))
    prog = await _program(monkeypatch, at(WED, 21, 0))
    assert calls == []
    for market in ("kospi", "kosdaq"):
        assert prog[market] == await _db_expected(market)
        assert prog[market]["provisional"] is False
    assert prog["kospi"]["date"] is not None  # 실제 DB 행


async def test_window_boundaries_and_holiday(monkeypatch):
    calls = []
    monkeypatch.setattr(program_flow, "_fetch_page", _fake_fetch(calls))
    assert (await _program(monkeypatch, at(WED, 20, 4)))["kospi"]["provisional"] is True
    m._program_live_cache.clear()
    assert (await _program(monkeypatch, at(WED, 20, 5)))["kospi"]["provisional"] is False
    assert (await _program(monkeypatch, at(WED, 7, 59)))["kospi"]["provisional"] is False
    assert (await _program(monkeypatch, at(dt.date(2026, 11, 29), 12, 0)))["kospi"]["provisional"] is False  # 일요일
    assert len(calls) == 2  # 20:04의 코스피·코스닥만


async def test_fetch_error_falls_back_to_db(monkeypatch):
    calls = []
    monkeypatch.setattr(program_flow, "_fetch_page", _fake_fetch(calls, raises=True))
    prog = await _program(monkeypatch, at(WED, 12, 0))
    assert calls  # 시도는 했다
    for market in ("kospi", "kosdaq"):
        assert prog[market] == await _db_expected(market)
        assert prog[market]["provisional"] is False


async def test_no_today_row_falls_back_to_db(monkeypatch):
    """원천 첫 행이 어제면(장 시작 직후 등) 오늘 값으로 보지 않는다."""
    calls = []
    yday = WED - dt.timedelta(days=1)
    monkeypatch.setattr(
        program_flow, "_fetch_page",
        _fake_fetch(calls, rows_for=lambda mk, a: [{"date": yday, "arb_net": 1, "nonarb_net": 2}]),
    )
    prog = await _program(monkeypatch, at(WED, 8, 30))
    assert prog["kospi"] == await _db_expected("kospi")
    assert prog["kospi"]["provisional"] is False


async def test_cache_prevents_second_fetch_within_60s(monkeypatch):
    calls = []
    monkeypatch.setattr(program_flow, "_fetch_page", _fake_fetch(calls))
    clock = {"t": 1000.0}
    monkeypatch.setattr(m.time, "monotonic", lambda: clock["t"])
    await _program(monkeypatch, at(WED, 12, 0))
    assert len(calls) == 2
    clock["t"] += 30
    await _program(monkeypatch, at(WED, 12, 0, ))
    assert len(calls) == 2  # 캐시 적중
    clock["t"] += 31
    await _program(monkeypatch, at(WED, 12, 2))
    assert len(calls) == 4  # TTL 경과 후 재조회
