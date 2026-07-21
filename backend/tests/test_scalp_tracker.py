"""Unit/integration tests for app.collectors.scalp_tracker (PLAN.md §5.7,
스켈핑 후보 추적 기록 — 관찰 로그, 매매 신호 아님).

Same house pattern as tests/test_basis_router.py: real dev Postgres via
app.db.async_session_factory, test rows dated 2099-01-05 (far outside any real
data) cleaned up in teardown. The tracker's own external-fetch layer
(routers.scalp._scored_candidates / _fetch_live_payloads) is monkeypatched so
these tests never touch the network — they only verify the DB read/write
logic in scalp_tracker.py itself.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.collectors import scalp_tracker
from app.db import async_session_factory, engine
from app.models import ScalpPick
from app.routers.scalp import router as scalp_router

KST = dt.timezone(dt.timedelta(hours=9))
TEST_DATE = dt.date(2099, 1, 5)
ENTRY_TIME = dt.datetime(2099, 1, 5, 10, 0, 0, tzinfo=KST)

SCORED_CANDIDATES = [
    {
        "code": "000660",
        "name": "SK하이닉스",
        "market": "kospi",
        "change_rate": 3.5,
        "turnover": 8.2,
        "value_rank": 1,
        "in_attention_top": True,
        "score": 1.234,
    },
    {
        "code": "247540",
        "name": "에코프로비엠",
        "market": "kosdaq",
        "change_rate": -6.1,
        "turnover": 15.4,
        "value_rank": 3,
        "in_attention_top": False,
        "score": 0.5,
    },
]

VALUE_PAYLOAD = {"date": "2099-01-05", "market_closed": False, "cached_at": "x"}


async def _clear_test_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(ScalpPick.__table__.delete().where(ScalpPick.date == TEST_DATE))
        await session.commit()


@pytest.fixture(autouse=True)
async def _clean_scalp_pick():
    # 단일 autouse 픽스처로 정리+엔진 dispose를 함께 처리한다(tests/test_basis_router.py의
    # `_dispose_engine_per_test` 단독 autouse 패턴과 동일) — 두 개의 별도 autouse
    # 비동기 픽스처를 두면 테스트 간 이벤트 루프 경계에서 커넥션 풀이 꼬여
    # "attached to a different loop" RuntimeError가 나는 걸 실제로 겪었다.
    await _clear_test_rows()
    yield
    await _clear_test_rows()
    await engine.dispose()


async def _fake_scored_candidates(session):
    return SCORED_CANDIDATES, VALUE_PAYLOAD


# ---------------------------------------------------------------------------
# record_new_entries — 1일 1종목 1회
# ---------------------------------------------------------------------------


async def test_record_new_entries_inserts_each_candidate_once(monkeypatch):
    monkeypatch.setattr(scalp_tracker, "_scored_candidates", _fake_scored_candidates)

    async with async_session_factory() as session:
        inserted = await scalp_tracker.record_new_entries(session, ENTRY_TIME)

    assert inserted == 2

    async with async_session_factory() as session:
        rows = (
            await session.execute(select(ScalpPick).where(ScalpPick.date == TEST_DATE))
        ).scalars().all()

    by_code = {r.code: r for r in rows}
    assert set(by_code) == {"000660", "247540"}
    assert by_code["000660"].entry_rank == 1
    assert by_code["247540"].entry_rank == 2
    assert float(by_code["000660"].entry_score) == pytest.approx(1.234)
    assert by_code["000660"].in_attention_top_at_entry is True
    assert by_code["247540"].in_attention_top_at_entry is False
    assert by_code["000660"].name == "SK하이닉스"
    assert by_code["000660"].market == "kospi"


async def test_record_new_entries_same_code_same_day_inserted_once(monkeypatch):
    """같은 종목이 그날 다시 상위권에 뜨더라도(재조회) 두 번째 호출에서는
    스킵돼야 한다(§5.7 설계 1번 "1일 1종목 1회") — 자기상관 방지."""
    monkeypatch.setattr(scalp_tracker, "_scored_candidates", _fake_scored_candidates)

    async with async_session_factory() as session:
        first = await scalp_tracker.record_new_entries(session, ENTRY_TIME)
    async with async_session_factory() as session:
        second = await scalp_tracker.record_new_entries(
            session, ENTRY_TIME + dt.timedelta(minutes=1)
        )

    assert first == 2
    assert second == 0  # 이미 오늘 기록된 두 종목뿐이라 신규 삽입 없음

    async with async_session_factory() as session:
        rows = (
            await session.execute(select(ScalpPick).where(ScalpPick.date == TEST_DATE))
        ).scalars().all()
    assert len(rows) == 2  # 중복 삽입 안 됨


async def test_record_new_entries_partial_overlap_only_inserts_new_code(monkeypatch):
    """오늘 이미 기록된 종목 하나 + 새 종목 하나가 섞인 상위권이면 새 종목만
    삽입돼야 한다."""
    monkeypatch.setattr(scalp_tracker, "_scored_candidates", _fake_scored_candidates)
    async with async_session_factory() as session:
        await scalp_tracker.record_new_entries(session, ENTRY_TIME)

    async def _fake_with_new_code(session):
        return [
            SCORED_CANDIDATES[0],  # 이미 기록됨 -> 스킵
            {
                "code": "005930",
                "name": "삼성전자",
                "market": "kospi",
                "change_rate": 1.1,
                "turnover": 2.0,
                "value_rank": 2,
                "in_attention_top": False,
                "score": 0.9,
            },
        ], VALUE_PAYLOAD

    monkeypatch.setattr(scalp_tracker, "_scored_candidates", _fake_with_new_code)
    async with async_session_factory() as session:
        inserted = await scalp_tracker.record_new_entries(
            session, ENTRY_TIME + dt.timedelta(minutes=2)
        )

    assert inserted == 1
    async with async_session_factory() as session:
        rows = (
            await session.execute(select(ScalpPick).where(ScalpPick.date == TEST_DATE))
        ).scalars().all()
    assert {r.code for r in rows} == {"000660", "247540", "005930"}


# ---------------------------------------------------------------------------
# fill_horizons — 도래한 호라이즌만 채움
# ---------------------------------------------------------------------------


async def _seed_pick(session, code: str, entry_time: dt.datetime) -> None:
    session.add(
        ScalpPick(
            date=TEST_DATE,
            code=code,
            name=code,
            market="kospi",
            entry_time=entry_time,
            entry_rank=1,
            entry_score=1.0,
            entry_change_rate=1.0,
            entry_turnover=1.0,
            in_attention_top_at_entry=False,
        )
    )
    await session.commit()


async def test_fill_horizons_fills_elapsed_and_skips_not_yet_elapsed(monkeypatch):
    entry_time = dt.datetime(2099, 1, 5, 9, 0, 0, tzinfo=KST)
    async with async_session_factory() as session:
        await _seed_pick(session, "000660", entry_time)

    async def _fake_fetch_live_payloads(session):
        return {"rows": [{"code": "000660", "change_rate": 4.2}]}, {"rows": []}

    monkeypatch.setattr(scalp_tracker, "_fetch_live_payloads", _fake_fetch_live_payloads)

    # 5분은 지났지만(9:00 + 5m = 9:05) 15분은 아직(9:00 + 15m = 9:15) 안 지난 시점.
    now_kst = dt.datetime(2099, 1, 5, 9, 10, 0, tzinfo=KST)
    async with async_session_factory() as session:
        filled = await scalp_tracker.fill_horizons(session, now_kst)

    assert filled == 1

    async with async_session_factory() as session:
        row = (
            await session.execute(select(ScalpPick).where(ScalpPick.code == "000660", ScalpPick.date == TEST_DATE))
        ).scalar_one()

    assert float(row.change_rate_5m) == pytest.approx(4.2)
    assert row.change_rate_15m is None
    assert row.change_rate_30m is None
    assert row.change_rate_60m is None


async def test_fill_horizons_does_not_touch_already_filled_column(monkeypatch):
    """이미 채워진 호라이즌 컬럼(NULL 아님)은 재계산하지 않는다."""
    # entry_time=8:50 기준 now=9:10이면 5m(8:55)/15m(9:05)는 지났고
    # 30m(9:20)/60m(9:50)은 아직 안 지남 — 5m을 미리 채워두면 그 컬럼만 그대로
    # 유지되고 15m만 새로 채워져야 한다(30m/60m은 애초에 시각이 안 지나 대상 아님).
    entry_time = dt.datetime(2099, 1, 5, 8, 50, 0, tzinfo=KST)
    async with async_session_factory() as session:
        await _seed_pick(session, "000660", entry_time)
        row = (
            await session.execute(select(ScalpPick).where(ScalpPick.code == "000660", ScalpPick.date == TEST_DATE))
        ).scalar_one()
        row.change_rate_5m = 0.0  # 이론상 0%도 "채워짐"으로 취급돼야 함
        await session.commit()

    async def _fake_fetch_live_payloads(session):
        return {"rows": [{"code": "000660", "change_rate": 99.9}]}, {"rows": []}

    monkeypatch.setattr(scalp_tracker, "_fetch_live_payloads", _fake_fetch_live_payloads)

    now_kst = dt.datetime(2099, 1, 5, 9, 10, 0, tzinfo=KST)
    async with async_session_factory() as session:
        filled = await scalp_tracker.fill_horizons(session, now_kst)

    assert filled == 1  # 15m만 새로 채워짐

    async with async_session_factory() as session:
        row = (
            await session.execute(select(ScalpPick).where(ScalpPick.code == "000660", ScalpPick.date == TEST_DATE))
        ).scalar_one()
    assert row.change_rate_5m == 0.0  # 그대로 유지(재계산 안 됨)
    assert float(row.change_rate_15m) == pytest.approx(99.9)


async def test_fill_horizons_no_data_in_cache_leaves_column_null(monkeypatch):
    """캐시에 해당 code의 change_rate가 없으면(예: 거래대금 상위권 이탈) 이번
    폴링에서는 건너뛰고 NULL로 남겨야 한다(다음 폴링에서 재시도)."""
    entry_time = dt.datetime(2099, 1, 5, 9, 0, 0, tzinfo=KST)
    async with async_session_factory() as session:
        await _seed_pick(session, "000660", entry_time)

    async def _fake_fetch_live_payloads(session):
        return {"rows": []}, {"rows": []}

    monkeypatch.setattr(scalp_tracker, "_fetch_live_payloads", _fake_fetch_live_payloads)

    now_kst = dt.datetime(2099, 1, 5, 9, 10, 0, tzinfo=KST)
    async with async_session_factory() as session:
        filled = await scalp_tracker.fill_horizons(session, now_kst)

    assert filled == 0
    async with async_session_factory() as session:
        row = (
            await session.execute(select(ScalpPick).where(ScalpPick.code == "000660", ScalpPick.date == TEST_DATE))
        ).scalar_one()
    assert row.change_rate_5m is None


# ---------------------------------------------------------------------------
# fill_eod — NXT 마감 후에만
# ---------------------------------------------------------------------------


async def test_fill_eod_noop_before_nxt_close(monkeypatch):
    entry_time = dt.datetime(2099, 1, 5, 9, 0, 0, tzinfo=KST)
    async with async_session_factory() as session:
        await _seed_pick(session, "000660", entry_time)

    called = {"fetched": False}

    async def _fake_fetch_live_payloads(session):
        called["fetched"] = True
        return {"rows": [{"code": "000660", "change_rate": 5.0}]}, {"rows": []}

    monkeypatch.setattr(scalp_tracker, "_fetch_live_payloads", _fake_fetch_live_payloads)

    now_kst = dt.datetime(2099, 1, 5, 15, 0, 0, tzinfo=KST)  # NXT 개장 중(08~20시)
    async with async_session_factory() as session:
        filled = await scalp_tracker.fill_eod(session, now_kst)

    assert filled == 0
    assert called["fetched"] is False


async def test_fill_eod_fills_after_nxt_close(monkeypatch):
    entry_time = dt.datetime(2099, 1, 5, 9, 0, 0, tzinfo=KST)
    async with async_session_factory() as session:
        await _seed_pick(session, "000660", entry_time)

    async def _fake_fetch_live_payloads(session):
        return {"rows": [{"code": "000660", "change_rate": 5.0}]}, {"rows": []}

    monkeypatch.setattr(scalp_tracker, "_fetch_live_payloads", _fake_fetch_live_payloads)

    now_kst = dt.datetime(2099, 1, 5, 20, 30, 0, tzinfo=KST)  # NXT 마감 후(20시 이후)
    async with async_session_factory() as session:
        filled = await scalp_tracker.fill_eod(session, now_kst)

    assert filled == 1
    async with async_session_factory() as session:
        row = (
            await session.execute(select(ScalpPick).where(ScalpPick.code == "000660", ScalpPick.date == TEST_DATE))
        ).scalar_one()
    assert float(row.change_rate_eod) == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# track_scalp_picks — 세 단계 조합
# ---------------------------------------------------------------------------


async def test_track_scalp_picks_skips_new_entries_when_nxt_closed(monkeypatch):
    """마감 중엔 신규 진입 기록을 시도하지 않는다(마지막 캐시 스냅샷을 "새
    진입"으로 잘못 타임스탬프 찍는 사고 방지, 모듈 docstring 참고)."""
    called = {"entries": False}

    async def _fake_scored(session):
        called["entries"] = True
        return SCORED_CANDIDATES, VALUE_PAYLOAD

    async def _fake_fetch(session):
        return {"rows": []}, {"rows": []}

    monkeypatch.setattr(scalp_tracker, "_scored_candidates", _fake_scored)
    monkeypatch.setattr(scalp_tracker, "_fetch_live_payloads", _fake_fetch)

    now_kst = dt.datetime(2099, 1, 5, 20, 30, 0, tzinfo=KST)  # 마감 후
    async with async_session_factory() as session:
        result = await scalp_tracker.track_scalp_picks(session, now_kst)

    assert result["entries"] == 0
    assert called["entries"] is False


async def test_track_scalp_picks_records_entries_when_nxt_open(monkeypatch):
    monkeypatch.setattr(scalp_tracker, "_scored_candidates", _fake_scored_candidates)

    async def _fake_fetch(session):
        return {"rows": []}, {"rows": []}

    monkeypatch.setattr(scalp_tracker, "_fetch_live_payloads", _fake_fetch)

    now_kst = dt.datetime(2099, 1, 5, 10, 0, 0, tzinfo=KST)  # 개장 중
    async with async_session_factory() as session:
        result = await scalp_tracker.track_scalp_picks(session, now_kst)

    assert result["entries"] == 2


# ---------------------------------------------------------------------------
# GET /api/markets/scalp-candidates/track-record
# ---------------------------------------------------------------------------


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(scalp_router)
    return app


async def test_track_record_api_returns_seeded_rows():
    async with async_session_factory() as session:
        await _seed_pick(session, "000660", ENTRY_TIME)

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # days는 1..90 범위(라우터 Query 제약) — TEST_DATE(2099-01-05)는 실제
        # "오늘"보다 훨씬 미래라 어떤 days 값을 넣어도 하한(since) 조건을 항상
        # 만족한다(라우터가 상한 없이 since 이상만 필터링, test_basis_router.py의
        # 2099-* 관례와 동일한 이유).
        resp = await client.get("/api/markets/scalp-candidates/track-record", params={"days": 90})

    assert resp.status_code == 200
    body = resp.json()
    assert "rows" in body and "since" in body and "days" in body
    rows = [r for r in body["rows"] if r["date"] == TEST_DATE.isoformat()]
    assert len(rows) == 1
    row = rows[0]
    assert row["code"] == "000660"
    assert row["change_rate_5m"] is None
    assert row["entry_rank"] == 1


async def test_track_record_api_default_days_param_ok():
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/scalp-candidates/track-record")

    assert resp.status_code == 200
    body = resp.json()
    assert body["days"] == 7
    assert isinstance(body["rows"], list)
