"""Unit/DB tests for app.quant.etf_weight_backtest (PLAN.md §5.26-2, ETF 비중 변화
↔ 실제 수급/가격 상관관계 백테스트 인프라).

test_etf_weight_changes.py와 동일한 하우스 패턴(실 dev Postgres,
app.db.async_session_factory)을 쓰지만, 이 파일 전용 코드/날짜 공간(2098-05/
2098-06, 990300번대)으로 격리해 그 파일의 픽스처(2099-04, 990010번대)와도
겹치지 않는다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.db import async_session_factory, engine
from app.models import EtfHolding, Stock, StockFlow, StockOhlcv
from app.quant import etf_weight_backtest as ewb

# ---------------------------------------------------------------------------
# 순수 함수 (DB 무관) — _pearson_correlation
# ---------------------------------------------------------------------------


def test_pearson_correlation_perfect_positive():
    pairs = [(1.0, 2.0), (2.0, 4.0), (3.0, 6.0), (4.0, 8.0)]
    assert ewb._pearson_correlation(pairs) == pytest.approx(1.0)


def test_pearson_correlation_perfect_negative():
    pairs = [(1.0, -3.0), (2.0, -6.0), (3.0, -9.0)]
    assert ewb._pearson_correlation(pairs) == pytest.approx(-1.0)


def test_pearson_correlation_constant_variance_is_none():
    """한쪽 변수가 전부 같은 값이면(분산 0) 상관계수 정의 자체가 불가능하다 —
    분모가 0이 되어 크래시하는 대신 None을 반환해야 한다(이 프로젝트 전반의
    "표본/조건 부족 -> None, 예외 아님" 관례)."""
    pairs = [(1.0, 5.0), (2.0, 5.0), (3.0, 5.0)]
    assert ewb._pearson_correlation(pairs) is None


def test_pearson_correlation_fewer_than_two_points_is_none():
    assert ewb._pearson_correlation([]) is None
    assert ewb._pearson_correlation([(1.0, 2.0)]) is None


# ---------------------------------------------------------------------------
# _all_consecutive_date_pairs — 4개 날짜 -> 정확히 3쌍, 올바른 순서
# ---------------------------------------------------------------------------

PAIR_TEST_DATES = [dt.date(2098, 6, 1) + dt.timedelta(days=i) for i in range(4)]
PAIR_TEST_ETF = "990390"
PAIR_TEST_STOCK = "990391"


async def _clear_pair_test_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(EtfHolding.__table__.delete().where(EtfHolding.date.in_(PAIR_TEST_DATES)))
        await session.execute(Stock.__table__.delete().where(Stock.code.in_((PAIR_TEST_ETF, PAIR_TEST_STOCK))))
        await session.commit()


@pytest.fixture
async def four_dates_seeded():
    """`etf_holdings`에 서로 다른 4개 날짜만 존재하게 심는다(내용 자체는
    무관 — 이 테스트는 오직 날짜 페어링 로직만 검증한다)."""
    await _clear_pair_test_rows()
    async with async_session_factory() as session:
        session.add(Stock(code=PAIR_TEST_ETF, name="테스트페어ETF", market="KOSDAQ", is_etf=True))
        session.add(Stock(code=PAIR_TEST_STOCK, name="테스트페어종목", market="KOSDAQ", is_etf=False))
        await session.flush()
        for d in PAIR_TEST_DATES:
            session.add(EtfHolding(etf_code=PAIR_TEST_ETF, date=d, stock_code=PAIR_TEST_STOCK, weight=5.0))
        await session.commit()
    yield
    await _clear_pair_test_rows()
    await engine.dispose()


async def test_all_consecutive_date_pairs_from_four_dates(four_dates_seeded):
    """`_all_consecutive_date_pairs`는 `etf_holdings`에 실제로 존재하는 **모든**
    날짜를 훑는다(market/code로 스코프를 좁히는 파라미터가 없다 — 함수 자체의
    존재 목적이 "지금까지 쌓인 전체 히스토리"이기 때문). 그래서 실 dev DB에
    이미 있는 2026-07 스냅샷들이 함께 섞여 나온다(test_concentration_backtest.py
    의 동일한 실데이터 공존 전략과 같은 이유) — 우리가 심은 4개 날짜(2098-06,
    항상 가장 미래라 정렬 뒤쪽에 그대로 붙는다)로 이뤄진 마지막 3쌍만 정확한
    순서로 나오는지 확인한다. 전체 리스트 길이나 그 앞부분(실데이터 유래)은
    검증 대상이 아니다."""
    async with async_session_factory() as session:
        pairs = await ewb._all_consecutive_date_pairs(session)

    d0, d1, d2, d3 = PAIR_TEST_DATES
    assert pairs[-3:] == [(d0, d1), (d1, d2), (d2, d3)]


# ---------------------------------------------------------------------------
# compute_etf_weight_correlation — 손계산 가능한 합성 다중 날짜쌍 픽스처
# ---------------------------------------------------------------------------
#
# 3개 날짜(E1<E2<E3) -> 연속 쌍 2개: (E1,E2), (E2,E3).
#
# ETF_CODE 하나가 SA/SB/SC를 다음과 같이 보유(레버리지/인버스 아님 —
# exclude_leveraged 기본값 True가 아무것도 빼지 않아야 함을 함께 확인):
#   SA: E1=5.0, E2=7.0, E3=10.0  (양쪽 날짜쌍 모두에서 연속 보유)
#   SB: E1=3.0, E2=2.0           (E3에는 없음 -> pair(E2,E3)에서는 자동 제외)
#   SC: E2=4.0, E3=4.5           (E1에는 없음 -> pair(E1,E2)에서는 자동 제외)
#
# 비중변화(curr-prev):
#   pair(E1,E2): SA=+2.0, SB=-1.0
#   pair(E2,E3): SA=+3.0, SC=+0.5
# -> n_stock_observations = 2 + 2 = 4 (< min_reliable_n=30, reliable는 False여야 함)
#
# stock_flow_delta를 weight_delta의 정확히 10배가 되도록 StockFlow를 심어
# (외국인/기관계 두 행으로 나눠 합산 로직도 함께 검증), correlation_weight_vs_flow가
# 정확히 +1.0이 나오는지 확인한다:
#   SA pair(E1,E2) 윈도우[E1,E2] 합 = 8(E1,외국인)+12(E2,기관계) = 20 = 10*2.0
#   SA pair(E2,E3) 윈도우[E2,E3] 합 = 12(E2, 위와 동일 행 재사용)+18(E3,외국인) = 30 = 10*3.0
#   SB pair(E1,E2) 윈도우[E1,E2] 합 = -10(E1,외국인) = 10*(-1.0)
#   SC pair(E2,E3) 윈도우[E2,E3] 합 = 5(E3,기관계) = 10*0.5
#
# price_change_pct를 weight_delta의 정확히 -3배가 되도록 StockOhlcv 종가를 심어
# (정수 종가로 정확히 떨어지는 값만 사용), correlation_weight_vs_price가 정확히
# -1.0이 나오는지 확인한다:
#   SA: E1=10000 -> E2=9400 (-6.0% = -3*2.0) -> E3=8554 (-9.0% = -3*3.0, 9400*0.91=8554)
#   SB: E1=20000 -> E2=20600 (+3.0% = -3*(-1.0))
#   SC: E2=10000 -> E3=9850 (-1.5% = -3*0.5)

E1, E2, E3 = (dt.date(2098, 5, 1), dt.date(2098, 5, 2), dt.date(2098, 5, 3))

CORR_ETF = "990300"
SA, SB, SC = "990301", "990302", "990303"
CORR_CODES = [CORR_ETF, SA, SB, SC]
CORR_DATES = [E1, E2, E3]


async def _clear_corr_test_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(EtfHolding.__table__.delete().where(EtfHolding.date.in_(CORR_DATES)))
        await session.execute(StockFlow.__table__.delete().where(StockFlow.code.in_(CORR_CODES)))
        await session.execute(StockOhlcv.__table__.delete().where(StockOhlcv.code.in_(CORR_CODES)))
        await session.execute(Stock.__table__.delete().where(Stock.code.in_(CORR_CODES)))
        await session.commit()


@pytest.fixture
async def corr_seeded():
    await _clear_corr_test_rows()
    async with async_session_factory() as session:
        session.add(Stock(code=CORR_ETF, name="테스트지수ETF", market="KOSDAQ", is_etf=True))
        session.add(Stock(code=SA, name="테스트종목SA", market="KOSDAQ", is_etf=False))
        session.add(Stock(code=SB, name="테스트종목SB", market="KOSDAQ", is_etf=False))
        session.add(Stock(code=SC, name="테스트종목SC", market="KOSDAQ", is_etf=False))
        await session.flush()

        # 비중 스냅샷.
        session.add(EtfHolding(etf_code=CORR_ETF, date=E1, stock_code=SA, weight=5.0))
        session.add(EtfHolding(etf_code=CORR_ETF, date=E2, stock_code=SA, weight=7.0))
        session.add(EtfHolding(etf_code=CORR_ETF, date=E3, stock_code=SA, weight=10.0))
        session.add(EtfHolding(etf_code=CORR_ETF, date=E1, stock_code=SB, weight=3.0))
        session.add(EtfHolding(etf_code=CORR_ETF, date=E2, stock_code=SB, weight=2.0))
        session.add(EtfHolding(etf_code=CORR_ETF, date=E2, stock_code=SC, weight=4.0))
        session.add(EtfHolding(etf_code=CORR_ETF, date=E3, stock_code=SC, weight=4.5))

        # 수급 — 위 주석 손계산 근거 그대로.
        session.add(StockFlow(code=SA, date=E1, investor="외국인", net_value=8))
        session.add(StockFlow(code=SA, date=E2, investor="기관계", net_value=12))
        session.add(StockFlow(code=SA, date=E3, investor="외국인", net_value=18))
        session.add(StockFlow(code=SB, date=E1, investor="외국인", net_value=-10))
        session.add(StockFlow(code=SC, date=E3, investor="기관계", net_value=5))

        # 가격 — 위 주석 손계산 근거 그대로.
        session.add(StockOhlcv(code=SA, date=E1, open=10000, high=10000, low=10000, close=10000, volume=1, value=1))
        session.add(StockOhlcv(code=SA, date=E2, open=9400, high=9400, low=9400, close=9400, volume=1, value=1))
        session.add(StockOhlcv(code=SA, date=E3, open=8554, high=8554, low=8554, close=8554, volume=1, value=1))
        session.add(StockOhlcv(code=SB, date=E1, open=20000, high=20000, low=20000, close=20000, volume=1, value=1))
        session.add(StockOhlcv(code=SB, date=E2, open=20600, high=20600, low=20600, close=20600, volume=1, value=1))
        session.add(StockOhlcv(code=SC, date=E2, open=10000, high=10000, low=10000, close=10000, volume=1, value=1))
        session.add(StockOhlcv(code=SC, date=E3, open=9850, high=9850, low=9850, close=9850, volume=1, value=1))
        await session.commit()
    yield
    await _clear_corr_test_rows()
    await engine.dispose()


async def test_aggregate_stock_weight_change_only_counts_continuous_exposure(corr_seeded):
    """SB는 E3에 없고 SC는 E1에 없다 — 신규편입/편출 쪽 기여분은 0으로 취급되어
    (해당 페어의 결과에 아예 나타나지 않아야) "연속 노출 변화만 본다"는 모듈
    독스트링의 의도적 단순화가 실제로 지켜지는지 확인한다."""
    async with async_session_factory() as session:
        pair1 = await ewb._aggregate_stock_weight_change(session, E1, E2)
        pair2 = await ewb._aggregate_stock_weight_change(session, E2, E3)

    assert pair1 == pytest.approx({SA: 2.0, SB: -1.0})
    assert pair2 == pytest.approx({SA: 3.0, SC: 0.5})


async def test_compute_etf_weight_correlation_matches_hand_computed_values(corr_seeded, monkeypatch):
    """`compute_etf_weight_correlation`은 내부적으로 `_all_consecutive_date_pairs`
    (스코프 없이 `etf_holdings` 전체 날짜를 훑는 함수, 위 테스트 참고)를 호출
    하므로 그대로 두면 실 dev DB의 2026-07 스냅샷 사이 실제 비중 변화까지
    표본에 섞여 들어와 아래 손계산 값과 맞지 않게 된다(정확히
    test_concentration_backtest.py가 `compute_daily_concentration_series`를
    monkeypatch해 우회한 것과 같은 문제). 그래서 이 함수만 우리가 심은 두
    날짜쌍으로 고정하고, 나머지(`_aggregate_stock_weight_change`·
    `etf_weight_changes._stock_flow_delta_map`·`_price_change_pct_map`)는 전부
    실제로 Postgres에 쿼리해 진짜 파이프라인이 맞물려 동작하는지 검증한다."""

    async def fake_pairs(session):
        return [(E1, E2), (E2, E3)]

    monkeypatch.setattr(ewb, "_all_consecutive_date_pairs", fake_pairs)

    async with async_session_factory() as session:
        result = await ewb.compute_etf_weight_correlation(session)

    assert result["n_date_pairs"] == 2
    assert result["n_stock_observations"] == 4
    assert result["min_reliable_n"] == 30
    assert result["reliable"] is False  # 4 < 30

    # flow = 10 * weight_delta 정확히 -> 완전 양의 상관관계.
    assert result["correlation_weight_vs_flow"] == pytest.approx(1.0)
    # price = -3 * weight_delta 정확히 -> 완전 음의 상관관계.
    assert result["correlation_weight_vs_price"] == pytest.approx(-1.0)
