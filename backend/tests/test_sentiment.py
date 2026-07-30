"""Unit tests for app.sentiment (순수 계산부, DB 무관) — PLAN.md §4.6 3.6-4, §5.43.

핵심 동작을 손계산 가능한 픽스처로 검증한다:
1. 네 요소·가중 평균이 정확히 계산되는지
2. 클램프가 -100/100 경계에서 동작하는지
3. 데이터 없는 요소(None)는 제외하고 나머지 가중치를 재정규화하는지(1개/여러 개 None)
4. flow_live_score(신규, §5.43-1)가 경계값·부호 혼합·zero-denominator에서 올바른지
"""

from __future__ import annotations

from app.sentiment import (
    DEFAULT_WEIGHTS,
    breadth_score,
    compute_sentiment,
    etf_score,
    flow_live_score,
    flow_score,
)


# ---------------------------------------------------------------------------
# breadth_score / flow_score / etf_score — 개별 요소 산식 + 클램프 + None 처리
# ---------------------------------------------------------------------------


def test_breadth_score_basic_ratio():
    # (600-400)/(600+400+0) * 100 = 20.0
    assert breadth_score(adv=600, dec=400, flat=0) == 20.0


def test_breadth_score_zero_denominator_is_none():
    assert breadth_score(adv=0, dec=0, flat=0) is None


def test_breadth_score_clamped_at_extremes():
    # 전부 상승, 하락 0, 보합 0 -> (100-0)/100*100 = 100 (경계값, 클램프해도 100).
    assert breadth_score(adv=1000, dec=0, flat=0) == 100.0
    assert breadth_score(adv=0, dec=1000, flat=0) == -100.0


def test_flow_score_basic_ratio():
    # (12000-8000)/(12000+8000) * 100 = 20.0
    assert flow_score(buy_sum=12000, sell_sum=8000) == 20.0


def test_flow_score_zero_denominator_is_none():
    assert flow_score(buy_sum=0, sell_sum=0) is None


def test_etf_score_basic_ratio():
    # 500 / 10000 * 100 = 5.0
    assert etf_score(net_inflow_sum=500, aum_sum=10000) == 5.0


def test_etf_score_none_when_aum_missing_or_zero():
    assert etf_score(net_inflow_sum=500, aum_sum=None) is None
    assert etf_score(net_inflow_sum=500, aum_sum=0) is None


def test_etf_score_clamped_when_ratio_exceeds_100():
    # net_inflow_sum이 aum_sum보다 커서 비율이 100을 넘는 극단치 -> 클램프.
    assert etf_score(net_inflow_sum=20000, aum_sum=10000) == 100.0
    assert etf_score(net_inflow_sum=-20000, aum_sum=10000) == -100.0


# ---------------------------------------------------------------------------
# flow_live_score(신규, §5.43-1) — 개인/외국인/기관계 net_value로 "순방향/전체
# 활동" 비율을 계산한다. flow(현물, 코스피+코스닥 합산)와 futures(선물) 두 요소가
# 이 함수 하나를 공유한다.
# ---------------------------------------------------------------------------


def test_flow_live_score_all_positive():
    # denom = 1000+2000+1500 = 4500, (2000+1500)/4500*100 = 77.777... -> 77.8
    assert flow_live_score(individual=1000, foreign=2000, institution=1500) == 77.8


def test_flow_live_score_all_negative():
    # 부호만 반대인 대칭 케이스 -> -77.8
    assert flow_live_score(individual=-1000, foreign=-2000, institution=-1500) == -77.8


def test_flow_live_score_zero_denominator_is_none():
    assert flow_live_score(individual=0, foreign=0, institution=0) is None


def test_flow_live_score_mixed_signs():
    # denom = 1000+500+300 = 1800, (-500+300)/1800*100 = -11.111... -> -11.1
    assert flow_live_score(individual=1000, foreign=-500, institution=300) == -11.1


def test_flow_live_score_clamped_at_extremes():
    # individual=0(전체활동에 개인이 전혀 없음), foreign+institution이 전부 순매수
    # 방향 -> (1000+1000)/(0+1000+1000)*100 = 100.0 (경계값, 클램프해도 100).
    assert flow_live_score(individual=0, foreign=1000, institution=1000) == 100.0


# ---------------------------------------------------------------------------
# compute_sentiment — (a) 4요소 가중평균 정확성, (b) 클램프, (c) None 재정규화
# (1개/여러 개 동시)
# ---------------------------------------------------------------------------


