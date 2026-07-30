"""시장 전체 매수세/매도세 종합 게이지 — 순수 계산부 (PLAN.md §4.6 3.6-4, §5.43).

이 모듈은 DB 무관 순수 함수만 담는다(단위테스트 대상, tests/test_sentiment.py) —
DB 조회·응답 조립은 routers/flow_rank.py의 market_sentiment 핸들러가 담당한다
(collectors/flow_path.py의 compute_flow_path/collect_flow_path 분리 패턴과 동일).
관찰용 참고 지표이지 매매 신호가 아니다(이 프로젝트 전체의 house principle —
근사치 기반 게이지일 뿐 확정된 시장 전체 정밀값이 아니다).

산식(§4.6 3.6-4 "시장 종합 매수세/매도세 게이지" + §5.43 "flow 라이브화 + 선물
요소 추가" — 등락 비율 + 현물 수급 + 선물 수급 + ETF 순유입 4개 가중 → -100~+100):

- breadth_score = (adv-dec) / (adv+dec+flat) * 100 — 등락 종목수 비율(§4.6 3.6-2
  market_breadth). 분모(전체 종목수)가 0이면 계산 불가 -> None.
- flow_score = (buy_sum-sell_sum) / (buy_sum+sell_sum) * 100 — 외인+기관 순매수
  랭킹 상위(flow_rank)의 매수 금액 합 vs 매도 금액 합 비율. 랭킹 소스가 상위 N만
  주는 근사치다(§4.6 한계). market_sentiment가 flow 요소의 EOD 폴백 전용으로만
  쓴다(§5.43 이후 — 라이브이면 아래 flow_live_score로 대체된다). 분모가 0이면 None.
- flow_live_score(individual, foreign, institution) = (foreign+institution) /
  (|individual|+|foreign|+|institution|) * 100 — breadth_score와 동일한 "순방향/
  전체활동" 비율 구조. 투자자별(개인/외국인/기관계) net_value dict 하나만 받는
  범용 함수라 두 곳에서 재사용한다(§5.43-1): (1) flow 요소 — 코스피+코스닥 현물
  전체(routers.markets._warm_flow_live)의 두 시장 net_value를 미리 합산해 넘긴
  값, (2) futures 요소(신규) — K200 선물(routers.markets._warm_futures_flow_live)
  단일 시장 net_value. 상위 랭킹 근사치였던 기존 flow_score와 달리 시장 전체
  투자자 합계를 그대로 쓰므로 근사가 아니다. 분모가 0이면 None.
- etf_score = net_inflow_sum / aum_sum * 100 — ETF 순유입 합이 운용자산(AUM) 합
  대비 몇 %인지(방향과 크기를 함께 반영). aum_sum이 0/None이면 None. 4개 요소 중
  유일하게 라이브 소스가 확인되지 않아(펀드 순유입/AUM은 일단위 보고 특성상)
  EOD 전용으로 남는다(§5.43 설계 노트).

네 요소 모두 [-100, 100] 클램프. compute_sentiment가 이 네 값을 가중평균하는데,
None인 요소는 제외하고 남은 가중치의 합이 1이 되도록 재정규화한다(예: futures만
None이면 나머지 breadth/flow/etf 가중치를 그 합이 1이 되도록 비례 재조정).
"""

from __future__ import annotations

CLAMP_MIN = -100.0
CLAMP_MAX = 100.0

# 가중치 합은 1.0이어야 한다(compute_sentiment의 재정규화 로직이 이를 전제한다).
# PLAN.md §5.43(2026-07-30) — flow 요소를 flow_rank 상위 랭킹 근사치에서 코스피+
# 코스닥 전체 수급 라이브 계산(flow_live_score)으로 교체하면서 K200 선물 요소
# (futures)를 신규 추가했다. 기존 breadth/flow/etf = 0.4/0.35/0.25 3분배에서
# breadth를 0.35로 소폭 낮추고 옛 flow 몫(0.35)을 현물/선물에 0.20씩 대등하게
# 나눴다 — 사용자가 "선물·현물이 없어 반쪽 정보였다"(외인 양손 중 한쪽만 보던
# 문제)고 지적한 맥락을 반영해 두 수급 요소를 동일 비중으로 둔 것이다.
# **이 가중치는 검증된 값이 아니라 첫 배정이다** — quant/screener.py의 WEIGHTS와
# 동일한 정직성 원칙: 백테스트로 정한 값이 아니라 합리적으로 보이는 초기 배정일
# 뿐이며, 표본이 쌓이면 재검토 대상이다.
DEFAULT_WEIGHTS: dict[str, float] = {"breadth": 0.35, "flow": 0.20, "futures": 0.20, "etf": 0.25}


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


def flow_live_score(individual: float, foreign: float, institution: float) -> float | None:
    """(foreign+institution) / (|individual|+|foreign|+|institution|) * 100,
    [-100,100] 클램프. 분모가 0이면 None(breadth_score/flow_score와 동일한
    zero-denominator 처리).

    breadth_score와 같은 "순방향(외국인+기관계 순매수 합)/전체활동(개인까지 포함한
    매매 규모)" 비율 구조다(PLAN.md §5.43-1). 코스피+코스닥 현물 전체 수급(flow
    요소 — 두 시장 investors net_value를 미리 합산해 호출)과 K200 선물 수급
    (futures 요소 — 선물은 단일 시장이라 그대로 호출) 둘 다 이 하나의 함수로
    계산한다: 산식과 zero-denominator 처리가 완전히 동일해 근사치 상위 랭킹 기반
    이던 옛 flow_score와 달리 함수를 나눌 이유가 없다."""
    denom = abs(individual) + abs(foreign) + abs(institution)
    if denom == 0:
        return None
    return round(_clamp((foreign + institution) / denom * 100), 1)


def compute_sentiment(
    breadth: float | None,
    flow: float | None,
    futures: float | None,
    etf: float | None,
    weights: dict[str, float] | None = None,
) -> tuple[float | None, dict[str, float]]:
    """breadth/flow/futures/etf 네 점수를 가중평균해 -100~+100 종합 게이지
    점수를 만든다(PLAN.md §5.43 — futures 신규 추가로 3요소에서 4요소로 확장).

    None인 요소는 평균에서 제외하고, 남은 요소들의 weight 합이 1이 되도록
    재정규화한다(예: futures만 None이면 breadth/flow/etf 0.35/0.20/0.25 ->
    그 합 0.8을 기준으로 0.4375/0.25/0.3125로 재조정). 전부 None이면
    (None, {"breadth": 0.0, "flow": 0.0, "futures": 0.0, "etf": 0.0}) 반환.

    Returns:
        (score, used_weights) — score는 round(…, 1) + [-100,100] 클램프,
        used_weights는 code -> 실제로 사용된(재정규화 후) weight(응답 투명성용,
        요소가 None이면 0.0).
    """
    w = weights if weights is not None else DEFAULT_WEIGHTS
    raw = {"breadth": breadth, "flow": flow, "futures": futures, "etf": etf}
    available = {k: v for k, v in raw.items() if v is not None}

    if not available:
        return None, {"breadth": 0.0, "flow": 0.0, "futures": 0.0, "etf": 0.0}

    weight_sum = sum(w[k] for k in available)
    used_weights = {k: (w[k] / weight_sum if k in available else 0.0) for k in raw}

    score = sum(raw[k] * used_weights[k] for k in available)
    return round(_clamp(score), 1), used_weights
