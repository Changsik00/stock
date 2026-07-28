"""Unit tests for app.collectors.stock_ohlcv_watchlist.collect (PLAN.md §5.35-2).

Real dev Postgres (docker-compose `db` service, must be running) — same
rationale as tests/test_stocks_router.py: stock_ohlcv.code has a FK to
stocks.code, and this collector's core behavior (backfill-size decision,
upsert) is the same SQL path routers/stocks.py exercises.

Watchlist membership is monkeypatched (``list_watchlist_codes``) instead of
seeding the real `watchlist` table — that table is shared, ever-growing
production state (a manual admin-trigger run earlier in this same
verification session may have already populated hundreds of real codes into
it), and this collector doesn't care how the code list was obtained. The
external network call (``naver_index.fetch_stock_series``) is always
monkeypatched — no real network traffic.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import delete, select

from app import services
from app.collectors import stock_ohlcv_watchlist
from app.db import async_session_factory, engine
from app.models import Stock, StockOhlcv

TEST_CODES = ["999911", "999912"]
TARGET_DATE = dt.date(2026, 7, 27)


@pytest.fixture(autouse=True)
async def _dispose_engine_per_test():
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """REQUEST_DELAY_SECONDS(0.2초)만큼의 실제 대기 없이 테스트가 빠르게 끝나도록
    asyncio.sleep을 no-op으로 바꾼다 — 지연 자체(호출 여부)는 별도로 검증하지
    않고, "실패 격리"/"백필 크기 산정" 로직만 검증하는 이 파일에선 불필요한
    실시간 대기다."""

    async def _fake_sleep(_seconds):
        return None

    monkeypatch.setattr(stock_ohlcv_watchlist.asyncio, "sleep", _fake_sleep)


async def _clear_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(delete(StockOhlcv).where(StockOhlcv.code.in_(TEST_CODES)))
        await session.execute(delete(Stock).where(Stock.code.in_(TEST_CODES)))
        await session.commit()


@pytest.fixture
async def seeded_stocks():
    await _clear_rows()
    async with async_session_factory() as session:
        for code in TEST_CODES:
            session.add(Stock(code=code, name=f"테스트{code}", market="KOSPI", is_etf=False))
        await session.commit()
    yield
    await _clear_rows()


def _fake_series(start: dt.date, end: dt.date) -> list[dict]:
    out = []
    d = start
    while d <= end:
        out.append(
            {"date": d, "open": 100, "high": 110, "low": 90, "close": 105, "volume": 1000}
        )
        d += dt.timedelta(days=1)
    return out


def _patch_watchlist(monkeypatch, codes: list[str]) -> None:
    async def fake_list_codes(_session):
        return list(codes)

    monkeypatch.setattr(stock_ohlcv_watchlist, "list_watchlist_codes", fake_list_codes)


async def test_new_code_triggers_full_backfill(monkeypatch, seeded_stocks):
    """stock_ohlcv에 행이 하나도 없는 코드는 BACKFILL_DAYS(180거래일)를 달력일로
    환산한 전체 구간을 요청한다(services.plan_stock_ohlcv_fetch_start와 동일한
    산식 — routers/stocks.py 온디맨드 경로의 신규 종목 백필과 정확히 같은 계산)."""
    calls = []

    def fake_fetch(code, start, end, timeout=15):
        calls.append((code, start, end))
        return _fake_series(start, end)

    monkeypatch.setattr(stock_ohlcv_watchlist.naver_index, "fetch_stock_series", fake_fetch)
    _patch_watchlist(monkeypatch, [TEST_CODES[0]])

    async with async_session_factory() as session:
        total = await stock_ohlcv_watchlist.collect(session, TARGET_DATE)
        await session.commit()

    assert len(calls) == 1
    code, start, end = calls[0]
    assert code == TEST_CODES[0]
    assert end == TARGET_DATE
    expected_calendar_days = (
        int(stock_ohlcv_watchlist.BACKFILL_DAYS * services.CANDLE_CALENDAR_BUFFER_RATIO)
        + services.CANDLE_CALENDAR_BUFFER_MIN_DAYS
    )
    assert start == TARGET_DATE - dt.timedelta(days=expected_calendar_days)
    assert total > 0

    async with async_session_factory() as session:
        rows = (
            await session.execute(select(StockOhlcv).where(StockOhlcv.code == TEST_CODES[0]))
        ).scalars().all()
    assert len(rows) > 0


async def test_existing_code_triggers_incremental_top_up(monkeypatch, seeded_stocks):
    """이미 stock_ohlcv에 캐시된 코드는 마지막 저장일부터 target_date까지만
    증분 요청한다(전체 백필과 달리 매우 짧은 구간)."""
    existing_max = TARGET_DATE - dt.timedelta(days=5)
    async with async_session_factory() as session:
        session.add(
            StockOhlcv(
                code=TEST_CODES[0], date=existing_max, open=1, high=1, low=1, close=1, volume=1
            )
        )
        await session.commit()

    calls = []

    def fake_fetch(code, start, end, timeout=15):
        calls.append((code, start, end))
        return _fake_series(start, end)

    monkeypatch.setattr(stock_ohlcv_watchlist.naver_index, "fetch_stock_series", fake_fetch)
    _patch_watchlist(monkeypatch, [TEST_CODES[0]])

    async with async_session_factory() as session:
        await stock_ohlcv_watchlist.collect(session, TARGET_DATE)
        await session.commit()

    assert len(calls) == 1
    _, start, end = calls[0]
    assert start == existing_max
    assert end == TARGET_DATE


async def test_code_already_up_to_date_skips_external_call(monkeypatch, seeded_stocks):
    """existing_max가 이미 target_date 이상이면 외부 호출 자체를 하지 않는다
    (예: 사용자가 오늘 이미 온디맨드로 열어본 종목)."""
    async with async_session_factory() as session:
        session.add(
            StockOhlcv(
                code=TEST_CODES[0], date=TARGET_DATE, open=1, high=1, low=1, close=1, volume=1
            )
        )
        await session.commit()

    calls = []

    def fake_fetch(code, start, end, timeout=15):
        calls.append((code, start, end))
        return []

    monkeypatch.setattr(stock_ohlcv_watchlist.naver_index, "fetch_stock_series", fake_fetch)
    _patch_watchlist(monkeypatch, [TEST_CODES[0]])

    async with async_session_factory() as session:
        total = await stock_ohlcv_watchlist.collect(session, TARGET_DATE)
        await session.commit()

    assert calls == []
    assert total == 0


async def test_one_code_failure_does_not_block_others(monkeypatch, seeded_stocks):
    """한 종목의 fetch가 예외를 던져도(상장폐지/네트워크 오류 등 흉내) 나머지
    종목은 정상적으로 upsert되고, collect() 자체는 예외를 전파하지 않는다
    (collectors/short_selling_market.py와 동일한 시장/종목별 격리 원칙)."""

    def fake_fetch(code, start, end, timeout=15):
        if code == TEST_CODES[0]:
            raise RuntimeError("naver unavailable")
        return _fake_series(start, end)

    monkeypatch.setattr(stock_ohlcv_watchlist.naver_index, "fetch_stock_series", fake_fetch)
    _patch_watchlist(monkeypatch, [TEST_CODES[0], TEST_CODES[1]])

    async with async_session_factory() as session:
        total = await stock_ohlcv_watchlist.collect(session, TARGET_DATE)
        await session.commit()

    assert total > 0  # TEST_CODES[1]은 정상 적재됨

    async with async_session_factory() as session:
        rows0 = (
            await session.execute(select(StockOhlcv).where(StockOhlcv.code == TEST_CODES[0]))
        ).scalars().all()
        rows1 = (
            await session.execute(select(StockOhlcv).where(StockOhlcv.code == TEST_CODES[1]))
        ).scalars().all()
    assert rows0 == []
    assert len(rows1) > 0
