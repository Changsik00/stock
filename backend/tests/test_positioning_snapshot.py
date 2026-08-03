"""Integration tests for app.collectors.positioning_snapshot (PLAN.md §5.52,
§5.50 포지셔닝 프레임 사후 검증 스냅샷 — 하루 1회 기록 + same_day/next_day
2단계 채우기).

Same house pattern as tests/test_scalp_tracker.py: real dev Postgres via
app.db.async_session_factory, test rows dated 2099-01-05 (far outside any real
data) cleaned up in teardown. The tracker's reused warm functions
(routers.markets._warm_regime / flow_concentration_intraday_accumulated /
_warm_nasdaq_futures_live / _warm_stock_intraday / hynix_relative_strength /
_warm_index_tiles_live, collectors.intraday_snapshot.get_foreign_position_series,
and the module's own _sox_change_rate/get_stock_series_from_db) are all
monkeypatched so these tests never touch the network or real market_flow/
macro_series aggregates — they only verify positioning_snapshot.py's own DB
read/write logic.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from app.collectors import positioning_snapshot
from app.db import async_session_factory, engine
from app.models import PositioningSnapshot
from app.routers import markets

KST = dt.timezone(dt.timedelta(hours=9))
TEST_DATE = dt.date(2099, 1, 5)
NXT_OPEN = dt.datetime(2099, 1, 5, 10, 0, 0, tzinfo=KST)  # NXT 개장 중(08~20시)
NXT_CLOSED = dt.datetime(2099, 1, 5, 20, 30, 0, tzinfo=KST)  # NXT 마감 후


async def _clear_test_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(
            PositioningSnapshot.__table__.delete().where(PositioningSnapshot.date == TEST_DATE)
        )
        await session.commit()


@pytest.fixture(autouse=True)
async def _clean_positioning_snapshot():
    # test_scalp_tracker.py와 동일한 단일 autouse 픽스처 패턴(정리+엔진 dispose를
    # 하나로 묶는 이유는 그 파일 주석 참고).
    await _clear_test_rows()
    yield
    await _clear_test_rows()
    await engine.dispose()


def _patch_all_sources(
    monkeypatch,
    regime="코스피우세",
    concentration_value=55.5,
    spot_value=1000.0,
    futures_value=-500.0,
    sox_change_rate=1.2,
    nasdaq_change_pct=-0.3,
    hynix_close=205000.0,
    hynix_change_rate=2.5,
    kospi_change_rate=1.0,
    relative_strength_pct=1.5,
    risk_alerts=None,
):
    async def fake_warm_regime(session):
        return {"regime": regime}

    async def fake_flow_concentration(days, session):
        assert days == 1
        return {"series": [{"time": "10:00", "value": concentration_value}]} if concentration_value is not None else {"series": []}

    async def fake_get_foreign_position_series(session, days):
        assert days == 1
        return {
            "spot": [{"time": "10:00", "value": spot_value}] if spot_value is not None else [],
            "futures": [{"time": "10:00", "value": futures_value}] if futures_value is not None else [],
        }

    async def fake_sox_change_rate(session):
        return sox_change_rate

    async def fake_warm_nasdaq_futures_live():
        return {"latest_change_pct": nasdaq_change_pct}

    async def fake_warm_stock_intraday(code, interval):
        assert code == "000660"
        assert interval == 1
        return {"bars": [{"close": hynix_close}]} if hynix_close is not None else {"bars": []}

    async def fake_hynix_relative_strength(session):
        return {
            "hynix_change_rate": hynix_change_rate,
            "kospi_change_rate": kospi_change_rate,
            "relative_strength_pct": relative_strength_pct,
        }

    async def fake_warm_index_tiles_live(session):
        return {"risk": {"alerts": risk_alerts} if risk_alerts is not None else None}

    monkeypatch.setattr(markets, "_warm_regime", fake_warm_regime)
    monkeypatch.setattr(markets, "flow_concentration_intraday_accumulated", fake_flow_concentration)
    monkeypatch.setattr(
        positioning_snapshot.intraday_snapshot, "get_foreign_position_series", fake_get_foreign_position_series
    )
    monkeypatch.setattr(positioning_snapshot, "_sox_change_rate", fake_sox_change_rate)
    monkeypatch.setattr(markets, "_warm_nasdaq_futures_live", fake_warm_nasdaq_futures_live)
    monkeypatch.setattr(markets, "_warm_stock_intraday", fake_warm_stock_intraday)
    monkeypatch.setattr(markets, "hynix_relative_strength", fake_hynix_relative_strength)
    monkeypatch.setattr(markets, "_warm_index_tiles_live", fake_warm_index_tiles_live)


# ---------------------------------------------------------------------------
# record_snapshot — 오늘 1회 기록
# ---------------------------------------------------------------------------


async def test_record_snapshot_creates_row_with_all_fields(monkeypatch):
    _patch_all_sources(monkeypatch, risk_alerts=["circuit_breaker"])

    async with async_session_factory() as session:
        created = await positioning_snapshot.record_snapshot(session, NXT_OPEN)

    assert created is True

    async with async_session_factory() as session:
        row = await session.get(PositioningSnapshot, TEST_DATE)

    assert row is not None
    assert row.regime == "코스피우세"
    assert float(row.concentration_pct) == pytest.approx(55.5)
    assert float(row.foreign_spot_cum) == pytest.approx(1000.0)
    assert float(row.foreign_futures_cum) == pytest.approx(-500.0)
    assert float(row.sox_change_rate) == pytest.approx(1.2)
    assert float(row.nasdaq_futures_change_pct) == pytest.approx(-0.3)
    assert float(row.hynix_price_at_snapshot) == pytest.approx(205000.0)
    assert float(row.hynix_change_rate_at_snapshot) == pytest.approx(2.5)
    assert float(row.kospi_change_rate_at_snapshot) == pytest.approx(1.0)
    assert float(row.relative_strength_pct) == pytest.approx(1.5)
    assert row.risk_alert_count == 1
    assert row.same_day_remaining_change_rate is None
    assert row.next_day_change_rate is None


async def test_record_snapshot_risk_none_counts_as_zero(monkeypatch):
    _patch_all_sources(monkeypatch, risk_alerts=None)

    async with async_session_factory() as session:
        await positioning_snapshot.record_snapshot(session, NXT_OPEN)

    async with async_session_factory() as session:
        row = await session.get(PositioningSnapshot, TEST_DATE)
    assert row.risk_alert_count == 0


async def test_record_snapshot_skips_when_row_already_exists(monkeypatch):
    _patch_all_sources(monkeypatch)
    async with async_session_factory() as session:
        first = await positioning_snapshot.record_snapshot(session, NXT_OPEN)
    assert first is True

    async def _raise_regime(session):  # pragma: no cover - 불리면 안 됨
        raise AssertionError("_warm_regime should not be called when row already exists")

    monkeypatch.setattr(markets, "_warm_regime", _raise_regime)

    async with async_session_factory() as session:
        second = await positioning_snapshot.record_snapshot(
            session, NXT_OPEN + dt.timedelta(minutes=1)
        )
    assert second is False


async def test_record_snapshot_skips_when_nxt_closed(monkeypatch):
    called = {"regime": False}

    async def fake_warm_regime(session):
        called["regime"] = True
        return {"regime": "코스피우세"}

    monkeypatch.setattr(markets, "_warm_regime", fake_warm_regime)

    async with async_session_factory() as session:
        created = await positioning_snapshot.record_snapshot(session, NXT_CLOSED)

    assert created is False
    assert called["regime"] is False
    async with async_session_factory() as session:
        row = await session.get(PositioningSnapshot, TEST_DATE)
    assert row is None


async def test_record_snapshot_partial_failure_keeps_other_fields(monkeypatch):
    """regime 소스 하나가 예외를 던져도 나머지 필드는 그대로 기록돼야 한다
    (§5.52 설계 — 개별 소스 실패에 관대함)."""
    _patch_all_sources(monkeypatch)

    async def fake_warm_regime_boom(session):
        raise RuntimeError("kiwoom boom")

    monkeypatch.setattr(markets, "_warm_regime", fake_warm_regime_boom)

    async with async_session_factory() as session:
        created = await positioning_snapshot.record_snapshot(session, NXT_OPEN)

    assert created is True
    async with async_session_factory() as session:
        row = await session.get(PositioningSnapshot, TEST_DATE)
    assert row.regime is None
    assert float(row.relative_strength_pct) == pytest.approx(1.5)  # 무관한 필드는 살아있음


# ---------------------------------------------------------------------------
# fill_same_day — NXT 마감 후에만
# ---------------------------------------------------------------------------


async def _seed_snapshot(session, hynix_price_at_snapshot=200000.0, same_day_filled=None):
    session.add(
        PositioningSnapshot(
            date=TEST_DATE,
            snapshot_at=NXT_OPEN,
            hynix_price_at_snapshot=hynix_price_at_snapshot,
            same_day_remaining_change_rate=same_day_filled,
        )
    )
    await session.commit()


async def test_fill_same_day_noop_before_nxt_close(monkeypatch):
    async with async_session_factory() as session:
        await _seed_snapshot(session)

    called = {"fetched": False}

    async def fake_warm_stock_intraday(code, interval):
        called["fetched"] = True
        return {"bars": [{"close": 210000.0}]}

    monkeypatch.setattr(markets, "_warm_stock_intraday", fake_warm_stock_intraday)

    async with async_session_factory() as session:
        filled = await positioning_snapshot.fill_same_day(session, NXT_OPEN)

    assert filled is False
    assert called["fetched"] is False


async def test_fill_same_day_fills_after_nxt_close(monkeypatch):
    async with async_session_factory() as session:
        await _seed_snapshot(session, hynix_price_at_snapshot=200000.0)

    async def fake_warm_stock_intraday(code, interval):
        assert code == "000660"
        return {"bars": [{"close": 206000.0}]}

    monkeypatch.setattr(markets, "_warm_stock_intraday", fake_warm_stock_intraday)

    async with async_session_factory() as session:
        filled = await positioning_snapshot.fill_same_day(session, NXT_CLOSED)

    assert filled is True
    async with async_session_factory() as session:
        row = await session.get(PositioningSnapshot, TEST_DATE)
    expected = round((206000.0 - 200000.0) / 200000.0 * 100, 4)
    assert float(row.same_day_remaining_change_rate) == pytest.approx(expected)


async def test_fill_same_day_noop_when_already_filled(monkeypatch):
    async with async_session_factory() as session:
        await _seed_snapshot(session, hynix_price_at_snapshot=200000.0, same_day_filled=3.0)

    async def _raise(code, interval):  # pragma: no cover
        raise AssertionError("should not be called when already filled")

    monkeypatch.setattr(markets, "_warm_stock_intraday", _raise)

    async with async_session_factory() as session:
        filled = await positioning_snapshot.fill_same_day(session, NXT_CLOSED)
    assert filled is False


async def test_fill_same_day_noop_when_no_snapshot_price(monkeypatch):
    async with async_session_factory() as session:
        await _seed_snapshot(session, hynix_price_at_snapshot=None)

    async def _raise(code, interval):  # pragma: no cover
        raise AssertionError("should not be called when snapshot price missing")

    monkeypatch.setattr(markets, "_warm_stock_intraday", _raise)

    async with async_session_factory() as session:
        filled = await positioning_snapshot.fill_same_day(session, NXT_CLOSED)
    assert filled is False


# ---------------------------------------------------------------------------
# fill_next_day — 다음 확정 거래일 종가가 들어온 뒤
# ---------------------------------------------------------------------------


async def test_fill_next_day_fills_when_next_trading_day_available(monkeypatch):
    async with async_session_factory() as session:
        session.add(PositioningSnapshot(date=TEST_DATE, snapshot_at=NXT_OPEN))
        await session.commit()

    async def fake_get_stock_series_from_db(session, code, days):
        assert code == "000660"
        return [
            {"date": "20990104", "close": 199000.0, "changeRate": 0.5},
            {"date": TEST_DATE.strftime("%Y%m%d"), "close": 200000.0, "changeRate": 0.5},
            {"date": "20990106", "close": 204000.0, "changeRate": 2.0},  # 다음 거래일
        ]

    monkeypatch.setattr(positioning_snapshot, "get_stock_series_from_db", fake_get_stock_series_from_db)

    async with async_session_factory() as session:
        filled_count = await positioning_snapshot.fill_next_day(session, NXT_CLOSED)

    assert filled_count == 1
    async with async_session_factory() as session:
        row = await session.get(PositioningSnapshot, TEST_DATE)
    assert float(row.next_day_change_rate) == pytest.approx(2.0)


async def test_fill_next_day_skips_when_next_day_not_yet_in_db(monkeypatch):
    async with async_session_factory() as session:
        session.add(PositioningSnapshot(date=TEST_DATE, snapshot_at=NXT_OPEN))
        await session.commit()

    async def fake_get_stock_series_from_db(session, code, days):
        return [
            {"date": TEST_DATE.strftime("%Y%m%d"), "close": 200000.0, "changeRate": 0.5},
        ]  # 오늘까지만 있고 다음 거래일 없음

    monkeypatch.setattr(positioning_snapshot, "get_stock_series_from_db", fake_get_stock_series_from_db)

    async with async_session_factory() as session:
        filled_count = await positioning_snapshot.fill_next_day(session, NXT_CLOSED)

    assert filled_count == 0
    async with async_session_factory() as session:
        row = await session.get(PositioningSnapshot, TEST_DATE)
    assert row.next_day_change_rate is None


async def test_fill_next_day_skips_rows_already_filled():
    async with async_session_factory() as session:
        session.add(
            PositioningSnapshot(date=TEST_DATE, snapshot_at=NXT_OPEN, next_day_change_rate=9.9)
        )
        await session.commit()

    async with async_session_factory() as session:
        filled_count = await positioning_snapshot.fill_next_day(session, NXT_CLOSED)

    assert filled_count == 0
    async with async_session_factory() as session:
        row = await session.get(PositioningSnapshot, TEST_DATE)
    assert float(row.next_day_change_rate) == pytest.approx(9.9)  # 그대로 유지


# ---------------------------------------------------------------------------
# track_positioning_snapshot — 세 단계 조합
# ---------------------------------------------------------------------------


async def test_track_positioning_snapshot_creates_when_nxt_open(monkeypatch):
    _patch_all_sources(monkeypatch)

    async def fake_get_stock_series_from_db(session, code, days):
        return []

    monkeypatch.setattr(positioning_snapshot, "get_stock_series_from_db", fake_get_stock_series_from_db)

    async with async_session_factory() as session:
        result = await positioning_snapshot.track_positioning_snapshot(session, NXT_OPEN)

    assert result["created"] is True
    assert result["same_day_filled"] is False  # NXT 개장 중이라 same_day는 아직
    assert result["next_day_filled_count"] == 0


async def test_track_positioning_snapshot_survives_one_stage_failure(monkeypatch):
    """세 단계 중 하나(예: fill_next_day의 DB 조회)가 예외를 던져도 나머지
    단계는 그대로 실행돼야 한다."""
    _patch_all_sources(monkeypatch)

    async def fake_get_stock_series_from_db_boom(session, code, days):
        raise RuntimeError("db boom")

    monkeypatch.setattr(
        positioning_snapshot, "get_stock_series_from_db", fake_get_stock_series_from_db_boom
    )

    async with async_session_factory() as session:
        result = await positioning_snapshot.track_positioning_snapshot(session, NXT_OPEN)

    assert result["created"] is True  # record_snapshot 단계는 정상 완료
    assert result["next_day_filled_count"] == 0  # 실패했지만 예외가 전파되지 않음

    async with async_session_factory() as session:
        row = await session.get(PositioningSnapshot, TEST_DATE)
    assert row is not None
