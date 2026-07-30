"""Unit/DB tests for app.quant.flow_baseline (PLAN.md §5.44-2, flow 요소
코스피/코스닥 개별 과거 대비 percentile).

``_percentile_rank``/``aggregate_flow_baseline``은 DB 무관 순수 함수라 손계산
가능한 synthetic 리스트로 직접 검증한다. ``compute_flow_market_baseline``(세션
기반)만 실 dev Postgres(app.db.async_session_factory)를 쓴다 — test_flow_
acceleration.py와 동일한 하우스 패턴: 실 앱이 절대 조회하지 않는 전용 fake
market 문자열(``__test_flow_baseline_*__``)로 격리하고, 비교 기준일(as_of)도
미래 날짜로 고정해 날짜 기준 격리까지 이중으로 보장한다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.db import async_session_factory, engine
from app.models import MarketFlow
from app.quant.flow_baseline import (
    MIN_BASELINE_DAYS,
    _percentile_rank,
    aggregate_flow_baseline,
    compute_flow_market_baseline,
)

# market_flow.market is String(20) — 격리용 fake 이름은 그 한도 안에 들어와야 한다.
MARKET_MAIN = "__tfb_main__"
MARKET_SHORT = "__tfb_short__"
ALL_TEST_MARKETS = [MARKET_MAIN, MARKET_SHORT]

INDIVIDUAL, FOREIGN, INSTITUTION = "개인", "외국인", "기관계"

# ---------------------------------------------------------------------------
# 순수 함수 — _percentile_rank / aggregate_flow_baseline (DB 없음, 손계산 검증)
# ---------------------------------------------------------------------------


def test_percentile_rank_basic_no_ties():
    # 10/20/30 중 25보다 작은 건 10,20 두 개, 같은 값 없음 -> 2/3*100 = 66.7
    assert _percentile_rank(25.0, [10.0, 20.0, 30.0]) == pytest.approx(66.7)


def test_percentile_rank_with_ties_uses_half_weight():
    # [10,10,20,30] 중 10과 같은 값이 자신 포함 2개, 작은 값 0개
    # -> (0 + 0.5*2) / 4 * 100 = 25.0 (표준 percentile rank 동점 처리)
    assert _percentile_rank(10.0, [10.0, 10.0, 20.0, 30.0]) == pytest.approx(25.0)


def test_percentile_rank_today_below_everything_is_zero():
    assert _percentile_rank(-100.0, [0.0, 10.0, 20.0]) == pytest.approx(0.0)


def test_percentile_rank_today_above_everything_is_hundred():
    assert _percentile_rank(100.0, [0.0, 10.0, 20.0]) == pytest.approx(100.0)


# 아래 HISTORICAL은 test_compute_flow_market_baseline_excludes_today_and_trims_window의
# DB 시나리오(12일 중 zero-denominator 1일 제외 -> 11일)와 정확히 같은 값 — 순수
# 함수 계산과 세션 래퍼 계산이 일치함을 서로 다른 테스트로 교차 검증한다.
HISTORICAL = [33.3, 13.0, -23.1, 50.0, -50.0, 28.6, 4.8, 50.0, -23.1, 0.0, 50.0]


def test_aggregate_flow_baseline_success():
    result = aggregate_flow_baseline(40.0, HISTORICAL, lookback_days_requested=12)
    assert result["reason"] is None
    assert result["mean_score"] == pytest.approx(12.1)
    assert result["percentile"] == pytest.approx(72.7)
    assert result["lookback_days_requested"] == 12
    assert result["lookback_days_used"] == 11


def test_aggregate_flow_baseline_insufficient_sample_reports_reason():
    short = HISTORICAL[:3]  # 3개 < MIN_BASELINE_DAYS(10)
    result = aggregate_flow_baseline(40.0, short, lookback_days_requested=20)
    assert result["reason"] is not None
    assert "표본" in result["reason"]
    assert result["mean_score"] is None
    assert result["percentile"] is None
    assert result["lookback_days_used"] == 3


def test_aggregate_flow_baseline_today_score_none_reports_reason():
    result = aggregate_flow_baseline(None, HISTORICAL, lookback_days_requested=20)
    assert result["reason"] is not None
    assert result["mean_score"] is None
    assert result["percentile"] is None


def test_min_baseline_days_is_half_default_lookback():
    # 모듈 docstring "표본 부족 임계값" 절 — 기본 20일 요청의 절반.
    assert MIN_BASELINE_DAYS == 10


# ---------------------------------------------------------------------------
# 세션 기반 래퍼 — compute_flow_market_baseline (실 dev Postgres)
# ---------------------------------------------------------------------------

AS_OF = dt.date(2099, 1, 15)
BASE = dt.date(2099, 1, 1)  # d1

# (day_offset, individual, foreign, institution|None) — institution=None이면 그
# 투자자 행 자체를 심지 않아 "결측 투자자는 0 취급" 분기를 검증한다(day 8).
# day 6은 전부 0(개인/외국인/기관계 다 0)이라 flow_live_score 분모가 0 ->
# None -> 최종 historical에서 빠져야 한다(12일 중 11일만 남는지 검증).
DAYS = [
    (1, -100, 30, 20),
    (2, -100, 10, 5),
    (3, -50, -10, -5),
    (4, -100, 50, 50),
    (5, -100, -50, -50),
    (6, 0, 0, 0),
    (7, -100, 20, 20),
    (8, -100, 5, None),
    (9, -100, 60, 40),
    (10, -100, -20, -10),
    (11, -100, 0, 0),
    (12, -100, 100, 0),
]


async def _clear_test_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(MarketFlow.__table__.delete().where(MarketFlow.market.in_(ALL_TEST_MARKETS)))
        await session.commit()


async def _seed_main_market() -> None:
    async with async_session_factory() as session:
        for offset, individual, foreign, institution in DAYS:
            d = BASE + dt.timedelta(days=offset - 1)
            session.add(MarketFlow(market=MARKET_MAIN, date=d, investor=INDIVIDUAL, net_value=individual))
            session.add(MarketFlow(market=MARKET_MAIN, date=d, investor=FOREIGN, net_value=foreign))
            if institution is not None:
                session.add(MarketFlow(market=MARKET_MAIN, date=d, investor=INSTITUTION, net_value=institution))
        # AS_OF 당일(cutoff) 자체 데이터 — baseline에 절대 섞이면 안 된다는 것을
        # 검증하기 위해 극단값(개인 0/외국인 1/기관 0 -> score 100.0)으로 심는다.
        session.add(MarketFlow(market=MARKET_MAIN, date=AS_OF, investor=INDIVIDUAL, net_value=0))
        session.add(MarketFlow(market=MARKET_MAIN, date=AS_OF, investor=FOREIGN, net_value=1))
        session.add(MarketFlow(market=MARKET_MAIN, date=AS_OF, investor=INSTITUTION, net_value=0))
        # lookback_days=12 창보다 오래된 날짜 — 창 트리밍이 안 되면 평균이
        # 흔들리도록 극단값(score 100.0)으로 2일치를 심는다.
        for ancient_offset in (2, 1):
            ancient = BASE - dt.timedelta(days=ancient_offset)
            session.add(MarketFlow(market=MARKET_MAIN, date=ancient, investor=INDIVIDUAL, net_value=0))
            session.add(MarketFlow(market=MARKET_MAIN, date=ancient, investor=FOREIGN, net_value=5))
            session.add(MarketFlow(market=MARKET_MAIN, date=ancient, investor=INSTITUTION, net_value=0))
        await session.commit()


async def _seed_short_market() -> None:
    async with async_session_factory() as session:
        for offset in range(1, 6):  # 5일치만(표본 부족)
            d = BASE + dt.timedelta(days=offset - 1)
            session.add(MarketFlow(market=MARKET_SHORT, date=d, investor=INDIVIDUAL, net_value=-100))
            session.add(MarketFlow(market=MARKET_SHORT, date=d, investor=FOREIGN, net_value=20))
            session.add(MarketFlow(market=MARKET_SHORT, date=d, investor=INSTITUTION, net_value=10))
        await session.commit()


@pytest.fixture(autouse=True)
async def _fixture_data():
    await _clear_test_rows()
    await _seed_main_market()
    await _seed_short_market()
    yield
    await _clear_test_rows()
    await engine.dispose()


async def test_compute_flow_market_baseline_excludes_today_and_trims_window():
    async with async_session_factory() as session:
        result = await compute_flow_market_baseline(
            session, MARKET_MAIN, today_score=40.0, lookback_days=12, as_of=AS_OF
        )

    # AS_OF 당일 극단값과 창 밖 ancient 극단값(둘 다 score 100.0)이 섞였다면
    # mean이 12.1보다 훨씬 커졌을 것 — 정확히 12.1이면 둘 다 배제된 것.
    assert result["reason"] is None
    assert result["mean_score"] == pytest.approx(12.1)
    assert result["percentile"] == pytest.approx(72.7)
    assert result["lookback_days_requested"] == 12
    assert result["lookback_days_used"] == 11


async def test_compute_flow_market_baseline_insufficient_reports_reason():
    async with async_session_factory() as session:
        result = await compute_flow_market_baseline(
            session, MARKET_SHORT, today_score=40.0, lookback_days=20, as_of=AS_OF
        )

    assert result["reason"] is not None
    assert result["mean_score"] is None
    assert result["percentile"] is None
    assert result["lookback_days_used"] == 5


async def test_compute_flow_market_baseline_unknown_market_is_insufficient():
    async with async_session_factory() as session:
        result = await compute_flow_market_baseline(
            session, "__tfb_none__", today_score=10.0, as_of=AS_OF
        )

    assert result["reason"] is not None
    assert result["lookback_days_used"] == 0
