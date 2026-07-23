"""Unit/integration tests for app.collectors.intraday_snapshot — DB-backed 장중
누적 스냅샷(PLAN.md §5.14, DB 영속화로 전면 재작성. 이전 순수 인메모리 버퍼 버전의
테스트는 §5.4-2/5.10/5.13이었다).

Same house pattern as tests/test_scalp_tracker.py/test_basis_router.py: real dev
Postgres via app.db.async_session_factory, test rows isolated by using a
"test_"-prefixed series_key set that never collides with the 8 real series_key
values, and a monkeypatched `snap._now_kst` seam (module docstring "단일 시간
seam" 참고) so timestamps are deterministic without touching real system time.
Rows are cleaned up in teardown.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from app.collectors import intraday_snapshot as snap
from app.db import async_session_factory, engine
from app.models import IntradaySample

KST = dt.timezone(dt.timedelta(hours=9))
TEST_DAY = dt.date(2099, 1, 5)  # 실 데이터와 절대 겹치지 않는 먼 미래


def _kst(hour, minute, day=TEST_DAY):
    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=KST)


def _cleanup_floor():
    # 며칠 전 날짜(days>1 창 테스트가 TEST_DAY-1에 행을 쓴다)까지 넉넉히 덮어야
    # 그 행도 다음 테스트로 새지 않고 지워진다.
    return _kst(0, 0) - dt.timedelta(days=3)


async def _clear_test_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(IntradaySample.__table__.delete().where(IntradaySample.time >= _cleanup_floor()))
        await session.commit()


@pytest.fixture(autouse=True)
async def _clean_intraday_sample():
    # 단일 autouse 픽스처로 정리+엔진 dispose(tests/test_scalp_tracker.py의
    # `_clean_scalp_pick` 패턴과 동일 — 별도 async fixture 두 개를 두면 이벤트 루프
    # 경계에서 커넥션 풀이 꼬이는 문제를 실제로 겪었다는 house 관례를 따른다).
    await _clear_test_rows()
    yield
    await _clear_test_rows()
    await engine.dispose()


def _flow_payload(kospi_gaein, kosdaq_gaein, market_closed=False):
    return {
        "kospi": {
            "date": "2099-01-05",
            "investors": {
                "개인": {"net_value": kospi_gaein, "net_volume": None},
                "외국인": {"net_value": 100, "net_volume": None},
                "기관계": {"net_value": -50, "net_volume": None},
            },
            "provisional": True,
            "source": "kiwoom_live",
        },
        "kosdaq": {
            "date": "2099-01-05",
            "investors": {
                "개인": {"net_value": kosdaq_gaein, "net_volume": None},
                "외국인": {"net_value": 10, "net_volume": None},
                "기관계": {"net_value": -5, "net_volume": None},
            },
            "provisional": True,
            "source": "kiwoom_live",
        },
        "market_closed": market_closed,
        "cached_at": "2099-01-05T03:00:00+00:00",
    }


def _futures_payload(net_value, market_closed=False):
    return {
        "date": "2099-01-05",
        "investors": {"외국인": {"net_value": net_value, "net_volume": None}},
        "market_closed": market_closed,
        "cached_at": "2099-01-05T03:00:00+00:00",
    }


def _breadth_payload(kospi=None, kosdaq=None, market_closed=False):
    return {
        "kospi": kospi,
        "kosdaq": kosdaq,
        "market_closed": market_closed,
        "cached_at": "2099-01-05T03:00:00+00:00",
    }


async def _all_rows() -> list[IntradaySample]:
    async with async_session_factory() as session:
        rows = (
            await session.execute(select(IntradaySample).where(IntradaySample.time >= _kst(0, 0)))
        ).scalars().all()
        return rows


# ---------------------------------------------------------------------------
# record_flow_snapshot
# ---------------------------------------------------------------------------


async def test_record_flow_snapshot_writes_six_series_keys(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))

    async with async_session_factory() as session:
        await snap.record_flow_snapshot(session, _flow_payload(kospi_gaein=100, kosdaq_gaein=20))

    rows = await _all_rows()
    by_key = {r.series_key: r for r in rows}
    assert set(by_key) == {
        "flow_kospi_개인",
        "flow_kospi_외국인",
        "flow_kospi_기관계",
        "flow_kosdaq_개인",
        "flow_kosdaq_외국인",
        "flow_kosdaq_기관계",
    }
    assert float(by_key["flow_kospi_개인"].value) == 100
    assert float(by_key["flow_kospi_외국인"].value) == 100
    assert float(by_key["flow_kospi_기관계"].value) == -50
    assert float(by_key["flow_kosdaq_개인"].value) == 20
    assert float(by_key["flow_kosdaq_외국인"].value) == 10
    assert float(by_key["flow_kosdaq_기관계"].value) == -5
    for row in rows:
        assert row.time == _kst(10, 0)
        assert row.resolution_seconds == 0


async def test_record_flow_snapshot_grows_series_across_multiple_calls(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    async with async_session_factory() as session:
        await snap.record_flow_snapshot(session, _flow_payload(kospi_gaein=100, kosdaq_gaein=20))

    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 1))
    async with async_session_factory() as session:
        await snap.record_flow_snapshot(session, _flow_payload(kospi_gaein=200, kosdaq_gaein=30))

    rows = await _all_rows()
    kospi_gaein_rows = sorted((r for r in rows if r.series_key == "flow_kospi_개인"), key=lambda r: r.time)
    assert [float(r.value) for r in kospi_gaein_rows] == [100, 200]


async def test_record_flow_snapshot_market_closed_writes_nothing(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    async with async_session_factory() as session:
        await snap.record_flow_snapshot(session, _flow_payload(kospi_gaein=100, kosdaq_gaein=20, market_closed=True))

    assert await _all_rows() == []


async def test_record_flow_snapshot_one_market_missing_only_writes_the_other(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    payload = _flow_payload(kospi_gaein=100, kosdaq_gaein=20)
    payload["kosdaq"] = None

    async with async_session_factory() as session:
        await snap.record_flow_snapshot(session, payload)

    rows = await _all_rows()
    keys = {r.series_key for r in rows}
    assert keys == {"flow_kospi_개인", "flow_kospi_외국인", "flow_kospi_기관계"}


async def test_record_flow_snapshot_both_markets_missing_writes_nothing(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    payload = _flow_payload(kospi_gaein=100, kosdaq_gaein=20)
    payload["kospi"] = None
    payload["kosdaq"] = None

    async with async_session_factory() as session:
        await snap.record_flow_snapshot(session, payload)

    assert await _all_rows() == []


async def test_record_flow_snapshot_same_time_conflict_does_not_raise(monkeypatch):
    """60초 잡이 정확히 60초 간격이 아니라 같은 timestamp로 두 번 불릴 수도 있다
    (예: 재시도) — ON CONFLICT DO NOTHING으로 조용히 무시돼야 한다(예외 없음)."""
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    async with async_session_factory() as session:
        await snap.record_flow_snapshot(session, _flow_payload(kospi_gaein=100, kosdaq_gaein=20))
        await snap.record_flow_snapshot(session, _flow_payload(kospi_gaein=999, kosdaq_gaein=999))

    rows = await _all_rows()
    kospi_gaein_rows = [r for r in rows if r.series_key == "flow_kospi_개인"]
    assert len(kospi_gaein_rows) == 1
    assert float(kospi_gaein_rows[0].value) == 100  # 두 번째 호출 값으로 덮이지 않는다


# ---------------------------------------------------------------------------
# record_futures_flow_snapshot
# ---------------------------------------------------------------------------


async def test_record_futures_flow_snapshot_writes_foreign_net_value(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 7))
    async with async_session_factory() as session:
        await snap.record_futures_flow_snapshot(session, _futures_payload(456))

    rows = await _all_rows()
    assert len(rows) == 1
    assert rows[0].series_key == "futures_외국인"
    assert float(rows[0].value) == 456
    assert rows[0].time == _kst(10, 7)


async def test_record_futures_flow_snapshot_market_closed_writes_nothing(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 7))
    async with async_session_factory() as session:
        await snap.record_futures_flow_snapshot(session, _futures_payload(456, market_closed=True))

    assert await _all_rows() == []


async def test_record_futures_flow_snapshot_missing_investor_writes_nothing(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 7))
    async with async_session_factory() as session:
        await snap.record_futures_flow_snapshot(
            session, {"date": "2099-01-05", "investors": {}, "market_closed": False}
        )

    assert await _all_rows() == []


# ---------------------------------------------------------------------------
# record_breadth_snapshot (PLAN.md §5.13)
# ---------------------------------------------------------------------------


async def test_record_breadth_snapshot_computes_ratio_excluding_flat(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    payload = _breadth_payload(
        kospi={"date": "2099-01-05", "adv": 500, "dec": 400, "flat": 50, "limit_up": 0, "limit_down": 0},
        kosdaq={"date": "2099-01-05", "adv": 500, "dec": 600, "flat": 30, "limit_up": 0, "limit_down": 0},
    )
    async with async_session_factory() as session:
        await snap.record_breadth_snapshot(session, payload)

    rows = await _all_rows()
    assert len(rows) == 1
    assert rows[0].series_key == "breadth_ratio"
    # total_adv=1000, total_dec=1000, flat 무시 -> 50.0%
    assert float(rows[0].value) == 50.0


async def test_record_breadth_snapshot_market_closed_writes_nothing(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    payload = _breadth_payload(
        kospi={"adv": 600, "dec": 400, "flat": 0}, kosdaq={"adv": 400, "dec": 600, "flat": 0}, market_closed=True
    )
    async with async_session_factory() as session:
        await snap.record_breadth_snapshot(session, payload)

    assert await _all_rows() == []


async def test_record_breadth_snapshot_one_market_missing_uses_available_side(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    payload = _breadth_payload(kospi={"adv": 700, "dec": 300, "flat": 20}, kosdaq=None)
    async with async_session_factory() as session:
        await snap.record_breadth_snapshot(session, payload)

    rows = await _all_rows()
    assert len(rows) == 1
    # kosdaq이 없으니 kospi만으로 계산: 700 / (700+300) * 100 = 70.0
    assert float(rows[0].value) == 70.0


async def test_record_breadth_snapshot_zero_adv_and_dec_writes_nothing(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    payload = _breadth_payload(kospi={"adv": 0, "dec": 0, "flat": 900}, kosdaq={"adv": 0, "dec": 0, "flat": 800})
    async with async_session_factory() as session:
        await snap.record_breadth_snapshot(session, payload)

    assert await _all_rows() == []


# ---------------------------------------------------------------------------
# get_flow_series
# ---------------------------------------------------------------------------


async def test_get_flow_series_shape_when_empty(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))

    async with async_session_factory() as session:
        result = await snap.get_flow_series(session, days=1)

    assert result["date"] == TEST_DAY.isoformat()
    assert result["series"] == {
        "kospi": {"개인": [], "외국인": [], "기관계": []},
        "kosdaq": {"개인": [], "외국인": [], "기관계": []},
    }
    assert isinstance(result["market_closed"], bool)


async def test_get_flow_series_reflects_written_points_split_by_market(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    async with async_session_factory() as session:
        await snap.record_flow_snapshot(session, _flow_payload(kospi_gaein=100, kosdaq_gaein=20))
        result = await snap.get_flow_series(session, days=1)

    assert result["series"]["kospi"]["개인"] == [{"time": "10:00", "value": 100.0}]
    assert result["series"]["kosdaq"]["개인"] == [{"time": "10:00", "value": 20.0}]


async def test_get_flow_series_days_greater_than_one_formats_with_date(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    async with async_session_factory() as session:
        await snap.record_flow_snapshot(session, _flow_payload(kospi_gaein=100, kosdaq_gaein=20))
        result = await snap.get_flow_series(session, days=7)

    assert result["series"]["kospi"]["개인"] == [{"time": "01/05 10:00", "value": 100.0}]


async def test_get_flow_series_days_excludes_points_before_cutoff(monkeypatch):
    # days=1은 오늘(마지막 호출 시점 KST 날짜) 00:00부터만 본다 — 어제 찍힌 점은
    # days=1일 때는 안 보이고 days=2로 늘리면 보여야 한다.
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(23, 0, day=TEST_DAY - dt.timedelta(days=1)))
    async with async_session_factory() as session:
        await snap.record_flow_snapshot(session, _flow_payload(kospi_gaein=100, kosdaq_gaein=20))

    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(9, 0))
    async with async_session_factory() as session:
        result_1d = await snap.get_flow_series(session, days=1)
        result_2d = await snap.get_flow_series(session, days=2)

    assert result_1d["series"]["kospi"]["개인"] == []
    assert len(result_2d["series"]["kospi"]["개인"]) == 1


# ---------------------------------------------------------------------------
# get_foreign_position_series — regression: kospi+kosdaq 외국인 합산(§5.10)
# ---------------------------------------------------------------------------


async def test_get_foreign_position_series_sums_kospi_and_kosdaq_by_exact_time(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    async with async_session_factory() as session:
        await snap.record_flow_snapshot(session, _flow_payload(kospi_gaein=100, kosdaq_gaein=20))
        await snap.record_futures_flow_snapshot(session, _futures_payload(456))
        result = await snap.get_foreign_position_series(session, days=1)

    # kospi 외국인(100) + kosdaq 외국인(10) = 110 (기존 합산 동작과 동일).
    assert result["spot"] == [{"time": "10:00", "value": 110.0}]
    assert result["futures"] == [{"time": "10:00", "value": 456.0}]


async def test_get_foreign_position_series_sums_across_multiple_ticks(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    async with async_session_factory() as session:
        await snap.record_flow_snapshot(session, _flow_payload(kospi_gaein=100, kosdaq_gaein=20))

    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 1))
    async with async_session_factory() as session:
        await snap.record_flow_snapshot(session, _flow_payload(kospi_gaein=200, kosdaq_gaein=30))
        result = await snap.get_foreign_position_series(session, days=1)

    assert result["spot"] == [
        {"time": "10:00", "value": 110.0},
        {"time": "10:01", "value": 110.0},
    ]


async def test_get_foreign_position_series_one_market_missing_uses_available_side(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    payload = _flow_payload(kospi_gaein=100, kosdaq_gaein=20)
    payload["kosdaq"] = None
    async with async_session_factory() as session:
        await snap.record_flow_snapshot(session, payload)
        result = await snap.get_foreign_position_series(session, days=1)

    assert result["spot"] == [{"time": "10:00", "value": 100.0}]


async def test_get_series_reports_today_even_with_no_rows(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    async with async_session_factory() as session:
        result = await snap.get_foreign_position_series(session, days=1)

    assert result["date"] == TEST_DAY.isoformat()
    assert result["spot"] == []
    assert result["futures"] == []


# ---------------------------------------------------------------------------
# get_breadth_series
# ---------------------------------------------------------------------------


async def test_get_breadth_series_shape_when_empty(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    async with async_session_factory() as session:
        result = await snap.get_breadth_series(session, days=1)

    assert result == {
        "date": TEST_DAY.isoformat(),
        "series": [],
        "market_closed": result["market_closed"],
    }


async def test_get_breadth_series_reflects_written_points(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    async with async_session_factory() as session:
        await snap.record_breadth_snapshot(
            session, _breadth_payload(kospi={"adv": 600, "dec": 400, "flat": 0}, kosdaq={"adv": 400, "dec": 600, "flat": 0})
        )
        result = await snap.get_breadth_series(session, days=1)

    assert result["series"] == [{"time": "10:00", "value": 50.0}]


# ---------------------------------------------------------------------------
# get_market_concentration_series (PLAN.md §5.18, 시총 편향 보정은 §5.19)
# ---------------------------------------------------------------------------


async def test_get_market_concentration_series_shape_when_empty(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    async with async_session_factory() as session:
        result = await snap.get_market_concentration_series(session, days=1, kospi_baseline=100.0, kosdaq_baseline=50.0)

    assert result == {
        "date": TEST_DAY.isoformat(),
        "series": [],
        "market_closed": result["market_closed"],
    }


async def test_get_market_concentration_series_computes_normalized_ratio_from_baselines(monkeypatch):
    # _flow_payload 고정값: kospi 외국인=100/기관계=-50 -> 활동량 150,
    # kosdaq 외국인=10/기관계=-5 -> 활동량 15(§5.18과 동일한 활동량 계산). §5.19부터는
    # 이 절대값을 바로 비교하지 않고 baseline으로 나눠 배율로 정규화한 뒤 비교한다:
    # kospi_multiple = 150/100 = 1.5, kosdaq_multiple = 15/5 = 3,
    # 쏠림% = 1.5/(1.5+3)*100 = 33.333...(활동량만 보면 코스피가 훨씬 크지만,
    # 코스피는 평소 대비 1.5배인데 코스닥은 평소 대비 3배라 정규화하면 코스닥
    # 쪽이 오히려 "평소보다 더 활발"하다는 걸 보여준다 — 이게 §5.19가 고치려는
    # 문제 그 자체다).
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    async with async_session_factory() as session:
        await snap.record_flow_snapshot(session, _flow_payload(kospi_gaein=100, kosdaq_gaein=20))
        result = await snap.get_market_concentration_series(session, days=1, kospi_baseline=100.0, kosdaq_baseline=5.0)

    assert len(result["series"]) == 1
    assert result["series"][0]["time"] == "10:00"
    assert result["series"][0]["value"] == pytest.approx(1.5 / 4.5 * 100)


async def test_get_market_concentration_series_uses_absolute_value_regardless_of_direction(monkeypatch):
    # 방향(순매수/순매도)과 무관하게 절댓값으로 활동량을 계산해야 한다 — 부호가
    # 반대라도(코스피 순매도, 코스닥 순매수) 활동량 자체는 양수로 합산된다.
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    payload = {
        "kospi": {"investors": {"외국인": {"net_value": -300}, "기관계": {"net_value": -100}}},
        "kosdaq": {"investors": {"외국인": {"net_value": 50}, "기관계": {"net_value": 50}}},
        "market_closed": False,
    }
    async with async_session_factory() as session:
        await snap.record_flow_snapshot(session, payload)
        # kospi 활동량 = |-300|+|-100| = 400, kosdaq 활동량 = |50|+|50| = 100.
        # baseline: kospi=200(배율 2), kosdaq=100(배율 1). 쏠림% = 2/(2+1)*100 = 66.666...
        result = await snap.get_market_concentration_series(session, days=1, kospi_baseline=200.0, kosdaq_baseline=100.0)

    assert len(result["series"]) == 1
    assert result["series"][0]["time"] == "10:00"
    assert result["series"][0]["value"] == pytest.approx(2 / 3 * 100)


async def test_get_market_concentration_series_one_market_missing_treats_it_as_zero_activity(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    payload = {
        "kospi": {"investors": {"외국인": {"net_value": 100}, "기관계": {"net_value": 50}}},
        "kosdaq": None,
        "market_closed": False,
    }
    async with async_session_factory() as session:
        await snap.record_flow_snapshot(session, payload)
        # 코스닥 쪽 series_key 자체가 안 쌓였으니 활동량 0 -> 배율도 0. 코스피 배율이
        # 뭐든(0 초과) 분모에서 코스닥 배율(0)의 비중이 없어 쏠림 100%(코스피 완전
        # 쏠림)가 그대로 유지된다.
        result = await snap.get_market_concentration_series(session, days=1, kospi_baseline=50.0, kosdaq_baseline=10.0)

    assert result["series"] == [{"time": "10:00", "value": 100.0}]


async def test_get_market_concentration_series_both_markets_zero_skips_the_tick(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    payload = {
        "kospi": {"investors": {"외국인": {"net_value": 0}, "기관계": {"net_value": 0}}},
        "kosdaq": {"investors": {"외국인": {"net_value": 0}, "기관계": {"net_value": 0}}},
        "market_closed": False,
    }
    async with async_session_factory() as session:
        await snap.record_flow_snapshot(session, payload)
        # baseline은 정상이어도(0 초과) 오늘 활동량 자체가 둘 다 0이라 배율도 둘 다
        # 0 -> 정규화 후 분모(배율 합)가 0이라 쏠림을 정의할 수 없다 — 억지로 50%를
        # 채우지 않고 건너뛴다.
        result = await snap.get_market_concentration_series(session, days=1, kospi_baseline=50.0, kosdaq_baseline=10.0)

    assert result["series"] == []


async def test_get_market_concentration_series_missing_baseline_skips_the_tick(monkeypatch):
    # PLAN.md §5.19 — 베이스라인이 없으면(market_flow 데이터 전무) 절대금액
    # 비교로 대체하지 않고 그 시각을 건너뛴다. kospi_baseline=None인 경우와
    # kosdaq_baseline<=0인 경우 둘 다 스킵되는지 확인한다(둘 중 하나만
    # 없어도 정규화 자체가 불가능하므로).
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    async with async_session_factory() as session:
        await snap.record_flow_snapshot(session, _flow_payload(kospi_gaein=100, kosdaq_gaein=20))

        result_none = await snap.get_market_concentration_series(session, days=1, kospi_baseline=None, kosdaq_baseline=5.0)
        assert result_none["series"] == []

        result_zero = await snap.get_market_concentration_series(session, days=1, kospi_baseline=100.0, kosdaq_baseline=0.0)
        assert result_zero["series"] == []


async def test_get_market_concentration_series_multiple_ticks_ordered_by_time(monkeypatch):
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    async with async_session_factory() as session:
        await snap.record_flow_snapshot(session, _flow_payload(kospi_gaein=100, kosdaq_gaein=20))

    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 1))
    async with async_session_factory() as session:
        await snap.record_flow_snapshot(session, _flow_payload(kospi_gaein=200, kosdaq_gaein=30))
        # 두 틱 모두 kospi 활동량 150/kosdaq 활동량 15로 동일(_flow_payload가 개인
        # net_value만 바꾸고 외국인/기관계는 고정값이라 — 모듈 상단 _flow_payload
        # docstring 참고). baseline도 동일하게 적용되니 두 틱 모두 같은 쏠림%.
        result = await snap.get_market_concentration_series(session, days=1, kospi_baseline=100.0, kosdaq_baseline=5.0)

    assert [p["time"] for p in result["series"]] == ["10:00", "10:01"]
    for point in result["series"]:
        assert point["value"] == pytest.approx(1.5 / 4.5 * 100)


# ---------------------------------------------------------------------------
# market_closed — 저장된 값이 아니라 호출 시점에 새로 계산
# ---------------------------------------------------------------------------


async def test_market_closed_reflects_current_clock_not_stored_payload(monkeypatch):
    # payload["market_closed"]=False였던 틱을 적립했더라도, 조회 시점에 장이
    # 마감돼 있으면 market_closed=True를 반환해야 한다(is_market_closed 재계산).
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(10, 0))
    async with async_session_factory() as session:
        await snap.record_breadth_snapshot(
            session, _breadth_payload(kospi={"adv": 600, "dec": 400, "flat": 0}, kosdaq={"adv": 400, "dec": 600, "flat": 0})
        )

    # 10:00은 장중, 20:00은 장 마감(is_market_closed는 실제 함수 그대로 사용).
    monkeypatch.setattr(snap, "_now_kst", lambda: _kst(20, 0))
    async with async_session_factory() as session:
        result = await snap.get_breadth_series(session, days=1)

    assert result["market_closed"] is True
    assert result["series"] == [{"time": "10:00", "value": 50.0}]


# ---------------------------------------------------------------------------
# days 파라미터 클램핑
# ---------------------------------------------------------------------------


async def test_clamp_days_bounds():
    assert snap._clamp_days(0) == snap.DAYS_MIN
    assert snap._clamp_days(1) == 1
    assert snap._clamp_days(30) == 30
    assert snap._clamp_days(999) == snap.DAYS_MAX