def test_compute_sentiment_weighted_average_all_present():
    # breadth=20, flow=-10, futures=15, etf=40, weights=0.35/0.20/0.20/0.25 (기본값)
    # = 20*0.35 + (-10)*0.20 + 15*0.20 + 40*0.25 = 7 - 2 + 3 + 10 = 18.0
    score, weights = compute_sentiment(20.0, -10.0, 15.0, 40.0)
    assert score == 18.0
    assert weights == {"breadth": 0.35, "flow": 0.20, "futures": 0.20, "etf": 0.25}


def test_compute_sentiment_clamps_final_score_at_boundaries():
    score, _weights = compute_sentiment(100.0, 100.0, 100.0, 100.0)
    assert score == 100.0
    score, _weights = compute_sentiment(-100.0, -100.0, -100.0, -100.0)
    assert score == -100.0


def test_compute_sentiment_renormalizes_when_breadth_is_none():
    # breadth만 None -> flow(0.20)/futures(0.20)/etf(0.25) 가중치를 합 1이 되도록
    # 재정규화(합 0.65): 0.20/0.65 = 0.30769..., 0.25/0.65 = 0.38461...
    score, weights = compute_sentiment(None, -10.0, 15.0, 40.0)
    assert weights["breadth"] == 0.0
    assert round(weights["flow"], 6) == round(0.20 / 0.65, 6)
    assert round(weights["futures"], 6) == round(0.20 / 0.65, 6)
    assert round(weights["etf"], 6) == round(0.25 / 0.65, 6)
    # score = -10*(0.20/0.65) + 15*(0.20/0.65) + 40*(0.25/0.65) = 16.923... -> 16.9
    assert score == 16.9


def test_compute_sentiment_renormalizes_when_futures_is_none():
    # futures만 None(§5.43 신규 요소가 없는 경우) -> breadth(0.35)/flow(0.20)/
    # etf(0.25) 가중치를 합 1이 되도록 재정규화(합 0.80).
    score, weights = compute_sentiment(20.0, -10.0, None, 40.0)
    assert weights["futures"] == 0.0
    assert round(weights["breadth"], 6) == round(0.35 / 0.80, 6)
    assert round(weights["flow"], 6) == round(0.20 / 0.80, 6)
    assert round(weights["etf"], 6) == round(0.25 / 0.80, 6)
    # score = 20*(0.35/0.8) + (-10)*(0.20/0.8) + 40*(0.25/0.8) = 18.75 -> 18.8
    assert score == 18.8


def test_compute_sentiment_renormalizes_when_two_components_are_none():
    # flow/futures가 동시에 None -> breadth(0.35)/etf(0.25)만 남아 합 1이 되도록
    # 재정규화(합 0.60): 0.35/0.6 = 0.58333..., 0.25/0.6 = 0.41666...
    score, weights = compute_sentiment(20.0, None, None, 40.0)
    assert weights["flow"] == 0.0
    assert weights["futures"] == 0.0
    assert round(weights["breadth"], 6) == round(0.35 / 0.60, 6)
    assert round(weights["etf"], 6) == round(0.25 / 0.60, 6)
    # score = 20*(0.35/0.6) + 40*(0.25/0.6) = 28.333... -> 28.3
    assert score == 28.3


def test_compute_sentiment_all_none_returns_none_score_and_zero_weights():
    score, weights = compute_sentiment(None, None, None, None)
    assert score is None
    assert weights == {"breadth": 0.0, "flow": 0.0, "futures": 0.0, "etf": 0.0}


def test_compute_sentiment_uses_default_weights_object_without_mutation():
    # 재정규화 로직이 DEFAULT_WEIGHTS 딕셔너리 자체를 변형하지 않는지 확인
    # (모듈 전역 상수를 옆에서 재사용하는 다른 호출자에게 영향 주면 안 됨).
    compute_sentiment(None, -10.0, 15.0, 40.0)
    assert DEFAULT_WEIGHTS == {"breadth": 0.35, "flow": 0.20, "futures": 0.20, "etf": 0.25}


def test_compute_sentiment_custom_weights_respected():
    score, weights = compute_sentiment(
        10.0, 10.0, 10.0, 10.0, weights={"breadth": 0.4, "flow": 0.3, "futures": 0.2, "etf": 0.1}
    )
    assert weights == {"breadth": 0.4, "flow": 0.3, "futures": 0.2, "etf": 0.1}
    assert score == 10.0
