"""Unit/integration tests for app.collectors.intraday_compaction (PLAN.md §5.14
다운샘플링 배치) — 최근 7일은 원본 그대로, 8일 전부터는 15분 버킷 평균으로 압축.

Same house pattern as tests/test_intraday_snapshot.py: real dev Postgres via
app.db.async_session_factory, rows isolated with a "test_compact_"-prefixed
series_key and a far-future TEST_DAY that never collides with real data,
cleaned up in teardown."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.collectors.base import REGISTRY
from app.collectors.intraday_compaction import (
    BUCKET_SECONDS,
    COMPACT_AFTER_DAYS,
    compact_intraday_samples,
)
from app.db import async_session_factory, engine
from app.models import IntradaySample

KST = dt.timezone(dt.timedelta(hours=9))
TEST_DAY = dt.date(2099, 1, 5)
SERIES_KEY = "test_compact_series"
OTHER_SERIES_KEY = "test_compact_series_2"


def _kst(day: dt.date, hour: int, minute: int, second: int = 0):
    return dt.datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=KST)


async def _clear_test_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(
            IntradaySample.__table__.delete().where(
                IntradaySample.series_key.in_([SERIES_KEY, OTHER_SERIES_KEY])
            )
        )
        await session.commit()


@pytest.fixture(autouse=True)
async def _clean_intraday_sample():
    await _clear_test_rows()
    yield
    await _clear_test_rows()
    await engine.dispose()


async def _seed(rows: list[dict]) -> None:
    async with async_session_factory() as session:
        stmt = pg_insert(IntradaySample).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=[IntradaySample.series_key, IntradaySample.time])
        await session.execute(stmt)
        await session.commit()


async def _rows_for(series_key: str) -> list[IntradaySample]:
    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(IntradaySample).where(IntradaySample.series_key == series_key).order_by(IntradaySample.time)
            )
        ).scalars().all()
        return rows


def test_registered_in_registry():
    assert REGISTRY.get("intraday_compaction") is compact_intraday_samples


async def test_compacts_old_bucket_into_average_and_deletes_originals():
    old_day = TEST_DAY - dt.timedelta(days=COMPACT_AFTER_DAYS + 2)
    await _seed(
        [
            {"series_key": SERIES_KEY, "time": _kst(old_day, 10, 0), "value": 100.0, "resolution_seconds": 0},
            {"series_key": SERIES_KEY, "time": _kst(old_day, 10, 3), "value": 200.0, "resolution_seconds": 0},
            {"series_key": SERIES_KEY, "time": _kst(old_day, 10, 7), "value": 300.0, "resolution_seconds": 0},
            {"series_key": SERIES_KEY, "time": _kst(old_day, 10, 12), "value": 400.0, "resolution_seconds": 0},
        ]
    )

    async with async_session_factory() as session:
        rows_written = await compact_intraday_samples(session, TEST_DAY)
        await session.commit()

    assert rows_written == 1

    rows = await _rows_for(SERIES_KEY)
    assert len(rows) == 1
    assert rows[0].time == _kst(old_day, 10, 0)
    assert float(rows[0].value) == pytest.approx(250.0)  # (100+200+300+400)/4
    assert rows[0].resolution_seconds == BUCKET_SECONDS


async def test_splits_across_15_minute_bucket_boundary():
    old_day = TEST_DAY - dt.timedelta(days=COMPACT_AFTER_DAYS + 2)
    await _seed(
        [
            {"series_key": SERIES_KEY, "time": _kst(old_day, 10, 14), "value": 111.0, "resolution_seconds": 0},
            {"series_key": SERIES_KEY, "time": _kst(old_day, 10, 15), "value": 999.0, "resolution_seconds": 0},
        ]
    )

    async with async_session_factory() as session:
        rows_written = await compact_intraday_samples(session, TEST_DAY)
        await session.commit()

    assert rows_written == 2

    rows = await _rows_for(SERIES_KEY)
    by_time = {r.time: float(r.value) for r in rows}
    assert by_time[_kst(old_day, 10, 0)] == pytest.approx(111.0)
    assert by_time[_kst(old_day, 10, 15)] == pytest.approx(999.0)


async def test_does_not_touch_rows_within_retention_window():
    # 정확히 경계 안(COMPACT_AFTER_DAYS - 1일 전, 즉 아직 원본 보관 기간)의 행은
    # 건드리지 않는다.
    recent_day = TEST_DAY - dt.timedelta(days=COMPACT_AFTER_DAYS - 1)
    await _seed(
        [{"series_key": SERIES_KEY, "time": _kst(recent_day, 10, 0), "value": 42.0, "resolution_seconds": 0}]
    )

    async with async_session_factory() as session:
        rows_written = await compact_intraday_samples(session, TEST_DAY)
        await session.commit()

    assert rows_written == 0

    rows = await _rows_for(SERIES_KEY)
    assert len(rows) == 1
    assert rows[0].resolution_seconds == 0
    assert float(rows[0].value) == 42.0


async def test_already_compacted_rows_are_left_alone_on_rerun():
    old_day = TEST_DAY - dt.timedelta(days=COMPACT_AFTER_DAYS + 2)
    await _seed(
        [
            {"series_key": SERIES_KEY, "time": _kst(old_day, 10, 0), "value": 100.0, "resolution_seconds": 0},
            {"series_key": SERIES_KEY, "time": _kst(old_day, 10, 5), "value": 200.0, "resolution_seconds": 0},
        ]
    )

    async with async_session_factory() as session:
        first_run = await compact_intraday_samples(session, TEST_DAY)
        await session.commit()
    assert first_run == 1

    rows_after_first = await _rows_for(SERIES_KEY)
    assert len(rows_after_first) == 1
    assert rows_after_first[0].resolution_seconds == BUCKET_SECONDS
    assert float(rows_after_first[0].value) == pytest.approx(150.0)

    # 재실행(멱등성) — 원본(resolution_seconds=0)이 이미 삭제됐으니 재압축 대상이
    # 없어 아무 것도 바뀌지 않아야 한다.
    async with async_session_factory() as session:
        second_run = await compact_intraday_samples(session, TEST_DAY)
        await session.commit()
    assert second_run == 0

    rows_after_second = await _rows_for(SERIES_KEY)
    assert len(rows_after_second) == 1
    assert float(rows_after_second[0].value) == pytest.approx(150.0)
    assert rows_after_second[0].resolution_seconds == BUCKET_SECONDS


async def test_compacts_multiple_series_keys_independently():
    old_day = TEST_DAY - dt.timedelta(days=COMPACT_AFTER_DAYS + 2)
    await _seed(
        [
            {"series_key": SERIES_KEY, "time": _kst(old_day, 10, 0), "value": 10.0, "resolution_seconds": 0},
            {"series_key": OTHER_SERIES_KEY, "time": _kst(old_day, 10, 0), "value": 90.0, "resolution_seconds": 0},
        ]
    )

    async with async_session_factory() as session:
        rows_written = await compact_intraday_samples(session, TEST_DAY)
        await session.commit()

    assert rows_written == 2

    rows_a = await _rows_for(SERIES_KEY)
    rows_b = await _rows_for(OTHER_SERIES_KEY)
    assert float(rows_a[0].value) == 10.0
    assert float(rows_b[0].value) == 90.0


async def test_no_old_rows_returns_zero():
    async with async_session_factory() as session:
        rows_written = await compact_intraday_samples(session, TEST_DAY)
        await session.commit()

    assert rows_written == 0
