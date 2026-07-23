"""Unit tests for app.quant.regime_backtest (PLAN.md §5.15-1, 검증 기반 시장
우세 판정 백테스트 함수 승격).

Same house pattern as tests/test_scalp_tracker.py: real dev Postgres via
app.db.async_session_factory. Isolation here uses a fake market/investor key
(``__test_market__``/``__test_investor__``) instead of far-future dates — the
functions under test aggregate over an *entire* market's history (no date
filter), so date-based isolation wouldn't prevent the real ~3-year kospi/kosdaq
dataset from leaking into the aggregate. A dedicated market/investor string is
never queried by the real app, so it fully isolates regardless of dates.

The exact match against PLAN.md §5.15's real numbers (코스닥·외국인 3+매수
n=69/65.2%, etc.) was verified directly against the live dev DB (not repeated
here as an automated test — those numbers drift as new trading days are
collected daily, so asserting them in CI would be flaky by design). This file
instead verifies the algorithm against a small hand-computed fixture whose
expected values never change.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.db import async_session_factory, engine
from app.models import IndexOhlcv, MarketFlow
from app.quant import regime_backtest

TEST_MARKET = "__test_market__"
TEST_INVESTOR = "__test_investor__"

# 7 거래일 종가 -> 다음날 수익률(%) 은 아래처럼 결정된다:
#   day1 100 -> day2 102 : +2.0
#   day2 102 -> day3 101 : -0.9803921568627451
#   day3 101 -> day4 105 : +3.9603960396039604
#   day4 105 -> day5 103 : -1.9047619047619047
#   day5 103 -> day6 101 : -1.941747572815534
#   day6 101 -> day7 104 : +2.9702970297029703
CLOSES = [100, 102, 101, 105, 103, 101, 104]

# net_value 부호 시퀀스 -> 스트릭: +1,+2,+3(3+매수),-1(1매도),-2(2매도),+1(1매수)
NET_VALUES = [100, 100, 100, -50, -50, 10]

DAYS = [dt.date(2099, 1, 1) + dt.timedelta(days=i) for i in range(7)]


async def _clear_test_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(IndexOhlcv.__table__.delete().where(IndexOhlcv.market == TEST_MARKET))
        await session.execute(MarketFlow.__table__.delete().where(MarketFlow.market == TEST_MARKET))
        await session.commit()


async def _seed() -> None:
    async with async_session_factory() as session:
        for d, close in zip(DAYS, CLOSES):
            session.add(IndexOhlcv(market=TEST_MARKET, date=d, open=close, high=close, low=close, close=close))
        for d, net_value in zip(DAYS, NET_VALUES):
            session.add(
                MarketFlow(
                    market=TEST_MARKET,
                    date=d,
                    investor=TEST_INVESTOR,
                    net_value=net_value,
                    net_volume=None,
                    source="test",
                )
            )
        await session.commit()


@pytest.fixture(autouse=True)
async def _fixture_data():
    # test_scalp_tracker.py와 동일한 단일 autouse 픽스처 패턴(정리+엔진 dispose를
    # 하나로 묶는 이유는 그 파일 주석 참고 — 이벤트 루프 경계 커넥션 풀 문제 방지).
    await _clear_test_rows()
    await _seed()
    yield
    await _clear_test_rows()
    await engine.dispose()


# ---------------------------------------------------------------------------
# 순수 함수 (DB 무관)
# ---------------------------------------------------------------------------


def test_bucket_label_maps_streak_to_bucket():
    assert regime_backtest.bucket_label(5) == "3+매수"
    assert regime_backtest.bucket_label(3) == "3+매수"
    assert regime_backtest.bucket_label(2) == "2매수"
    assert regime_backtest.bucket_label(1) == "1매수"
    assert regime_backtest.bucket_label(0) is None
    assert regime_backtest.bucket_label(-1) == "1매도"
    assert regime_backtest.bucket_label(-2) == "2매도"
    assert regime_backtest.bucket_label(-3) == "3+매도"
    assert regime_backtest.bucket_label(-9) == "3+매도"


def test_next_streak_accumulates_same_sign_and_resets_on_flip():
    assert regime_backtest.next_streak(0, 100) == 1
    assert regime_backtest.next_streak(1, 100) == 2
    assert regime_backtest.next_streak(2, 100) == 3
    assert regime_backtest.next_streak(3, -1) == -1  # 부호 반전 -> 리셋
    assert regime_backtest.next_streak(-1, -1) == -2
    assert regime_backtest.next_streak(-2, 0) == 0  # 0은 스트릭 리셋
    assert regime_backtest.next_streak(5, None) == 5  # None은 그대로 유지


# ---------------------------------------------------------------------------
# DB 기반 계산 (고정 픽스처)
# ---------------------------------------------------------------------------


async def test_compute_current_streak_returns_last_value():
    async with async_session_factory() as session:
        streak = await regime_backtest.compute_current_streak(session, TEST_MARKET, TEST_INVESTOR)
    assert streak == 1  # 마지막 net_value=10(양수)이고 직전이 매도 스트릭이라 1로 리셋


async def test_compute_current_streak_no_data_returns_zero():
    async with async_session_factory() as session:
        streak = await regime_backtest.compute_current_streak(session, TEST_MARKET, "__no_such_investor__")
    assert streak == 0


async def test_compute_baseline_matches_full_return_series():
    async with async_session_factory() as session:
        baseline = await regime_backtest.compute_baseline(session, TEST_MARKET)

    # 6개 수익률 전부: [+2.0, -0.980..., +3.960..., -1.905..., -1.942..., +2.970...]
    # 양수 3개(day1/day3/day6)/6개 = 50.0%
    assert baseline["n"] == 6
    assert baseline["positive_rate_pct"] == pytest.approx(50.0, abs=0.1)
    assert baseline["avg_return_pct"] == pytest.approx(0.684, abs=0.01)


async def test_compute_baseline_unknown_market_is_empty():
    async with async_session_factory() as session:
        baseline = await regime_backtest.compute_baseline(session, "__no_such_market__")
    assert baseline == {"n": 0, "avg_return_pct": None, "positive_rate_pct": None}


async def test_compute_streak_buckets_matches_hand_computed_values():
    async with async_session_factory() as session:
        buckets = await regime_backtest.compute_streak_buckets(session, TEST_MARKET, TEST_INVESTOR)

    by_label = {b["bucket"]: b for b in buckets}

    # day1 streak=1(1매수, ret=+2.0), day6 streak=1(1매수, ret=+2.970...)
    assert by_label["1매수"]["n"] == 2
    assert by_label["1매수"]["avg_return_pct"] == pytest.approx(2.485, abs=0.01)
    assert by_label["1매수"]["positive_rate_pct"] == 100.0

    # day2 streak=2(2매수, ret=-0.980...)
    assert by_label["2매수"]["n"] == 1
    assert by_label["2매수"]["avg_return_pct"] == pytest.approx(-0.980, abs=0.01)
    assert by_label["2매수"]["positive_rate_pct"] == 0.0

    # day3 streak=3(3+매수, ret=+3.960...)
    assert by_label["3+매수"]["n"] == 1
    assert by_label["3+매수"]["avg_return_pct"] == pytest.approx(3.960, abs=0.01)
    assert by_label["3+매수"]["positive_rate_pct"] == 100.0

    # day4 streak=-1(1매도, ret=-1.905...)
    assert by_label["1매도"]["n"] == 1
    assert by_label["1매도"]["avg_return_pct"] == pytest.approx(-1.905, abs=0.01)
    assert by_label["1매도"]["positive_rate_pct"] == 0.0

    # day5 streak=-2(2매도, ret=-1.942...)
    assert by_label["2매도"]["n"] == 1
    assert by_label["2매도"]["avg_return_pct"] == pytest.approx(-1.942, abs=0.01)
    assert by_label["2매도"]["positive_rate_pct"] == 0.0

    # 3+매도 버킷은 이 픽스처에 표본이 없다.
    assert by_label["3+매도"]["n"] == 0
    assert by_label["3+매도"]["avg_return_pct"] is None
    assert by_label["3+매도"]["positive_rate_pct"] is None

    assert [b["bucket"] for b in buckets] == regime_backtest.BUCKET_ORDER


async def test_compute_streak_buckets_unknown_investor_all_empty():
    async with async_session_factory() as session:
        buckets = await regime_backtest.compute_streak_buckets(session, TEST_MARKET, "__no_such_investor__")
    assert all(b["n"] == 0 for b in buckets)
