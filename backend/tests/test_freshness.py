"""데이터 신선도 레지스트리·감시 테스트 (PLAN.md Phase 5.120 C2/C4).

- expected_date / judge: 순수 함수 — 가짜 시계(naive KST datetime)로 하루 타임라인을 돌린다.
- 실사고 재현: 운영 DB를 **읽기만** 해서 2026-11-24 20:15에 value_rank가 early로 잡히는지 확인
  (11-24 value_rank는 18:05 수집 — NXT 마감 전 값. collected_at은 NULL이라 collect_log로 대체 판정).
- collected_at 갱신: 테스트 전용 코드/먼 과거 날짜 행만 쓰고 지운다.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest
from sqlalchemy import delete, select, text

from app import freshness
from app.freshness import DATASETS, KST, Dataset, expected_date, judge

HHMM = dt.time


def at(day: dt.date, hh: int, mm: int = 0) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=KST)


WED = dt.date(2026, 11, 25)
TUE = dt.date(2026, 11, 24)
MON = dt.date(2026, 11, 23)
FRI = dt.date(2026, 11, 20)
SAT = dt.date(2026, 11, 21)
SUN = dt.date(2026, 11, 22)
HOLIDAY = dt.date(2026, 10, 9)  # krx_calendar 등록 휴장일(금)
THU_BEFORE = dt.date(2026, 10, 8)
MON_AFTER = dt.date(2026, 10, 12)


@pytest.fixture(autouse=True)
async def _dispose_engine_per_test():
    from app.db import engine

    yield
    await engine.dispose()


# ---------------------------------------------------------------- expected_date

@pytest.mark.parametrize(
    "hh,mm,expected",
    [
        (8, 0, TUE),      # 배치 전 — 어제 거래일
        (15, 35, TUE),    # 장 마감 직후여도 배치(18:10) 전엔 어제
        (18, 5, TUE),     # 18:10 직전
        (18, 15, WED),    # 18:10 이후 — 오늘
        (20, 3, WED),
        (20, 15, WED),
        (23, 0, WED),
    ],
)
def test_expected_date_timeline_ready_1810(hh, mm, expected):
    assert expected_date(at(WED, hh, mm), HHMM(18, 10)) == expected


@pytest.mark.parametrize(
    "hh,mm,expected",
    [(8, 0, MON), (15, 35, MON), (18, 5, MON), (18, 15, TUE), (20, 15, TUE), (23, 0, TUE)],
)
def test_expected_date_lag1(hh, mm, expected):
    """T+1 소스(etf_stats): 18:10 이후에도 직전 거래일."""
    assert expected_date(at(WED, hh, mm), HHMM(18, 10), lag=1) == expected


def test_expected_date_flow_rank_2135():
    assert expected_date(at(WED, 21, 30), HHMM(21, 35)) == TUE
    assert expected_date(at(WED, 21, 40), HHMM(21, 35)) == WED


def test_expected_date_weekend_and_holiday():
    for day in (SAT, SUN):
        assert expected_date(at(day, 12, 0), HHMM(18, 10)) == FRI
        assert expected_date(at(day, 23, 0), HHMM(18, 10)) == FRI
    assert expected_date(at(MON, 8, 0), HHMM(18, 10)) == FRI
    # 휴장일(금) — 저녁에도 전 거래일(목)
    assert expected_date(at(HOLIDAY, 20, 15), HHMM(18, 10)) == THU_BEFORE
    assert expected_date(at(HOLIDAY, 20, 15), HHMM(18, 10), lag=1) == dt.date(2026, 10, 7)
    # 휴장 다음 월요일 아침
    assert expected_date(at(MON_AFTER, 8, 0), HHMM(18, 10)) == THU_BEFORE


# ---------------------------------------------------------------- judge (순수)

VR = next(d for d in DATASETS if d.key == "value_rank")
PLAIN = next(d for d in DATASETS if d.key == "index_ohlcv")


def test_judge_stale_when_actual_older():
    status, detail = judge(PLAIN, at(WED, 18, 15), WED, TUE, None)
    assert status == "stale" and "2026-11-24" in detail


def test_judge_stale_when_no_data():
    assert judge(PLAIN, at(WED, 18, 15), WED, None, None)[0] == "stale"


def test_judge_early_when_collected_before_final():
    status, detail = judge(VR, at(WED, 20, 15), WED, WED, at(WED, 18, 5))
    assert status == "early" and "18:05" in detail


def test_judge_ok_when_collected_after_final():
    assert judge(VR, at(WED, 21, 0), WED, WED, at(WED, 20, 57))[0] == "ok"


def test_judge_final_not_judged_before_final_time():
    """19:00엔 아직 NXT 진행 중 — 18:05 수집분도 정상."""
    assert judge(VR, at(WED, 19, 0), WED, WED, at(WED, 18, 5))[0] == "ok"


def test_judge_unknown_collected_at_is_ok():
    assert judge(VR, at(WED, 21, 0), WED, WED, None)[0] == "ok"


PF = next(d for d in DATASETS if d.key == "program_flow")


def test_program_flow_final_after_nxt_close():
    """Phase 5.123: 프로그램매매도 value_rank처럼 20:00 이후 확정. 18:30엔 아직 판정 안 함, 20:15에 18:05 수집분은 early."""
    assert PF.final_after == VR.final_after
    assert judge(PF, at(WED, 18, 30), WED, WED, at(WED, 18, 5))[0] == "ok"
    status, detail = judge(PF, at(WED, 20, 15), WED, WED, at(WED, 18, 5))
    assert status == "early" and "18:05" in detail
    assert judge(PF, at(WED, 20, 15), WED, WED, at(WED, 20, 6))[0] == "ok"
    # 오늘 행이 없으면 20:15엔 stale
    assert judge(PF, at(WED, 20, 15), WED, TUE, None)[0] == "stale"


def test_judge_ok_plain():
    assert judge(PLAIN, at(WED, 18, 15), WED, WED, None) == ("ok", "")


def test_registry_keys_unique_and_required():
    keys = [d.key for d in DATASETS]
    assert len(keys) == len(set(keys))
    for k in ("index_ohlcv", "market_flow", "market_breadth", "program_flow", "group_snapshot",
              "value_rank", "stock_ohlcv", "volume_profile_daily", "flow_rank", "etf_stats"):
        assert k in keys


# ---------------------------------------------------------------- 실사고 재현 (운영 DB 읽기 전용)

async def _items(now):
    from app.db import async_session_factory

    async with async_session_factory() as session:
        items = await freshness.check_freshness(session, now)
    return {i["key"]: i for i in items}


async def test_incident_value_rank_early_at_2026_11_24_2015():
    items = await _items(at(TUE, 20, 15))
    print("\n[11-24 20:15]", {k: (v["status"], v["actual_date"], v["detail"]) for k, v in items.items()})
    vr = items["value_rank"]
    assert vr["expected_date"] == "2026-11-24"
    assert vr["actual_date"] == "2026-11-24"  # as_of 상한 — 11-25 행은 무시
    assert vr["status"] == "early"
    assert "18:05" in vr["detail"]


async def test_incident_value_rank_ok_at_2026_11_25_2100():
    items = await _items(at(WED, 21, 0))
    print("\n[11-25 21:00]", {k: (v["status"], v["actual_date"], v["detail"]) for k, v in items.items()})
    assert items["value_rank"]["status"] == "ok"
    assert items["value_rank"]["actual_date"] == "2026-11-25"


# ---------------------------------------------------------------- collected_at 갱신 (테스트 행만 쓰기)

TEST_CODE = "999913"
OLD_DATE = dt.date(2001, 1, 2)


async def test_value_rank_upsert_refreshes_collected_at():
    from app.collectors import value_rank
    from app.db import async_session_factory
    from app.models import ValueRank

    row = {"code": TEST_CODE, "name": "테스트", "value_million": 100, "market_value_million": 1000,
           "change_rate": 1.0}

    async def clean():
        async with async_session_factory() as s:
            await s.execute(delete(ValueRank).where(ValueRank.date == OLD_DATE))
            await s.commit()

    await clean()
    try:
        stamps = []
        for _ in range(2):
            async with async_session_factory() as s:
                await value_rank._upsert_market_rows(s, OLD_DATE, "kospi", [row], set())
                await s.commit()
            async with async_session_factory() as s:
                stamps.append(
                    (await s.execute(select(ValueRank.collected_at).where(ValueRank.date == OLD_DATE))).scalar()
                )
            await asyncio.sleep(1.1)
        assert stamps[0] is not None and stamps[1] > stamps[0]
    finally:
        await clean()


async def test_stock_ohlcv_upsert_refreshes_collected_at():
    from app import services
    from app.db import async_session_factory
    from app.models import Stock, StockOhlcv

    async def clean():
        async with async_session_factory() as s:
            await s.execute(delete(StockOhlcv).where(StockOhlcv.code == TEST_CODE))
            await s.execute(delete(Stock).where(Stock.code == TEST_CODE))
            await s.commit()

    await clean()
    try:
        async with async_session_factory() as s:
            s.add(Stock(code=TEST_CODE, name="테스트", market="KOSPI", is_etf=False))
            await s.commit()
        stamps = []
        for _ in range(2):
            async with async_session_factory() as s:
                await services.upsert_stock_ohlcv_rows(
                    s, TEST_CODE,
                    [{"date": OLD_DATE, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}],
                )
                await s.commit()
            async with async_session_factory() as s:
                stamps.append(
                    (await s.execute(select(StockOhlcv.collected_at).where(StockOhlcv.code == TEST_CODE))).scalar()
                )
            await asyncio.sleep(1.1)
        assert stamps[0] is not None and stamps[1] > stamps[0]
    finally:
        await clean()
