"""시장 전체 매수세/매도세 종합 게이지 — 순수 계산부 (PLAN.md §4.6 3.6-4).

이 모듈은 DB 무관 순수 함수만 담는다(단위테스트 대상, tests/test_sentiment.py) —
DB 조회·응답 조립은 routers/flow_rank.py의 market_sentiment 핸들러가 담당한다
(collectors/flow_path.py의 compute_flow_path/collect_flow_path 분리 패턴과 동일).

산식(§4.6 3.6-4 "시장 종합 매수세/매도세 게이지: 등락 비율 + 외인·기관 순매수 합 +
ETF 순유입 합 가중 → -100~+100"):

- breadth_score = (adv-dec) / (adv+dec+flat) * 100 — 등락 종목수 비율(§4.6 3.6-2
  market_breadth). 분모(전체 종목수)가 0이면 계산 불가 -> None.
- flow_score = (buy_sum-sell_sum) / (buy_sum+sell_sum) * 100 — 외인+기관 순매수
  랭킹 상위(flow_rank)의 매수 금액 합 vs 매도 금액 합 비율. 랭킹 소스가 상위 N만
  주는 근사치다(§4.6 한계). 분모가 0이면 None.
- etf_score = net_inflow_sum / aum_sum * 100 — ETF 순유입 합이 운용자산(AUM) 합
  대비 몇 %인지(방향과 크기를 함께 반영). aum_sum이 0/None이면 None.

세 요소 모두 [-100, 100] 클램프. compute_sentiment가 이 세 값을 가중평균하는데,
None인 요소는 제외하고 남은 가중치의 합이 1이 되도록 재정규화한다(예: breadth만
None이면 flow/etf 가중치 0.35/0.25 -> 0.35/0.6, 0.25/0.6로 재조정).
"""

from __future__ import annotations

CLAMP_MIN = -100.0
CLAMP_MAX = 100.0

# 가중치 합은 1.0이어야 한다(compute_sentiment의 재정규화 로직이 이를 전제한다).
DEFAULT_WEIGHTS: dict[str, float] = {"breadth": 0.4, "flow": 0.35, "etf": 0.25}


def _clamp(value: float) -> float:
    return max(CLAMP_MIN, min(CLAMP_MAX, value))


def breadth_score(adv: int, dec: int, flat: int) -> float | None:
    """(adv-dec)/(adv+dec+flat) * 100, [-100,100] 클램프. 분모가 0이면 None."""
    denom = adv + dec + flat
    if denom == 0:
        return None
    return round(_clamp((adv - dec) / denom * 100), 1)


def flow_score(buy_sum: int, sell_sum: int) -> float | None:
    """(buy_sum-sell_sum)/(buy_sum+sell_sum) * 100, [-100,100] 클램프. 분모가 0이면 None."""
    denom = buy_sum + sell_sum
    if denom == 0:
        return None
    return round(_clamp((buy_sum - sell_sum) / denom * 100), 1)


def etf_score(net_inflow_sum: int, aum_sum: int | None) -> float | None:
    """net_inflow_sum/aum_sum * 100 (AUM 대비 순유입 비율), [-100,100] 클램프.
    aum_sum이 None이거나 0이면 None(비율을 정의할 수 없음)."""
    if not aum_sum:
        return None
    return round(_clamp(net_inflow_sum / aum_sum * 100), 1)


def compute_sentiment(
    breadth: float | None,
    flow: float | None,
    etf: float | None,
    weights: dict[str, float] | None = None,
) -> tuple[float | None, dict[str, float]]:
    """breadth/flow/etf 세 점수를 가중평균해 -100~+100 종합 게이지 점수를 만든다.

    None인 요소는 평균에서 제외하고, 남은 요소들의 weight 합이 1이 되도록
    재정규화한다(예: breadth만 None이면 flow 0.35/etf 0.25 -> 0.35/0.6과 0.25/0.6로
    재조정). 전부 None이면 (None, {"breadth": 0.0, "flow": 0.0, "etf": 0.0}) 반환.

    Returns:
        (score, used_weights) — score는 round(…, 1) + [-100,100] 클램프,
        used_weights는 code -> 실제로 사용된(재정규화 후) weight(응답 투명성용,
        요소가 None이면 0.0).
    """
    w = weights if weights is not None else DEFAULT_WEIGHTS
    raw = {"breadth": breadth, "flow": flow, "etf": etf}
    available = {k: v for k, v in raw.items() if v is not None}

    if not available:
        return None, {"breadth": 0.0, "flow": 0.0, "etf": 0.0}

    weight_sum = sum(w[k] for k in available)
    used_weights = {k: (w[k] / weight_sum if k in available else 0.0) for k in raw}

    score = sum(raw[k] * used_weights[k] for k in available)
    return round(_clamp(score), 1), used_weights
