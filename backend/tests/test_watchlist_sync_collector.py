"""Unit tests for app.collectors.watchlist_sync.collect (PLAN.md §5.35-1).

Real dev Postgres (docker-compose `db` service, must be running) — same
rationale as tests/test_stocks_router.py: this repo has no sqlite test
harness, and this collector's core behavior (ON CONFLICT DO NOTHING
idempotency, per-table date fallback, FK-violation isolation) is SQL/
transaction behavior only meaningfully exercised against a real backend.

To stay deterministic despite value_rank/flow_rank/flow_path being live,
ever-growing production tables, every seeded row uses a date far in the
future (FUTURE_DATE/FUTURE_DATE_2) that the real daily batch will never
reach — so "date == target_date"/"MAX(date)" queries only ever see rows this
test itself inserted, never real production rows.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import delete, select

from app.collectors import watchlist_sync
from app.db import async_session_factory, engine
from app.models import FlowPath, FlowRank, Stock, ValueRank, Watchlist

FUTURE_DATE = dt.date(2099, 1, 1)
FUTURE_DATE_2 = dt.date(2099, 1, 2)  # target_date 후보 — 이 날짜엔 일부러 데이터를 안 둔다

TEST_CODES = ["999901", "999902", "999903"]
BAD_CODE = "999999FK"  # value_rank에만 나타나고 stocks 마스터엔 없는 코드(FK 위반 유도)


@pytest.fixture(autouse=True)
async def _dispose_engine_per_test():
    """pytest-asyncio가 테스트마다 새 이벤트 루프를 주므로, app.db.engine(모듈 전역
    싱글턴)이 이전 루프에 묶인 채 남지 않도록 매 테스트 후 dispose한다(tests/
    test_stocks_router.py와 동일한 픽스처)."""
    yield
    await engine.dispose()


async def _clear_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(
            delete(Watchlist).where(Watchlist.code.in_(TEST_CODES + [BAD_CODE]))
        )
        await session.execute(delete(ValueRank).where(ValueRank.date.in_([FUTURE_DATE, FUTURE_DATE_2])))
        await session.execute(delete(FlowRank).where(FlowRank.date.in_([FUTURE_DATE, FUTURE_DATE_2])))
        await session.execute(delete(FlowPath).where(FlowPath.date.in_([FUTURE_DATE, FUTURE_DATE_2])))
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


async def test_union_and_dedup_across_three_tables(seeded_stocks):
    """value_rank/flow_rank/flow_path에 걸쳐 코드가 겹쳐도 watchlist엔 코드당 한
    번만 신규 삽입된다(합집합 + 중복 제거)."""
    async with async_session_factory() as session:
        session.add(ValueRank(date=FUTURE_DATE, market="kospi", rank=1, code=TEST_CODES[0], name="a"))
        session.add(
            FlowRank(date=FUTURE_DATE, investor="foreign", side="buy", rank=1, code=TEST_CODES[0])
        )
        session.add(
            FlowRank(date=FUTURE_DATE, investor="foreign", side="buy", rank=2, code=TEST_CODES[1])
        )
        session.add(FlowPath(date=FUTURE_DATE, code=TEST_CODES[2]))
        await session.commit()

    async with async_session_factory() as session:
        inserted = await watchlist_sync.collect(session, FUTURE_DATE)
        await session.commit()

    assert inserted == 3

    async with async_session_factory() as session:
        rows = (
            await session.execute(select(Watchlist.code).where(Watchlist.code.in_(TEST_CODES)))
        ).scalars().all()
    assert set(rows) == set(TEST_CODES)


async def test_running_twice_does_not_duplicate_or_raise(seeded_stocks):
    """ON CONFLICT DO NOTHING — 같은 날 두 번 돌려도 에러 없이 두 번째는 신규
    삽입 0건, 실제 행은 여전히 1개뿐(added_at도 그대로).

    flow_rank/flow_path에도 FUTURE_DATE에 같은 코드로 정확히 일치하는 행을
    함께 심어 둔다 — 그렇지 않으면 이 두 테이블은 FUTURE_DATE에 데이터가
    없어 "날짜 self-healing"이 발동해 실제 운영 중인 최신 날짜의 진짜 코드
    전부를 끌어와 이 테스트의 의도(value_rank 하나만 검증)와 무관한 노이즈가
    섞인다(다른 테스트 `test_falls_back_to_latest_available_date_per_table`가
    바로 그 self-healing 자체를 검증한다)."""
    async with async_session_factory() as session:
        session.add(ValueRank(date=FUTURE_DATE, market="kospi", rank=1, code=TEST_CODES[0], name="a"))
        session.add(
            FlowRank(date=FUTURE_DATE, investor="foreign", side="buy", rank=1, code=TEST_CODES[0])
        )
        session.add(FlowPath(date=FUTURE_DATE, code=TEST_CODES[0]))
        await session.commit()

    async with async_session_factory() as session:
        first = await watchlist_sync.collect(session, FUTURE_DATE)
        await session.commit()
    assert first == 1

    async with async_session_factory() as session:
        second = await watchlist_sync.collect(session, FUTURE_DATE)
        await session.commit()
    assert second == 0  # 이미 있음 — 새로 삽입된 게 없다는 뜻이지 에러가 아니다

    async with async_session_factory() as session:
        rows = (
            await session.execute(select(Watchlist).where(Watchlist.code == TEST_CODES[0]))
        ).scalars().all()
    assert len(rows) == 1


async def test_falls_back_to_latest_available_date_per_table(seeded_stocks):
    """target_date(FUTURE_DATE_2)엔 value_rank에 데이터가 없고 FUTURE_DATE에만
    있으면, value_rank는 가장 최근 날짜(FUTURE_DATE)로 자동 대체돼 코드가 여전히
    수집된다. flow_rank/flow_path는 (self-healing 대상이 아니라는 걸 명확히
    하기 위해) target_date에 정확히 일치하는 행을 별도로 심어 둬 이 테스트가
    value_rank의 폴백 하나만 검증하게 한다."""
    async with async_session_factory() as session:
        session.add(ValueRank(date=FUTURE_DATE, market="kospi", rank=1, code=TEST_CODES[0], name="a"))
        session.add(
            FlowRank(date=FUTURE_DATE_2, investor="foreign", side="buy", rank=1, code=TEST_CODES[0])
        )
        session.add(FlowPath(date=FUTURE_DATE_2, code=TEST_CODES[0]))
        await session.commit()

    async with async_session_factory() as session:
        inserted = await watchlist_sync.collect(session, FUTURE_DATE_2)
        await session.commit()

    assert inserted == 1

    async with async_session_factory() as session:
        row = await session.get(Watchlist, TEST_CODES[0])
    assert row is not None


async def test_fk_violation_on_one_code_does_not_block_others(seeded_stocks):
    """value_rank가 stocks 마스터에 없는 코드(BAD_CODE)를 포함해도 watchlist.code
    FK 위반은 그 코드 하나만 건너뛰고, 나머지(TEST_CODES[0])는 정상 등록된다 —
    일괄 upsert가 실패하면 코드별 SAVEPOINT 재시도로 좁히는 방어 로직(모듈
    docstring "FK 안전성") 검증. flow_rank/flow_path도 FUTURE_DATE에 정확히
    일치하는 행을 심어 둬 이 테스트가 value_rank의 FK 격리 동작 하나만
    검증하게 한다(위 두 테스트와 동일한 이유)."""
    async with async_session_factory() as session:
        session.add(ValueRank(date=FUTURE_DATE, market="kospi", rank=1, code=TEST_CODES[0], name="a"))
        session.add(ValueRank(date=FUTURE_DATE, market="kospi", rank=2, code=BAD_CODE, name="bad"))
        session.add(
            FlowRank(date=FUTURE_DATE, investor="foreign", side="buy", rank=1, code=TEST_CODES[0])
        )
        session.add(FlowPath(date=FUTURE_DATE, code=TEST_CODES[0]))
        await session.commit()

    async with async_session_factory() as session:
        inserted = await watchlist_sync.collect(session, FUTURE_DATE)
        await session.commit()

    assert inserted == 1  # BAD_CODE는 FK 위반으로 건너뜀, TEST_CODES[0]만 삽입

    async with async_session_factory() as session:
        good = await session.get(Watchlist, TEST_CODES[0])
        bad = await session.get(Watchlist, BAD_CODE)
    assert good is not None
    assert bad is None
