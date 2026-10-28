"""Integration tests for app.collectors.etf_pair_sampler (PLAN.md §5.88-1).

Same house pattern as tests/test_positioning_snapshot.py: real dev Postgres via
app.db.async_session_factory, test rows dated 2099-01-05 (far outside real data)
cleaned up in teardown. KiwoomClient and naver_etf.fetch_etf_list are replaced
with fakes so the tests never touch the network.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from app.collectors import etf_pair_sampler
from app.db import async_session_factory, engine
from app.models import EtfPairSample

KST = dt.timezone(dt.timedelta(hours=9))
OPEN_TIME = dt.datetime(2099, 1, 5, 10, 0, 37, tzinfo=KST)  # 월요일 정규장 중, 초는 내림 대상
CLOSED_TIME = dt.datetime(2099, 1, 5, 15, 30, 0, tzinfo=KST)  # 정규장 마감 직후
DAY_START = dt.datetime(2099, 1, 5, 0, 0, tzinfo=KST)
DAY_END = dt.datetime(2099, 1, 6, 0, 0, tzinfo=KST)

QUOTES = {
    "000660": {"cur_prc": "+1484000", "base_pric": "1341000", "flu_rt": "+10.66"},
    "0193T0": {"cur_prc": "+7055", "base_pric": "5805", "flu_rt": "+21.53"},
    "0197X0": {"cur_prc": "-7290", "base_pric": "9275", "flu_rt": "-21.40"},
}


async def _clear() -> None:
    async with async_session_factory() as session:
        await session.execute(
            EtfPairSample.__table__.delete().where(
                EtfPairSample.time >= DAY_START, EtfPairSample.time < DAY_END
            )
        )
        await session.commit()


@pytest.fixture(autouse=True)
async def _clean():
    await _clear()
    yield
    await _clear()
    await engine.dispose()


def _install_fakes(monkeypatch, fail_codes=(), nav_error=False, calls=None):
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def stock_info(self, code):
            if calls is not None:
                calls.append(code)
            if code in fail_codes:
                raise RuntimeError("boom")
            return QUOTES[code]

    def fake_list():
        if nav_error:
            raise RuntimeError("naver down")
        return [
            {"code": "0193T0", "nav": 7040.0, "now_value": 7055},
            {"code": "0197X0", "nav": 7300.0, "now_value": 7290},
            {"code": "069500", "nav": 100.0, "now_value": 100},
        ]

    monkeypatch.setattr(etf_pair_sampler, "KiwoomClient", FakeClient)
    monkeypatch.setattr(etf_pair_sampler.naver_etf, "fetch_etf_list", fake_list)


async def _rows():
    async with async_session_factory() as session:
        stmt = select(EtfPairSample).where(
            EtfPairSample.time >= DAY_START, EtfPairSample.time < DAY_END
        )
        return {r.code: r for r in (await session.execute(stmt)).scalars().all()}


async def test_records_both_etfs(monkeypatch):
    _install_fakes(monkeypatch)
    async with async_session_factory() as session:
        n = await etf_pair_sampler.record_pair_sample(session, OPEN_TIME)
    assert n == 2
    rows = await _rows()
    assert set(rows) == {"0193T0", "0197X0"}
    lev = rows["0193T0"]
    assert lev.time == OPEN_TIME.replace(second=0)
    assert lev.multiple == 2
    assert float(lev.price) == 7055 and float(lev.base_price) == 5805
    assert float(lev.nav) == 7040
    assert float(lev.underlying_price) == 1484000
    assert float(lev.underlying_change_rate) == pytest.approx(10.66)
    assert float(lev.lag_pct) == pytest.approx(0.17, abs=0.01)
    assert float(lev.premium_pct) == pytest.approx((7055 - 7040) / 7040 * 100, abs=0.001)
    inv = rows["0197X0"]
    assert inv.multiple == -2 and float(inv.price) == 7290
    assert float(inv.lag_pct) == pytest.approx(-0.10, abs=0.01)


async def test_nav_failure_leaves_nav_null(monkeypatch):
    _install_fakes(monkeypatch, nav_error=True)
    async with async_session_factory() as session:
        assert await etf_pair_sampler.record_pair_sample(session, OPEN_TIME) == 2
    rows = await _rows()
    assert rows["0193T0"].nav is None and rows["0193T0"].premium_pct is None
    assert rows["0193T0"].lag_pct is not None


async def test_underlying_failure_writes_nothing(monkeypatch):
    _install_fakes(monkeypatch, fail_codes=("000660",))
    async with async_session_factory() as session:
        assert await etf_pair_sampler.record_pair_sample(session, OPEN_TIME) == 0
    assert await _rows() == {}


async def test_single_etf_failure_keeps_other(monkeypatch):
    _install_fakes(monkeypatch, fail_codes=("0197X0",))
    async with async_session_factory() as session:
        assert await etf_pair_sampler.record_pair_sample(session, OPEN_TIME) == 1
    assert set(await _rows()) == {"0193T0"}


async def test_market_closed_skips_kiwoom(monkeypatch):
    calls: list[str] = []
    _install_fakes(monkeypatch, calls=calls)
    async with async_session_factory() as session:
        assert await etf_pair_sampler.record_pair_sample(session, CLOSED_TIME) == 0
    assert calls == []
    assert await _rows() == {}


async def test_rerun_same_minute_no_duplicates(monkeypatch):
    _install_fakes(monkeypatch)
    async with async_session_factory() as session:
        await etf_pair_sampler.record_pair_sample(session, OPEN_TIME)
        await etf_pair_sampler.record_pair_sample(session, OPEN_TIME.replace(second=59))
    async with async_session_factory() as session:
        stmt = select(EtfPairSample).where(
            EtfPairSample.time >= DAY_START, EtfPairSample.time < DAY_END
        )
        assert len((await session.execute(stmt)).scalars().all()) == 2
