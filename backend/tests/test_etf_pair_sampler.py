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
    "233740": {"cur_prc": "+5460", "base_pric": "5095", "flu_rt": "+7.16"},
    "251340": {"cur_prc": "-2595", "base_pric": "2690", "flu_rt": "-3.53"},
}
SECTOR = {"return_code": 0, "cur_prc": "+1283.02", "flu_rt": "+3.63", "inds_cur_prc_tm": [{"x": 1}]}


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


def _install_fakes(monkeypatch, fail_codes=(), nav_error=False, calls=None, sector_error=False):
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def sector_current_price(self, inds_cd, *args, **kwargs):
            if calls is not None:
                calls.append(inds_cd)
            if sector_error:
                raise RuntimeError("boom")
            return SECTOR, {}

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
            {"code": "233740", "nav": 5450.0, "now_value": 5460},
            {"code": "251340", "nav": 2600.0, "now_value": 2595},
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
    assert set(rows) == {"233740", "251340"}
    lev = rows["233740"]
    assert lev.time == OPEN_TIME.replace(second=0)
    assert lev.multiple == 2
    assert float(lev.price) == 5460 and float(lev.base_price) == 5095
    assert float(lev.nav) == 5450
    assert float(lev.underlying_price) == pytest.approx(1283.02)
    assert float(lev.underlying_change_rate) == pytest.approx(3.63)
    assert float(lev.theory_price) == pytest.approx(5464.9, abs=0.1)
    assert float(lev.lag_pct) == pytest.approx(-0.09, abs=0.01)
    assert float(lev.premium_pct) == pytest.approx((5460 - 5450) / 5450 * 100, abs=0.001)
    inv = rows["251340"]
    assert inv.multiple == -1 and float(inv.price) == 2595
    assert float(inv.theory_price) == pytest.approx(2592.4, abs=0.1)
    assert float(inv.lag_pct) == pytest.approx(0.10, abs=0.01)


async def test_nav_failure_leaves_nav_null(monkeypatch):
    _install_fakes(monkeypatch, nav_error=True)
    async with async_session_factory() as session:
        assert await etf_pair_sampler.record_pair_sample(session, OPEN_TIME) == 2
    rows = await _rows()
    assert rows["233740"].nav is None and rows["233740"].premium_pct is None
    assert rows["233740"].lag_pct is not None


async def test_underlying_failure_writes_nothing(monkeypatch):
    _install_fakes(monkeypatch, sector_error=True)
    async with async_session_factory() as session:
        assert await etf_pair_sampler.record_pair_sample(session, OPEN_TIME) == 0
    assert await _rows() == {}


async def test_single_etf_failure_keeps_other(monkeypatch):
    _install_fakes(monkeypatch, fail_codes=("251340",))
    async with async_session_factory() as session:
        assert await etf_pair_sampler.record_pair_sample(session, OPEN_TIME) == 1
    assert set(await _rows()) == {"233740"}


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
