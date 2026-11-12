"""NXT 5분봉 + KRX 종가 적재기 단위테스트 (PLAN.md §5.107). 파서는 순수, DB는 롤백 트랜잭션(코드 TST*)."""

from __future__ import annotations

import datetime as dt

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors import nxt_minute_bars as nx
from app.collectors.market_minute_bars import _upsert
from app.db import engine
from app.models import KrxRegularClose, NxtIntradayBar


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_per_test():
    """테스트마다 이벤트 루프가 달라 커넥션 풀을 setup/teardown 양쪽에서 비운다(기존 테스트 관례)."""
    await engine.dispose()
    yield
    await engine.dispose()


def _row(tm, o, c, vol="10"):
    return {"cntr_tm": tm, "open_pric": o, "high_pric": c, "low_pric": o, "cur_prc": c, "trde_qty": vol}


def test_parse_nxt_rows_strips_sign_and_filters_session():
    data = {
        "stk_min_pole_chart_qry": [
            _row("20261111195500", "+208000", "-207500"),
            _row("20261111080000", "-195500", "+196000"),
            _row("20261111075500", "+1", "+1"),  # NXT 시간 밖
            _row("20261111200000", "+1", "+1"),  # 20:00 이후
            _row("20261111090000", "", ""),  # 시가 없음
        ]
    }
    rows = nx.parse_nxt_rows("TSTA", data)
    assert sorted(r["time"] for r in rows) == ["0800", "1955"]
    r = next(r for r in rows if r["time"] == "1955")
    assert r["open"] == 208000 and r["close"] == 207500 and r["date"] == dt.date(2026, 11, 11)


def test_parse_krx_close_rows_takes_1530_bar():
    data = {
        "stk_min_pole_chart_qry": [
            _row("20261111153000", "-206000", "+206000"),
            _row("20261111153500", "+206000", "+206000"),
            _row("20261110153000", "+198900", "-198900"),
            _row("20261111195500", "+208000", "+208000"),
        ]
    }
    rows = nx.parse_krx_close_rows("TSTA", data)
    assert {(r["date"], r["close"]) for r in rows} == {(dt.date(2026, 11, 11), 206000), (dt.date(2026, 11, 10), 198900)}


def test_plan_pages():
    t = dt.date(2026, 11, 12)  # 목, 직전 개장일 11-11
    assert nx.plan_pages(None, t) == (nx.BACKFILL_MAX_PAGES, nx.BACKFILL_FLOOR)
    assert nx.plan_pages(dt.date(2026, 11, 11), t) == (1, None)
    assert nx.plan_pages(dt.date(2026, 11, 12), t) == (1, None)
    assert nx.plan_pages(dt.date(2026, 11, 5), t) == (nx.BACKFILL_MAX_PAGES, dt.date(2026, 11, 5))
    assert nx.BACKFILL_MAX_PAGES == 12 and nx.BACKFILL_FLOOR == dt.date(2026, 8, 26)
    # 월요일 대상이면 직전 개장일은 금요일
    assert nx.plan_pages(dt.date(2026, 11, 13), dt.date(2026, 11, 16)) == (1, None)


@pytest_asyncio.fixture
async def rb_session():
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()


async def test_upsert_idempotent(rb_session):
    data = {"stk_min_pole_chart_qry": [_row("20261111080000", "+100", "+101"), _row("20261111080500", "+101", "+102")]}
    rows = nx.parse_nxt_rows("TSTA", data)
    await _upsert(rb_session, NxtIntradayBar, rows)
    rows[0]["close"] = 999
    await _upsert(rb_session, NxtIntradayBar, rows)
    cnt = (await rb_session.execute(select(func.count()).select_from(NxtIntradayBar).where(NxtIntradayBar.code == "TSTA"))).scalar_one()
    c = (await rb_session.execute(select(NxtIntradayBar.close).where(NxtIntradayBar.code == "TSTA", NxtIntradayBar.time == "0800"))).scalar_one()
    assert cnt == 2 and c == 999


class _FakeClient:
    """코드별 응답 맵: {stk_cd: [page, ...]} — page는 (data, headers) 혹은 예외."""

    pages: dict = {}
    calls: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def stock_minute_chart(self, code, tic_scope, upd_stkpc_tp="1", cont_yn=None, next_key=None):
        type(self).calls.append((code, tic_scope, cont_yn, next_key))
        idx = int(next_key or 0)
        page = type(self).pages[code][idx]
        if isinstance(page, Exception):
            raise page
        data, more = page
        return data, {"cont-yn": "Y" if more else "N", "next-key": str(idx + 1) if more else ""}


def _page(day: str):
    return {"stk_min_pole_chart_qry": [_row(f"{day}080000", "+100", "+101"), _row(f"{day}153000", "+100", "+101")]}


async def test_collect_partial_failure_isolated_and_all_failure_raises(rb_session, monkeypatch):
    monkeypatch.setattr(nx, "KiwoomClient", _FakeClient)
    monkeypatch.setattr(nx, "OUTLOOK_CODES", ("TSTA", "TSTB"))
    boom = RuntimeError("키움 실패")
    _FakeClient.calls = []
    _FakeClient.pages = {
        "TSTA_NX": [(_page("20261111"), False)],
        "TSTA": [(_page("20261111"), False)],
        "TSTB_NX": [boom],
        "TSTB": [boom],
    }
    # 테이블이 비어 있으므로 백필 모드 — 연속키가 없으면 1페이지로 끝난다
    total, msg = await nx.collect_nxt_minute_bars(rb_session, dt.date(2026, 11, 12))
    assert total == 3 and "TSTB" in msg  # TSTA: NXT 2행(08:00·15:30) + KRX 종가 1행
    n = (await rb_session.execute(select(func.count()).select_from(NxtIntradayBar).where(NxtIntradayBar.code == "TSTA"))).scalar_one()
    k = (await rb_session.execute(select(KrxRegularClose.close).where(KrxRegularClose.code == "TSTA"))).scalar_one()
    assert n == 2 and k == 101
    # 멱등: 한 번 더 돌려도 행 수 불변
    await nx.collect_nxt_minute_bars(rb_session, dt.date(2026, 11, 12))
    n2 = (await rb_session.execute(select(func.count()).select_from(NxtIntradayBar).where(NxtIntradayBar.code == "TSTA"))).scalar_one()
    assert n2 == 2
    # 전부 실패하면 예외 -> run_job이 fail로 기록
    _FakeClient.pages = {k: [boom] for k in ("TSTA_NX", "TSTA", "TSTB_NX", "TSTB")}
    with pytest.raises(RuntimeError):
        await nx.collect_nxt_minute_bars(rb_session, dt.date(2026, 11, 12))


async def test_collect_backfill_paginates_until_floor(rb_session, monkeypatch):
    monkeypatch.setattr(nx, "KiwoomClient", _FakeClient)
    monkeypatch.setattr(nx, "OUTLOOK_CODES", ("TSTA",))
    _FakeClient.calls = []
    pages = [(_page("20261111"), True), (_page("20261105"), True), (_page("20260825"), True), (_page("20260801"), False)]
    _FakeClient.pages = {"TSTA_NX": pages, "TSTA": pages}
    await nx.collect_nxt_minute_bars(rb_session, dt.date(2026, 11, 12))
    nx_calls = [c for c in _FakeClient.calls if c[0] == "TSTA_NX"]
    assert len(nx_calls) == 3  # 3번째 페이지가 2026-08-26 이하라 거기서 멈춘다
    assert all(c[1] == "5" for c in _FakeClient.calls)
