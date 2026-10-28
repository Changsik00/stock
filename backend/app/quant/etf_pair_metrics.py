"""코스닥150 레버리지/인버스 ETF 쌍의 괴리 지표 순수 계산 (PLAN.md §5.88-1).

**관찰 전용** — 여기서 나오는 값은 "ETF 가격이 NAV·기초지수 이론가 대비 어디에
있는지"를 숫자로 적어 둔 것일 뿐 매매 신호가 아니다(§5 house rule: 관찰
데이터만 제공, 판단은 사용자 몫). 추후 진입 필터로 쓸 만한지는 분포가 쌓인 뒤
사용자가 판단한다.

- **괴리율(premium_pct)** = (현재가 - NAV) / NAV * 100.
- **이론가(theory_price)** = 전일 기준가 * (1 + 배수 * 기초지수 등락률/100). 일일
  리밸런싱형 2X 상품의 "오늘 하루" 근사치다(복리·수수료·괴리 무시).
- **시차(lag_pct)** = (현재가 - 이론가) / 이론가 * 100. 양수면 이론가보다 비싸게,
  음수면 싸게 거래 중이라는 산술적 사실만 의미한다.

입력이 None/NaN/비정상(NAV·이론가 ≤ 0 등)이면 예외 없이 None을 돌려준다
(`quant/volume_profile.py`와 같은 방어적 태도 — 지표 하나 때문에 기록기가
죽으면 안 된다).
"""

from __future__ import annotations

import math

# 한 곳에서만 정의하는 대상 상수 — collectors/etf_pair_sampler.py와
# routers/etf_pair.py가 공유한다.
UNDERLYING_CODE = "150"  # 키움 업종코드: 코스닥150 지수(기초지수)
# KODEX 코스닥150레버리지(+2X) / KODEX 코스닥150선물인버스(-1X)
PAIR_ETFS: dict[str, int] = {"233740": 2, "251340": -1}

# 표시용 이름 (키 집합은 PAIR_ETFS와 동일해야 함 — 테스트로 검증)
PAIR_ETF_NAMES: dict[str, str] = {
    "233740": "KODEX 코스닥150레버리지",
    "251340": "KODEX 코스닥150선물인버스",
}


def _finite(x: float | int | None) -> float | None:
    """None/NaN/inf를 걸러 float로 정규화한다."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def compute_premium_pct(price: float | None, nav: float | None) -> float | None:
    """괴리율(%) = (price - nav) / nav * 100. nav가 없거나 0 이하면 None."""
    p, n = _finite(price), _finite(nav)
    if p is None or n is None or n <= 0:
        return None
    return (p - n) / n * 100


def compute_theory_price(
    base_price: float | None, multiple: float | None, underlying_change_rate: float | None
) -> float | None:
    """기초지수 등락률(%) 기준 ETF 이론가 = base * (1 + multiple * rate/100)."""
    b, m, r = _finite(base_price), _finite(multiple), _finite(underlying_change_rate)
    if b is None or m is None or r is None or b <= 0:
        return None
    return b * (1 + m * r / 100)


def compute_lag_pct(price: float | None, theory_price: float | None) -> float | None:
    """시차(%) = (price - theory) / theory * 100. 이론가가 없거나 0 이하면 None."""
    p, t = _finite(price), _finite(theory_price)
    if p is None or t is None or t <= 0:
        return None
    return (p - t) / t * 100


def _percentile(sorted_vals: list[float], q: float) -> float:
    """선형 보간 백분위(q: 0~100). 입력은 비어 있지 않은 오름차순 리스트."""
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * q / 100
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def summarize_distribution(values: list[float | None]) -> dict | None:
    """None을 뺀 표본의 {mean,p5,p95,min,max}(소수 4자리). 표본이 없으면 None.
    표본이 적을 때 p5/p95는 사실상 min/max에 가까워진다 — 호출부가 count를
    함께 내려 과장을 막는다(§5.40 관례)."""
    vals = sorted(v for v in (_finite(x) for x in values) if v is not None)
    if not vals:
        return None
    return {
        "mean": round(sum(vals) / len(vals), 4),
        "p5": round(_percentile(vals, 5), 4),
        "p95": round(_percentile(vals, 95), 4),
        "min": round(vals[0], 4),
        "max": round(vals[-1], 4),
    }
