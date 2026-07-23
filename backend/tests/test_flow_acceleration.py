"""Unit tests for app.quant.flow_acceleration (PLAN.md §5.17, 수급 가속도
실시간 반응성 지표).

Same house pattern as tests/test_regime_backtest.py: real dev Postgres via
app.db.async_session_factory, isolated with a dedicated fake series_key
(``__test_flow_accel_*__``) that the real app never queries, so no date-based
isolation is needed.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.db import async_session_factory, engine
from app.models import IntradaySample
from app.quant import flow_acceleration

ACCEL_KEY = "__test_flow_accel_up__"
DECEL_KEY = "__test_flow_accel_down__"
FLAT_KEY = "__test_flow_accel_flat__"
INSUFFICIENT_KEY = "__test_flow_accel_short__"
APPROX_KEY = "__test_flow_accel_approx__"

ALL_TEST_KEYS = [ACCEL_KEY, DECEL_KEY, FLAT_KEY, INSUFFICIENT_KEY, APPROX_KEY]

NOW = dt.datetime(2099, 1, 1, 10, 30, tzinfo=dt.timezone.utc)


async def _clear_test_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(IntradaySample.__table__.delete().where(IntradaySample.series_key.in_(ALL_TEST_KEYS)))
        await session.commit()


async def _seed(rows: list[tuple[str, dt.datetime, float]]) -> None:
    async with async_session_factory() as session:
        for series_key, time, value in rows:
            session.add(IntradaySample(series_key=series_key, time=time, value=value, resolution_seconds=0))
        await session.commit()


@pytest.fixture(autouse=True)
async def _fixture_data():
    await _clear_test_rows()
    await _seed(
        [
            # ACCEL_KEY: 60분전 1000 -> 30분전 1300(속도+300) -> 지금 2200(속도+900)
            # -> acceleration = 900-300 = +600 (가속)
            (ACCEL_KEY, NOW - dt.timedelta(minutes=61), 1000.0),
            (ACCEL_KEY, NOW - dt.timedelta(minutes=31), 1300.0),
            (ACCEL_KEY, NOW - dt.timedelta(minutes=1), 2200.0),
            # DECEL_KEY: 60분전 1000 -> 30분전 1800(속도+800) -> 지금 1900(속도+100)
            # -> acceleration = 100-800 = -700 (감속)
            (DECEL_KEY, NOW - dt.timedelta(minutes=61), 1000.0),
            (DECEL_KEY, NOW - dt.timedelta(minutes=31), 1800.0),
            (DECEL_KEY, NOW - dt.timedelta(minutes=1), 1900.0),
            # FLAT_KEY: 속도 동일(+300, +300) -> acceleration = 0
            (FLAT_KEY, NOW - dt.timedelta(minutes=61), 1000.0),
            (FLAT_KEY, NOW - dt.timedelta(minutes=31), 1300.0),
            (FLAT_KEY, NOW - dt.timedelta(minutes=1), 1600.0),
            # INSUFFICIENT_KEY: 35분치만 있음(60분전 틱 없음) -> None
            (INSUFFICIENT_KEY, NOW - dt.timedelta(minutes=35), 500.0),
            (INSUFFICIENT_KEY, NOW - dt.timedelta(minutes=10), 700.0),
            (INSUFFICIENT_KEY, NOW - dt.timedelta(minutes=1), 800.0),
            # APPROX_KEY: 정확히 경계 시각(60분전/30분전/지금)에 틱이 없고 그 "직전"
            # 틱만 있음 — "그 시각 이전 가장 가까운 값"으로 근사되는지 검증.
            # 60분전 경계(NOW-60m) 이전 가장 가까운 틱 = NOW-61m(값 100)
            (APPROX_KEY, NOW - dt.timedelta(minutes=61), 100.0),
            # 60분전 경계 "이후"(NOW-59m)는 60분전 근사값으로 쓰이면 안 됨(미래 누수) —
            # 아래 40분전 틱보다 먼저 나오지만 30분전 경계보다 전이라 prior 후보.
            (APPROX_KEY, NOW - dt.timedelta(minutes=59), 150.0),
            # 30분전 경계(NOW-30m) 이전 가장 가까운 틱 = NOW-31m(값 400)
            (APPROX_KEY, NOW - dt.timedelta(minutes=31), 400.0),
            # 지금(NOW) 이전 가장 가까운 틱 = NOW-1m(값 900)
            (APPROX_KEY, NOW - dt.timedelta(minutes=1), 900.0),
        ]
    )
    yield
    await _clear_test_rows()
    await engine.dispose()


async def test_acceleration_detects_speeding_up_flow():
    async with async_session_factory() as session:
        result = await flow_acceleration.compute_flow_acceleration(session, ACCEL_KEY, NOW)

    assert result == {
        "window_minutes": 30,
        "recent_velocity": pytest.approx(900.0),
        "prior_velocity": pytest.approx(300.0),
        "acceleration": pytest.approx(600.0),
    }
    assert result["acceleration"] > 0


async def test_acceleration_detects_slowing_down_flow():
    async with async_session_factory() as session:
        result = await flow_acceleration.compute_flow_acceleration(session, DECEL_KEY, NOW)

    assert result == {
        "window_minutes": 30,
        "recent_velocity": pytest.approx(100.0),
        "prior_velocity": pytest.approx(800.0),
        "acceleration": pytest.approx(-700.0),
    }
    assert result["acceleration"] < 0


async def test_acceleration_zero_when_velocity_unchanged():
    async with async_session_factory() as session:
        result = await flow_acceleration.compute_flow_acceleration(session, FLAT_KEY, NOW)

    assert result["recent_velocity"] == pytest.approx(300.0)
    assert result["prior_velocity"] == pytest.approx(300.0)
    assert result["acceleration"] == pytest.approx(0.0)


async def test_acceleration_returns_none_when_data_insufficient():
    async with async_session_factory() as session:
        result = await flow_acceleration.compute_flow_acceleration(session, INSUFFICIENT_KEY, NOW)

    assert result is None  # 60분전 틱이 없음(35분치만 적립됨) -> 억지로 계산하지 않음


async def test_acceleration_returns_none_for_unknown_series_key():
    async with async_session_factory() as session:
        result = await flow_acceleration.compute_flow_acceleration(session, "__no_such_series__", NOW)

    assert result is None


async def test_acceleration_approximates_to_nearest_earlier_tick():
    """정확히 경계 시각(60분전/30분전/지금)에 틱이 없어도 "그 시각 이전 가장
    가까운 값"으로 근사해 계산해야 한다(PLAN.md §5.17 명시 — 60초 틱이라 오차는
    최대 1분 이내)."""
    async with async_session_factory() as session:
        result = await flow_acceleration.compute_flow_acceleration(session, APPROX_KEY, NOW)

    # now~=900(NOW-1m), recent(30분전)~=400(NOW-31m) -> recent_velocity=500
    # recent(30분전)~=400, prior(60분전)~=100(NOW-61m, NOT the NOW-59m=150 tick,
    # since NOW-59m is *after* the NOW-60m boundary — must not leak forward)
    # -> prior_velocity=300
    assert result == {
        "window_minutes": 30,
        "recent_velocity": pytest.approx(500.0),
        "prior_velocity": pytest.approx(300.0),
        "acceleration": pytest.approx(200.0),
    }


async def test_acceleration_custom_window_minutes():
    async with async_session_factory() as session:
        result = await flow_acceleration.compute_flow_acceleration(session, ACCEL_KEY, NOW, window_minutes=60)

    # window=60이면 60분전/120분전을 봐야 하는데 120분전 틱이 없으므로 None.
    assert result is None
