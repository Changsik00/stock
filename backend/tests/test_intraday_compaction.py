"""Unit/integration tests for app.collectors.intraday_compaction (PLAN.md §5.14
다운샘플링 배치) — 최근 7일은 원본 그대로, 8일 전부터는 15분 버킷 평균으로 압축.

Same house pattern as tests/test_intraday_snapshot.py: real dev Postgres via
app.db.async_session_factory, rows isolated with a "test_compact_"-prefixed
series_key and a far-future TEST_DAY that never collides with real data,
cleaned up in teardown.

**2026-07-23 수정**: ``compact_intraday_samples``는 series_key로 스코프하지
않고 "target_date 기준 N일 지난 원본 전부"를 압축하는 전역 부수효과를 갖는다
— TEST_DAY가 2099년이라 실제 운영 데이터(§5.14 이후 worker가 실제로 쓰고
있는 flow_kospi_*/breadth_ratio 등)까지 전부 "2099년 기준으로는 오래된 것"에
걸려 같이 압축된다(장중에 실제로 쌓이고 있어 더 이상 우연이 아니라 항상
재현됨). 그래서 `rows_written`(함수의 전역 반환값)을 정확한 값으로 검증하지
않는다 — 하한(`>=`)만 확인하거나 아예 생략하고, 대신 `_rows_for(SERIES_KEY)`
(테스트 자신의 series_key로 스코프된 조회)로 실제 압축 결과를 검증한다."""

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
        rows_written = await compact_intraday_samples(session, TEST_DAY, series_keys=[SERIES_KEY])
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
        rows_written = await compact_intraday_samples(session, TEST_DAY, series_keys=[SERIES_KEY])
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
        rows_written = await compact_intraday_samples(session, TEST_DAY, series_keys=[SERIES_KEY])
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
        first_run = await compact_intraday_samples(session, TEST_DAY, series_keys=[SERIES_KEY])
        await session.commit()
    assert first_run == 1

    rows_after_first = await _rows_for(SERIES_KEY)
    assert len(rows_after_first) == 1
    assert rows_after_first[0].resolution_seconds == BUCKET_SECONDS
    assert float(rows_after_first[0].value) == pytest.approx(150.0)

    # 재실행(멱등성) — 원본(resolution_seconds=0)이 이미 삭제됐으니 재압축 대상이
    # 없어 아무 것도 바뀌지 않아야 한다.
    async with async_session_factory() as session:
        second_run = await compact_intraday_samples(session, TEST_DAY, series_keys=[SERIES_KEY])
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
        rows_written = await compact_intraday_samples(
            session, TEST_DAY, series_keys=[SERIES_KEY, OTHER_SERIES_KEY]
        )
        await session.commit()

    assert rows_written == 2

    rows_a = await _rows_for(SERIES_KEY)
    rows_b = await _rows_for(OTHER_SERIES_KEY)
    assert float(rows_a[0].value) == 10.0
    assert float(rows_b[0].value) == 90.0


async def test_no_old_rows_returns_zero():
    async with async_session_factory() as session:
        rows_written = await compact_intraday_samples(session, TEST_DAY, series_keys=[SERIES_KEY])
        await session.commit()

    assert rows_written == 0
