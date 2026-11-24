"""이상 거래 판정 순수 함수 (PLAN.md Phase 5.117). 표시 전용 — 매매 신호가 아니다.

종목별로 세 조건을 본다.
- 거래대금 급증: 오늘 거래대금 ÷ 직전 20거래일 평균 >= VALUE_SURGE_MULT
- 외인+기관 대량 수급: |외국인+기관계 순매수| >= 오늘 거래대금 x FLOW_SHARE_MIN 그리고 >= FLOW_ABS_MIN_MILLION
- 공매도 급증: 오늘 공매도 거래대금 ÷ 직전 20거래일 평균 >= SHORT_SURGE_MULT (오늘 값이 없으면 판정 제외)
두 조건 이상 충족하면 후보, 수급 부호로 매도 쏠림/매수 쏠림으로 나눈다. DB를 읽지 않는다.
금액 단위는 모두 백만원(DB 관례).
"""

from __future__ import annotations

# 첫 배정값 — 과거 데이터로 검증하지 않았다(PLAN.md 5.117).
VALUE_SURGE_MULT = 2.0
FLOW_SHARE_MIN = 0.10
FLOW_ABS_MIN_MILLION = 5000  # 50억원
SHORT_SURGE_MULT = 2.0
MIN_CONDITIONS = 2


def evaluate(
    today_value: float | None,
    avg_value: float | None,
    net_flow: float | None,
    short_today: float | None = None,
    short_avg: float | None = None,
) -> dict | None:
    """종목 하나를 판정한다. 거래대금·20일 평균·수급 중 하나라도 없으면 판정 불가(None)."""
    if not today_value or today_value <= 0 or not avg_value or avg_value <= 0 or net_flow is None:
        return None
    value_ratio = today_value / avg_value
    flow_share = abs(net_flow) / today_value
    value_surge = value_ratio >= VALUE_SURGE_MULT
    flow_large = flow_share >= FLOW_SHARE_MIN and abs(net_flow) >= FLOW_ABS_MIN_MILLION

    # 공매도는 오늘 값과 평균이 모두 있을 때만 판정한다. 없으면 충족 아님이 아니라 판정 제외.
    short_available = bool(short_today is not None and short_avg and short_avg > 0)
    short_ratio = short_today / short_avg if short_available else None
    short_surge = bool(short_available and short_ratio >= SHORT_SURGE_MULT)

    conditions = {"value_surge": value_surge, "flow_large": flow_large, "short_surge": short_surge}
    count = sum(conditions.values())
    direction = "buy" if net_flow > 0 else "sell" if net_flow < 0 else None
    return {
        "value_ratio": value_ratio,
        "flow_share": flow_share,
        "short_ratio": short_ratio,
        "short_available": short_available,
        "conditions": conditions,
        "condition_count": count,
        "is_candidate": count >= MIN_CONDITIONS and direction is not None,
        "direction": direction,
        "sort_key": flow_share,  # |순매수| ÷ 거래대금, 내림차순
    }
