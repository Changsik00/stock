"""Unit tests for app.quant.concentration_backtest (PLAN.md §5.23, 코스피/코스닥
쏠림 비율 백테스트).

test_regime_backtest.py와 같은 하우스 패턴(실 dev Postgres, app.db.
async_session_factory)을 쓰지만 격리 방식은 다르다: `concentration_backtest`의
DB 함수들은 (regime_backtest와 달리) market 이름을 인자로 받지 않고 "kospi"/
"kosdaq"을 직접 하드코딩한다 — 라이브 §5.18/§5.19 지표를 그대로 백테스트하는
게 목적이라 가짜 market 문자열로 바꿔치기할 수 없기 때문이다. 그래서 이 파일은
`test_scalp_tracker.py`/`test_intraday_snapshot.py`가 이미 쓰는 관례를 따라
"실 데이터와 절대 겹치지 않는 먼 미래 날짜 범위"(2099-06-01부터 25일)로
격리하고, 그 날짜 범위만 정확히 delete해서 정리한다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.db import async_session_factory, engine
from app.models import IndexOhlcv, MarketFlow
from app.quant import concentration_backtest

# ---------------------------------------------------------------------------
# DB 픽스처 — 25거래일(2099-06-01 ~ 2099-06-25), 실데이터와 절대 겹치지 않는
# 먼 미래 날짜 범위. index 0~19(20일)는 "평소" 베이스라인 구간(코스피 활동량
# 100/일, 코스닥 50/일 고정), index 20~23(4일)은 각각 4구간(코스피강쏠림/
# 코스피약쏠림/코스닥약쏠림/코스닥강쏠림)에 정확히 하나씩 떨어지도록 손으로
# 골라낸 활동량 값이다. index 24는 index 23의 "다음날 수익률"을 계산하기 위한
# 종가만 필요한 마감일이라 MarketFlow 없이 IndexOhlcv만 심는다.
# ---------------------------------------------------------------------------

DATES = [dt.date(2099, 6, 1) + dt.timedelta(days=i) for i in range(25)]  # index 0~24

# 코스피 활동량(외국인+기관계 |net_value| 합): 0~19일은 100 고정, 20~23일은
# 아래 값(하단 주석에 버킷별 손계산 근거 기록).
KOSPI_ACTIVITY = {i: 100.0 for i in range(20)}
KOSPI_ACTIVITY.update({20: 200.0, 21: 130.0, 22: 87.0, 23: 35.0})

# 코스닥 활동량은 24일 내내 50 고정 — 코스닥 배율이 항상 1.0이 되어 쏠림%가
# 오직 코스피 배율에만 좌우되므로 4구간을 손으로 고르기 쉬워진다.
KOSDAQ_ACTIVITY = {i: 50.0 for i in range(24)}

# 손계산(및 아래 스크립트로 재확인한) 각 테스트일의 롤링 20일 베이스라인/배율/쏠림%:
#   day20: baseline_k=mean(day0..19)=100.0, baseline_q=50.0
#          km=200/100=2.0, qm=50/50=1.0 -> conc=2.0/3.0*100=66.667  (코스피강쏠림, >=60)
#   day21: baseline_k=mean(day1..20)=(19*100+200)/20=105.0, baseline_q=50.0
#          km=130/105=1.238..., qm=1.0 -> conc=55.319  (코스피약쏠림, [50,60))
#   day22: baseline_k=mean(day2..21)=(18*100+200+130)/20=106.5, baseline_q=50.0
#          km=87/106.5=0.817..., qm=1.0 -> conc=44.961  (코스닥약쏠림, [40,50))
#   day23: baseline_k=mean(day3..22)=(17*100+200+130+87)/20=105.85, baseline_q=50.0
#          km=35/105.85=0.331..., qm=1.0 -> conc=24.849  (코스닥강쏠림, <40)
EXPECTED_CONCENTRATION = {
    20: pytest.approx(66.66667, abs=1e-3),
    21: pytest.approx(55.31915, abs=1e-3),
    22: pytest.approx(44.96124, abs=1e-3),
    23: pytest.approx(24.84913, abs=1e-3),
}
EXPECTED_BUCKET_BY_INDEX = {
    20: "코스피강쏠림",
    21: "코스피약쏠림",
    22: "코스닥약쏠림",
    23: "코스닥강쏠림",
}

# index 0~19 종가는 버킷 계산에 쓰이지 않아(베이스라인 미완성 구간) 임의의
# 상수값으로 채운다. index 20~24는 각 테스트일의 "다음날 수익률"을 손으로
# 검증하기 위한 값(아래 EXPECTED_RETURNS 주석 참고).
KOSPI_CLOSE = {i: 1000.0 for i in range(20)}
KOSPI_CLOSE.update({20: 1000.0, 21: 1020.0, 22: 1010.0, 23: 1050.0, 24: 1040.0})
KOSDAQ_CLOSE = {i: 500.0 for i in range(20)}
KOSDAQ_CLOSE.update({20: 500.0, 21: 490.0, 22: 510.0, 23: 505.0, 24: 515.0})

# 다음날 수익률(%) 손계산 (round 3자리, regime_backtest._daily_returns와 동일 정의):
#   day20: kospi (1020-1000)/1000*100=+2.0,      kosdaq (490-500)/500*100=-2.0
#   day21: kospi (1010-1020)/1020*100=-0.980,    kosdaq (510-490)/490*100=+4.082
#   day22: kospi (1050-1010)/1010*100=+3.960,    kosdaq (505-510)/510*100=-0.980
#   day23: kospi (1040-1050)/1050*100=-0.952,    kosdaq (515-505)/505*100=+1.980
EXPECTED_RETURNS = {
    20: (2.0, -2.0),
    21: (-0.980, 4.082),
    22: (3.960, -0.980),
    23: (-0.952, 1.980),
}


def _split(value: float) -> tuple[int, int]:
    """활동량을 외국인/기관계 두 정수 net_value로 나눈다(BigInteger 컬럼이라
    정수 필요) — 둘 다 양수로 나누면 abs 합이 원래 값과 정확히 같다."""
    half = int(value) // 2
    return half, int(value) - half


async def _clear_test_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(
            IndexOhlcv.__table__.delete().where(
                IndexOhlcv.market.in_(("kospi", "kosdaq")), IndexOhlcv.date.in_(DATES)
            )
        )
        await session.execute(
            MarketFlow.__table__.delete().where(
                MarketFlow.market.in_(("kospi", "kosdaq")), MarketFlow.date.in_(DATES)
            )
        )
        await session.commit()


async def _seed() -> None:
    async with async_session_factory() as session:
        for i, d in enumerate(DATES):
            kc = KOSPI_CLOSE[i]
            session.add(IndexOhlcv(market="kospi", date=d, open=kc, high=kc, low=kc, close=kc))
            qc = KOSDAQ_CLOSE[i]
            session.add(IndexOhlcv(market="kosdaq", date=d, open=qc, high=qc, low=qc, close=qc))

        for i in range(24):  # index 24는 MarketFlow 없음(마감 종가만 필요)
            d = DATES[i]
            kf, ki = _split(KOSPI_ACTIVITY[i])
            session.add(MarketFlow(market="kospi", date=d, investor="외국인", net_value=kf, source="test"))
            session.add(MarketFlow(market="kospi", date=d, investor="기관계", net_value=ki, source="test"))
            qf, qi = _split(KOSDAQ_ACTIVITY[i])
            session.add(MarketFlow(market="kosdaq", date=d, investor="외국인", net_value=qf, source="test"))
            session.add(MarketFlow(market="kosdaq", date=d, investor="기관계", net_value=qi, source="test"))
        await session.commit()


@pytest.fixture(autouse=True)
async def _fixture_data():
    await _clear_test_rows()
    await _seed()
    yield
    await _clear_test_rows()
    await engine.dispose()


# ---------------------------------------------------------------------------
# 순수 함수 (DB 무관)
# ---------------------------------------------------------------------------


def test_rolling_baseline_insufficient_history_is_none():
    dates = [dt.date(2050, 1, 1) + dt.timedelta(days=i) for i in range(25)]
    activity = {d: float(i) for i, d in enumerate(dates)}  # 0,1,2,...,24

    result = concentration_backtest._rolling_baseline(dates, activity, window=20)

    for i in range(20):
        assert result[dates[i]] is None


def test_rolling_baseline_uses_exact_preceding_window():
    dates = [dt.date(2050, 1, 1) + dt.timedelta(days=i) for i in range(25)]
    activity = {d: float(i) for i, d in enumerate(dates)}  # 0,1,2,...,24

    result = concentration_backtest._rolling_baseline(dates, activity, window=20)

    # dates[20]의 베이스라인 = mean(dates[0..19]의 값) = mean(0..19) = 9.5
    assert result[dates[20]] == pytest.approx(9.5)
    # dates[24]의 베이스라인 = mean(dates[4..23]의 값) = mean(4..23) = 13.5
    assert result[dates[24]] == pytest.approx(13.5)


def test_rolling_baseline_never_uses_current_or_future_dates():
    """핵심 무결성 속성: D일의 베이스라인은 D 자신이나 D 이후 값에 의존하지
    않는다 — 이 값들을 극단적으로 바꿔도 이미 계산된 베이스라인이 변하지
    않아야 한다. pandas rolling 등을 잘못 정렬해 현재/미래 값이 창에 섞여
    들어가는 실수를 잡기 위한 테스트."""
    dates = [dt.date(2050, 1, 1) + dt.timedelta(days=i) for i in range(25)]
    activity = {d: float(i) for i, d in enumerate(dates)}

    baseline_before = concentration_backtest._rolling_baseline(dates, activity, window=20)

    mutated = dict(activity)
    target = dates[20]
    for d in dates[20:]:  # target 자신 포함, 그 이후 전부
        mutated[d] = 999_999.0  # 극단적인 값으로 오염시켜본다

    baseline_after = concentration_backtest._rolling_baseline(dates, mutated, window=20)

    assert baseline_after[target] == baseline_before[target]
    assert baseline_after[target] == pytest.approx(9.5)  # mean(0..19), 오염 전과 동일


def test_bucket_label_boundaries():
    assert concentration_backtest._bucket_label(60) == "코스피강쏠림"
    assert concentration_backtest._bucket_label(59.999) == "코스피약쏠림"
    assert concentration_backtest._bucket_label(50) == "코스피약쏠림"
    assert concentration_backtest._bucket_label(49.999) == "코스닥약쏠림"
    assert concentration_backtest._bucket_label(40) == "코스닥약쏠림"
    assert concentration_backtest._bucket_label(39.999) == "코스닥강쏠림"


# ---------------------------------------------------------------------------
# DB 기반 계산 (고정 픽스처, 2099-06 먼 미래 날짜로 실데이터와 격리)
# ---------------------------------------------------------------------------


async def test_compute_daily_concentration_series_matches_hand_computed_values():
    # dev DB에는 이미 실 kospi/kosdaq market_flow가 2023년부터 쌓여 있어,
    # `_rolling_baseline`이 "index 0~19는 히스토리 부족으로 None"이라는 가정은
    # 이 함수 전체 결과에는 성립하지 않는다(2099-06-01 이전에도 실데이터가
    # 잔뜩 있으므로 index 0도 실데이터 기준 베이스라인을 얻는다). 하지만 우리
    # 테스트일(index 20~23)의 20일 윈도우는 바로 앞 20개 항목, 즉 우리가 심은
    # index 0~19(전부 우리 합성 데이터, 2026년 실데이터와 2099-06 사이에는
    # 아무 날짜도 없으므로 실데이터가 섞일 수 없다)만 가리키므로 그 4개 날짜의
    # concentration_pct는 여전히 손계산과 정확히 일치해야 한다 — 이 4개 값만
    # 검증한다(전체 시계열에서 우리 구간 이외 날짜의 존재/부재는 검증 대상이
    # 아니다).
    async with async_session_factory() as session:
        series = await concentration_backtest.compute_daily_concentration_series(session)

    by_date = {p["date"]: p["concentration_pct"] for p in series}

    for i in (20, 21, 22, 23):
        d = DATES[i]
        assert d in by_date
        assert by_date[d] == EXPECTED_CONCENTRATION[i]


# compute_concentration_buckets는 market 파라미터가 없고 "kospi"/"kosdaq"
# 전체 히스토리를 그대로 집계한다(regime_backtest의 함수들과 달리 가짜
# market 이름으로 바꿔치기할 수 없다) — 그래서 dev DB의 실 3년치 데이터가
# 그대로 버킷 표본수(n)에 섞여 들어가 "n==1" 같은 정확한 손계산 비교가
# 불가능하다(실측: 위 4개 합성일을 심어도 "코스피강쏠림" 버킷의 n이 실데이터
# 때문에 200을 넘었다). 그래서 이 함수의 버킷 집계/평균/스킵 로직 자체는
# 의존 함수(`compute_daily_concentration_series`, `regime_backtest.
# _daily_returns`)를 monkeypatch로 완전히 대체한 작은 합성 데이터로
# 격리해서 검증한다 — 위 DB 기반 테스트가 이미 `compute_daily_concentration_series`
# 개별 날짜 계산의 정확성(진짜 DB/롤링 베이스라인 포함)을 확인했으니, 여기서는
# 그 위에 얹힌 순수 집계 로직(버킷 분류, 평균, 상승확률, skip-if-missing)만
# 별도로 검증하는 분업이다.
async def test_compute_concentration_buckets_matches_hand_computed_values(monkeypatch):
    d1, d2, d3, d4, d5, d_no_return = (dt.date(2050, 1, 1) + dt.timedelta(days=i) for i in range(6))

    fake_series = [
        {"date": d1, "concentration_pct": 70.0},  # 코스피강쏠림
        {"date": d5, "concentration_pct": 65.0},  # 코스피강쏠림 (표본 2개로 평균 검증)
        {"date": d2, "concentration_pct": 55.0},  # 코스피약쏠림
        {"date": d3, "concentration_pct": 45.0},  # 코스닥약쏠림
        {"date": d4, "concentration_pct": 20.0},  # 코스닥강쏠림
        {"date": d_no_return, "concentration_pct": 55.0},  # 수익률 없음 -> 스킵되어야 함
    ]
    fake_kospi_returns = {d1: 1.0, d2: -2.0, d3: 3.0, d4: -4.0, d5: 2.0}  # d_no_return 없음
    fake_kosdaq_returns = {d1: -1.0, d2: 2.0, d3: -3.0, d4: 4.0, d5: -0.5}  # d_no_return 없음

    async def fake_compute_daily_concentration_series(session):
        return fake_series

    async def fake_daily_returns(session, market):
        return fake_kospi_returns if market == "kospi" else fake_kosdaq_returns

    monkeypatch.setattr(
        concentration_backtest, "compute_daily_concentration_series", fake_compute_daily_concentration_series
    )
    monkeypatch.setattr(concentration_backtest.regime_backtest, "_daily_returns", fake_daily_returns)

    buckets = await concentration_backtest.compute_concentration_buckets(session=None)
    by_label = {b["bucket"]: b for b in buckets}

    # 코스피강쏠림: d1(k=1.0,q=-1.0) + d5(k=2.0,q=-0.5) -> n=2
    b = by_label["코스피강쏠림"]
    assert b["n"] == 2
    assert b["avg_kospi_return_pct"] == pytest.approx(1.5)
    assert b["avg_kosdaq_return_pct"] == pytest.approx(-0.75)
    assert b["avg_relative_return_pct"] == pytest.approx(2.25)
    assert b["kospi_positive_rate_pct"] == 100.0
    assert b["kosdaq_positive_rate_pct"] == 0.0
    assert b["kospi_outperforms_rate_pct"] == 100.0

    # 코스피약쏠림: d2만(k=-2.0,q=2.0) -> n=1. d_no_return(같은 버킷 경계, 55.0)은
    # 수익률이 없어 스킵되어야 하므로 n이 2가 아니라 1이어야 한다.
    b = by_label["코스피약쏠림"]
    assert b["n"] == 1
    assert b["avg_kospi_return_pct"] == pytest.approx(-2.0)
    assert b["avg_kosdaq_return_pct"] == pytest.approx(2.0)
    assert b["avg_relative_return_pct"] == pytest.approx(-4.0)
    assert b["kospi_positive_rate_pct"] == 0.0
    assert b["kosdaq_positive_rate_pct"] == 100.0
    assert b["kospi_outperforms_rate_pct"] == 0.0

    # 코스닥약쏠림: d3만(k=3.0,q=-3.0)
    b = by_label["코스닥약쏠림"]
    assert b["n"] == 1
    assert b["avg_kospi_return_pct"] == pytest.approx(3.0)
    assert b["avg_kosdaq_return_pct"] == pytest.approx(-3.0)
    assert b["avg_relative_return_pct"] == pytest.approx(6.0)
    assert b["kospi_positive_rate_pct"] == 100.0
    assert b["kosdaq_positive_rate_pct"] == 0.0
    assert b["kospi_outperforms_rate_pct"] == 100.0

    # 코스닥강쏠림: d4만(k=-4.0,q=4.0)
    b = by_label["코스닥강쏠림"]
    assert b["n"] == 1
    assert b["avg_kospi_return_pct"] == pytest.approx(-4.0)
    assert b["avg_kosdaq_return_pct"] == pytest.approx(4.0)
    assert b["avg_relative_return_pct"] == pytest.approx(-8.0)
    assert b["kospi_positive_rate_pct"] == 0.0
    assert b["kosdaq_positive_rate_pct"] == 100.0
    assert b["kospi_outperforms_rate_pct"] == 0.0

    assert [b["bucket"] for b in buckets] == concentration_backtest.CONCENTRATION_BUCKET_ORDER


async def test_compute_concentration_buckets_empty_input_all_buckets_none(monkeypatch):
    """`regime_backtest.compute_streak_buckets`의 빈 버킷 관례(n=0, 모든 수치
    필드 None)와 정확히 일치하는지 확인 — 입력 자체를 monkeypatch로 완전히
    비워 실 DB 데이터의 영향을 배제한다."""

    async def empty_series(session):
        return []

    async def empty_returns(session, market):
        return {}

    monkeypatch.setattr(concentration_backtest, "compute_daily_concentration_series", empty_series)
    monkeypatch.setattr(concentration_backtest.regime_backtest, "_daily_returns", empty_returns)

    buckets = await concentration_backtest.compute_concentration_buckets(session=None)

    assert len(buckets) == 4
    for b in buckets:
        assert b["n"] == 0
        assert b["avg_kospi_return_pct"] is None
        assert b["kospi_positive_rate_pct"] is None
        assert b["avg_kosdaq_return_pct"] is None
        assert b["kosdaq_positive_rate_pct"] is None
        assert b["avg_relative_return_pct"] is None
        assert b["kospi_outperforms_rate_pct"] is None
