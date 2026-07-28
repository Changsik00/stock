"""Unit tests for app.collectors.volume_profile_snapshot.collect (PLAN.md §5.35-3).

Real dev Postgres (docker-compose `db` service, must be running) — same
rationale as tests/test_stocks_router.py. `TARGET_DATE`(2026-01-15) is a fixed
date well before this feature's real verification run so it never collides
with rows this session's manual admin-trigger run writes for "today".

Watchlist membership is monkeypatched (``list_watchlist_codes``) instead of
seeding the real `watchlist` table, for the same reason as
tests/test_stock_ohlcv_watchlist_collector.py (shared, ever-growing
production state).
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import delete, select

from app.collectors import volume_profile_snapshot
from app.db import async_session_factory, engine
from app.models import Stock, StockOhlcv, VolumeProfileDaily
from app.quant.volume_profile import compute_volume_profile, detect_levels
from app.services import get_stock_series_from_db

TARGET_DATE = dt.date(2026, 1, 15)
TEST_CODES = ["999921", "999922", "999923"]


@pytest.fixture(autouse=True)
async def _dispose_engine_per_test():
    yield
    await engine.dispose()


async def _clear_rows() -> None:
    """entity_code(예: "kospi")는 이 테스트만의 것이 아니라 실서비스 배치가 매일
    같은 코드로 행을 쌓아가는 공유 식별자다 — date 필터 없이 지우면 이 테스트가
    돌 때마다 실제 누적 이력(오늘/과거 다른 날짜의 실서비스 스냅샷)까지 통째로
    삭제해 버린다(실측으로 확인: 이 필터 없이 pytest를 한 번 돌렸더니 실제
    volume_profile_daily의 index 행 3개가 전부 사라졌다). TARGET_DATE로 반드시
    scope해 이 테스트 스위트가 쓴 행만 지운다."""
    async with async_session_factory() as session:
        await session.execute(
            delete(VolumeProfileDaily).where(
                VolumeProfileDaily.entity_code.in_(list(volume_profile_snapshot.INDEX_ENTITIES) + TEST_CODES),
                VolumeProfileDaily.date == TARGET_DATE,
            )
        )
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


def _patch_watchlist(monkeypatch, codes: list[str]) -> None:
    async def fake_list_codes(_session):
        return list(codes)

    monkeypatch.setattr(volume_profile_snapshot, "list_watchlist_codes", fake_list_codes)


async def _seed_ohlcv(session, code: str, n_days: int, start: dt.date) -> None:
    """n_days개의 합성 일봉을 upsert한다 — 가격대가 점진적으로 오르되 중간 한
    구간(volume_spike_at)에 거래량 스파이크를 둬 뚜렷한 POC가 생기게 한다."""
    for i in range(n_days):
        d = start + dt.timedelta(days=i)
        base = 100 + i  # 가격대가 매일 조금씩 오름
        volume = 10_000 if i != n_days // 2 else 500_000  # 중간 날짜에 거래량 폭증
        session.add(
            StockOhlcv(
                code=code,
                date=d,
                open=base,
                high=base + 3,
                low=base - 3,
                close=base,
                volume=volume,
            )
        )
    await session.flush()


async def test_upserts_row_for_all_three_index_entities(monkeypatch):
    """지수 3종(kospi/kosdaq/futures) 각각 entity_type="index" 행이 하나씩
    생긴다 — watchlist는 비어 있다고 가정(코드 목록만 monkeypatch)."""
    _patch_watchlist(monkeypatch, [])

    async with async_session_factory() as session:
        rows_written = await volume_profile_snapshot.collect(session, TARGET_DATE)
        await session.commit()

    assert rows_written == 3

    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(VolumeProfileDaily).where(
                    VolumeProfileDaily.entity_type == "index",
                    VolumeProfileDaily.date == TARGET_DATE,
                )
            )
        ).scalars().all()

    await _clear_rows()

    assert {r.entity_code for r in rows} == set(volume_profile_snapshot.INDEX_ENTITIES)
    for r in rows:
        assert r.lookback_days == volume_profile_snapshot.PROFILE_LOOKBACK_DAYS


async def test_stock_with_zero_history_gets_row_with_empty_levels_not_skipped(
    monkeypatch, seeded_stocks
):
    """watchlist에 막 추가돼 stock_ohlcv가 텅 빈 종목도 행 자체는 생성된다
    (poc_price=None, levels=[], bar_count=0) — 스킵되지 않는다는 게 핵심
    (모듈 docstring "표본 부족 시에도 행을 적재한다" 참고)."""
    _patch_watchlist(monkeypatch, [TEST_CODES[0]])

    async with async_session_factory() as session:
        rows_written = await volume_profile_snapshot.collect(session, TARGET_DATE)
        await session.commit()

    assert rows_written == 3 + 1  # 지수 3 + 이 종목 1

    async with async_session_factory() as session:
        row = await session.get(
            VolumeProfileDaily, {"entity_type": "stock", "entity_code": TEST_CODES[0], "date": TARGET_DATE}
        )

    assert row is not None
    assert row.bar_count == 0
    assert row.poc_price is None
    assert row.levels == []


async def test_stock_with_thin_history_below_min_bars_has_levels_empty_but_poc_set(
    monkeypatch, seeded_stocks
):
    """MIN_BARS_FOR_LEVELS(20) 미만의 봉만 있으면 levels는 여전히 빈 리스트지만
    (표본 부족), bar_count>0이고 유효 거래량이 있으면 poc_price 자체는 계산된다
    (compute_volume_profile은 detect_levels보다 문턱이 낮다 — quant/
    volume_profile.py 모듈 docstring 참고)."""
    _patch_watchlist(monkeypatch, [TEST_CODES[0]])

    async with async_session_factory() as session:
        await _seed_ohlcv(session, TEST_CODES[0], n_days=5, start=TARGET_DATE - dt.timedelta(days=10))
        await session.commit()

    async with async_session_factory() as session:
        await volume_profile_snapshot.collect(session, TARGET_DATE)
        await session.commit()

    async with async_session_factory() as session:
        row = await session.get(
            VolumeProfileDaily, {"entity_type": "stock", "entity_code": TEST_CODES[0], "date": TARGET_DATE}
        )

    assert row is not None
    assert row.bar_count == 5
    assert row.levels == []  # 5 < MIN_BARS_FOR_LEVELS(20)
    assert row.poc_price is not None  # compute_volume_profile 자체는 문턱이 없음


async def test_values_match_direct_quant_volume_profile_call(monkeypatch, seeded_stocks):
    """저장된 스냅샷의 poc_price/bar_count/total_volume/levels가
    quant/volume_profile.py를 동일 입력으로 직접 호출한 결과와 정확히 일치한다
    (이 collector는 §5.34 온디맨드 계산과 완전히 같은 함수 호출을 재사용할 뿐
    이라는 모듈 docstring 주장의 실증)."""
    _patch_watchlist(monkeypatch, [TEST_CODES[0]])

    async with async_session_factory() as session:
        await _seed_ohlcv(session, TEST_CODES[0], n_days=30, start=TARGET_DATE - dt.timedelta(days=40))
        await session.commit()

    async with async_session_factory() as session:
        await volume_profile_snapshot.collect(session, TARGET_DATE)
        await session.commit()

    async with async_session_factory() as session:
        row = await session.get(
            VolumeProfileDaily, {"entity_type": "stock", "entity_code": TEST_CODES[0], "date": TARGET_DATE}
        )
        # 독립적으로 동일 입력을 다시 읽어 직접 계산 — collect()와 완전히 같은 두
        # 함수 호출(services.get_stock_series_from_db, quant.volume_profile)이다.
        series = await get_stock_series_from_db(
            session, TEST_CODES[0], volume_profile_snapshot.PROFILE_LOOKBACK_DAYS
        )
    expected_profile = compute_volume_profile(series)
    expected_levels = detect_levels(expected_profile)
    expected_poc_price = (
        expected_profile["poc"]["price_mid"] if expected_profile["poc"] is not None else None
    )

    assert row is not None
    assert row.bar_count == expected_profile["bar_count"] == 30
    assert row.levels != []  # 30 >= MIN_BARS_FOR_LEVELS(20) — 검출된 피크가 있어야 뜻이 있는 비교
    assert float(row.poc_price) == pytest.approx(expected_poc_price)
    assert float(row.total_volume) == pytest.approx(expected_profile["total_volume"])
    assert row.levels == expected_levels


async def test_running_twice_upserts_same_row_without_duplicating(monkeypatch, seeded_stocks):
    _patch_watchlist(monkeypatch, [TEST_CODES[0]])
    async with async_session_factory() as session:
        await _seed_ohlcv(session, TEST_CODES[0], n_days=25, start=TARGET_DATE - dt.timedelta(days=30))
        await session.commit()

    async with async_session_factory() as session:
        await volume_profile_snapshot.collect(session, TARGET_DATE)
        await session.commit()
    async with async_session_factory() as session:
        await volume_profile_snapshot.collect(session, TARGET_DATE)
        await session.commit()

    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(VolumeProfileDaily).where(
                    VolumeProfileDaily.entity_type == "stock",
                    VolumeProfileDaily.entity_code == TEST_CODES[0],
                    VolumeProfileDaily.date == TARGET_DATE,
                )
            )
        ).scalars().all()
    assert len(rows) == 1


async def test_one_stock_failure_does_not_block_others_or_indices(monkeypatch, seeded_stocks):
    """한 종목의 계산 경로에서 예외가 나도(get_stock_series_from_db 모킹으로
    흉내) 나머지 종목과 지수 3종은 정상 적재된다."""
    _patch_watchlist(monkeypatch, [TEST_CODES[0], TEST_CODES[1]])

    async with async_session_factory() as session:
        await _seed_ohlcv(session, TEST_CODES[1], n_days=5, start=TARGET_DATE - dt.timedelta(days=10))
        await session.commit()

    real_get_stock_series = volume_profile_snapshot.get_stock_series_from_db

    async def fake_get_stock_series(session, code, days):
        if code == TEST_CODES[0]:
            raise RuntimeError("stock_ohlcv read failed")
        return await real_get_stock_series(session, code, days)

    monkeypatch.setattr(volume_profile_snapshot, "get_stock_series_from_db", fake_get_stock_series)

    async with async_session_factory() as session:
        rows_written = await volume_profile_snapshot.collect(session, TARGET_DATE)
        await session.commit()

    assert rows_written == 3 + 1  # 지수 3 + TEST_CODES[1]만(TEST_CODES[0]은 실패)

    async with async_session_factory() as session:
        bad = await session.get(
            VolumeProfileDaily, {"entity_type": "stock", "entity_code": TEST_CODES[0], "date": TARGET_DATE}
        )
        good = await session.get(
            VolumeProfileDaily, {"entity_type": "stock", "entity_code": TEST_CODES[1], "date": TARGET_DATE}
        )
    assert bad is None
    assert good is not None
