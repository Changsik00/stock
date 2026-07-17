"""Unit tests for app.sentiment (순수 계산부, DB 무관) — PLAN.md §4.6 3.6-4.

세 가지 핵심 동작을 손계산 가능한 픽스처로 검증한다:
1. 세 요소·가중 평균이 정확히 계산되는지
2. 클램프가 -100/100 경계에서 동작하는지
3. 데이터 없는 요소(None)는 제외하고 나머지 가중치를 재정규화하는지
"""

from __future__ import annotations

from app.sentiment import (
    DEFAULT_WEIGHTS,
    breadth_score,
    compute_sentiment,
    etf_score,
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
# compute_sentiment — (a) 가중평균 정확성, (b) 클램프, (c) None 재정규화
# ---------------------------------------------------------------------------


def test_compute_sentiment_weighted_average_all_present():
    # breadth=20, flow=-10, etf=40, weights=0.4/0.35/0.25 (기본값)
    # = 20*0.4 + (-10)*0.35 + 40*0.25 = 8 - 3.5 + 10 = 14.5
    score, weights = compute_sentiment(20.0, -10.0, 40.0)
    assert score == 14.5
    assert weights == {"breadth": 0.4, "flow": 0.35, "etf": 0.25}


def test_compute_sentiment_clamps_final_score_at_boundaries():
    score, _weights = compute_sentiment(100.0, 100.0, 100.0)
    assert score == 100.0
    score, _weights = compute_sentiment(-100.0, -100.0, -100.0)
    assert score == -100.0


def test_compute_sentiment_renormalizes_when_one_component_is_none():
    # breadth만 None -> flow(0.35)/etf(0.25) 가중치를 합 1이 되도록 재정규화:
    # 0.35/0.6 = 0.58333..., 0.25/0.6 = 0.41666...
    score, weights = compute_sentiment(None, -10.0, 40.0)
    assert weights["breadth"] == 0.0
    assert round(weights["flow"], 6) == round(0.35 / 0.6, 6)
    assert round(weights["etf"], 6) == round(0.25 / 0.6, 6)
    # score = -10*(0.35/0.6) + 40*(0.25/0.6) = -5.8333... + 16.6666... = 10.8333... -> 10.8
    assert score == 10.8


def test_compute_sentiment_renormalizes_when_two_components_are_none():
    # flow/etf가 None -> breadth 가중치만 남아 1.0으로 재정규화, score는 breadth 그대로.
    score, weights = compute_sentiment(25.0, None, None)
    assert weights == {"breadth": 1.0, "flow": 0.0, "etf": 0.0}
    assert score == 25.0


def test_compute_sentiment_all_none_returns_none_score_and_zero_weights():
    score, weights = compute_sentiment(None, None, None)
    assert score is None
    assert weights == {"breadth": 0.0, "flow": 0.0, "etf": 0.0}


def test_compute_sentiment_uses_default_weights_object_without_mutation():
    # 재정규화 로직이 DEFAULT_WEIGHTS 딕셔너리 자체를 변형하지 않는지 확인
    # (모듈 전역 상수를 옆에서 재사용하는 다른 호출자에게 영향 주면 안 됨).
    compute_sentiment(None, -10.0, 40.0)
    assert DEFAULT_WEIGHTS == {"breadth": 0.4, "flow": 0.35, "etf": 0.25}


def test_compute_sentiment_custom_weights_respected():
    score, weights = compute_sentiment(10.0, 10.0, 10.0, weights={"breadth": 0.5, "flow": 0.3, "etf": 0.2})
    assert weights == {"breadth": 0.5, "flow": 0.3, "etf": 0.2}
    assert score == 10.0
