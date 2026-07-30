"""코스피/코스닥 개별 flow_live_score의 "과거 대비 높낮이" 베이스라인 (PLAN.md §5.44-2).

**배경**: §5.43이 `flow` 요소를 코스피+코스닥 합산 라이브 점수 하나로 만들었는데,
사용자가 "현물, 즉 코스피 기준으로 지금 더 높은지 낮은지는 알 수 있을가?"라고
물었다 — 합산 점수만으로는 "코스피만 보면 지금이 평소보다 쏠린 편인지"를 답할 수
없다. 이 모듈은 시장별(코스피/코스닥) `flow_live_score`를 그 시장 **자기 자신의**
최근 거래일 분포와 비교해 percentile을 매긴다. §5.44 스코프 노트 그대로 종합
게이지 -100~100 산식은 이 모듈과 무관하다 — 여기서 계산하는 값은 `flow`
요소의 상세 표시용 부가 정보일 뿐이다.

**베이스라인 방법론 — §5.19/§5.33과 동일한 "자기 과거, 오늘 제외" 원칙**:
`quant/regime_backtest.py::compute_activity_baseline`/`quant/sector_rotation.py`가
이미 쓰는 관례를 그대로 재사용한다 — 오늘(비교 대상) 자신이 자기 베이스라인에
섞여 들어가면 "오늘이 오늘 대비 항상 중간"이라는 착시가 생기므로, 베이스라인은
비교 기준일(`as_of`, 기본값은 현재 KST 날짜) **미만**의 `market_flow` 확정치만
쓴다. `market_flow`는 18시 배치 확정치만 담으므로(§5.19 동일 전제) 이 필터는
사실상 "오늘 배치가 이미 들어왔더라도 그 값은 baseline이 아니라 오늘 라이브
점수와 비교당하는 쪽"이라는 의도를 명확히 한다.

`as_of`를 실행 시점의 실제 날짜(`dt.datetime.now(KST).date()`)로 고정하지 않고
파라미터로 받는 이유는 오직 테스트 격리 때문이다(§5.25/§5.26류 테스트가 미래
날짜로 실데이터와 겹치지 않게 하는 것과 동일한 목적) — 운영 코드 경로는 항상
기본값(현재 날짜)을 쓴다.

**percentile 공식 — `quant/flow_percentile.py`와 동일한 표준 percentile rank
공식을 재사용**하되(동점 평균 처리, `(작은 개수 + 0.5*같은 개수) / n * 100`),
비교 대상(오늘 라이브 점수)을 모집단(과거 N일)에 포함시키지 않는다는 점만
다르다 — `flow_percentile.py`는 "오늘 하루 안에서 여러 종목"을 서로 비교하지만,
이 모듈은 "오늘 하루"를 "과거 여러 날"과 비교하므로 오늘 자신을 모집단에
넣으면 표본 크기가 날짜마다 달라지는 비일관성이 생긴다. 값이 클수록(더 큰
+ 방향 점수) percentile이 높다 — 100에 가까울수록 최근 N거래일 중 외국인+기관
순매수 쏠림이 가장 강했던 축에 속한다는 뜻이다.

**표본 부족 임계값 — MIN_BASELINE_DAYS = 10 (요청 lookback_days=20의 절반)**:
`quant/sector_rotation.py::MIN_BASELINE_DAYS`(5, 20일 창의 1/4)보다 엄격하게
잡았다 — 그쪽은 업종별로 나눠도 표본이 상대적으로 안정적이지만, 이 모듈은
시장 전체 단일 계열값(코스피 또는 코스닥 하나) percentile이라 이상치 하루가
전체 순위를 크게 흔들 수 있어(§5.38 `MIN_SAMPLES_PER_TIER`처럼 "표본이 너무
적으면 percentile 자체가 의미 없다"는 동일 판단) 더 보수적인 절반 기준을
택했다. 미만이면 `reason`에 담아 정직하게 실패한다(§5.26/§5.30/§5.33/§5.38
"표본 부족은 조용히 감추지 않고 사유를 노출" 관례).

**순수/세션 분리 — `quant/scalp_hitrate.py`/`quant/expiry_pattern.py`와 동일한
이유**: percentile 산식 자체(``today_score`` + 과거 점수 리스트 -> percentile)는
DB와 무관하므로 ``aggregate_flow_baseline``으로 분리해 synthetic 리스트로 바로
단위테스트한다. ``compute_flow_market_baseline``(세션 기반)은 `market_flow`를
읽어 일별 `flow_live_score`를 재계산해 리스트로 만든 뒤 그 순수 함수에 넘기는
얇은 래퍼일 뿐이다.

**이것은 관찰 지표다 — 매매 신호도 예측도 아니다**: §5.19/§5.33/§5.38과 동일한
house rule. "오늘이 최근 N거래일 대비 몇 번째 percentile"이라는 사실만 말할 뿐,
"그래서 오를 것"이라는 근거는 전혀 없다.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..market_hours import KST
from ..models import MarketFlow
from ..sentiment import flow_live_score

DEFAULT_LOOKBACK_DAYS = 20

# 모듈 docstring "표본 부족 임계값" 절 참고 — 요청 lookback_days(기본 20)의 절반.
MIN_BASELINE_DAYS = 10

# market_flow.investor 3분류 라벨 — routers/flow_rank.py의 동일 상수와 같은 문자열
# (collectors/market_flow.py가 채우는 정확한 한국어 표기).
_INVESTOR_INDIVIDUAL = "개인"
_INVESTOR_FOREIGN = "외국인"
_INVESTOR_INSTITUTION = "기관계"
_INVESTORS = (_INVESTOR_INDIVIDUAL, _INVESTOR_FOREIGN, _INVESTOR_INSTITUTION)


def _percentile_rank(today_score: float, historical_scores: list[float]) -> float:
    """모듈 docstring "percentile 공식" 절의 표준 percentile rank 공식
    (동점 평균 처리) — ``today_score``는 ``historical_scores`` 모집단에
    포함되지 않는다(호출자가 이미 별도로 관리하는 값). 반환값 소수 1자리."""
    n = len(historical_scores)
    below = sum(1 for x in historical_scores if x < today_score)
    equal = sum(1 for x in historical_scores if x == today_score)
    return round((below + 0.5 * equal) / n * 100, 1)


def aggregate_flow_baseline(
    today_score: float | None,
    historical_scores: list[float],
    lookback_days_requested: int = DEFAULT_LOOKBACK_DAYS,
    min_days: int = MIN_BASELINE_DAYS,
) -> dict[str, Any]:
    """오늘 라이브 ``flow_live_score``(``today_score``)를 ``historical_scores``
    (그 시장의 과거 거래일별 flow_live_score 리스트, 오늘 미포함) 분포와
    비교한다 — DB 무관 순수 함수(단위테스트 대상, 모듈 docstring "순수/세션
    분리" 참고).

    ``today_score``가 None이면(그날 라이브 활동량 0 등으로 flow_live_score
    자체가 계산 불가) 비교할 대상이 없으므로 표본과 무관하게 실패 처리한다.
    ``historical_scores``개수가 ``min_days`` 미만이면 표본 부족으로 실패
    처리한다(모듈 docstring "표본 부족 임계값" 절).

    Returns ``{"reason": str|None, "mean_score": float|None,
    "percentile": float|None, "lookback_days_requested": int,
    "lookback_days_used": int}`` — 실패 시 ``mean_score``/``percentile``은
    None이고 ``reason``에 사유, 성공 시 ``reason``은 None.
    """
    n = len(historical_scores)
    if today_score is None:
        return {
            "reason": "오늘 라이브 flow 점수를 계산할 수 없어 베이스라인과 비교 불가(활동량 0 등)",
            "mean_score": None,
            "percentile": None,
            "lookback_days_requested": lookback_days_requested,
            "lookback_days_used": n,
        }

    if n < min_days:
        return {
            "reason": (
                f"과거 확정 거래일 표본 {n}개 — 최소 {min_days}개(요청 "
                f"{lookback_days_requested}일)에 못 미침"
            ),
            "mean_score": None,
            "percentile": None,
            "lookback_days_requested": lookback_days_requested,
            "lookback_days_used": n,
        }

    return {
        "reason": None,
        "mean_score": round(sum(historical_scores) / n, 1),
        "percentile": _percentile_rank(today_score, historical_scores),
        "lookback_days_requested": lookback_days_requested,
        "lookback_days_used": n,
    }


async def compute_flow_market_baseline(
    session: AsyncSession,
    market: str,
    today_score: float | None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    min_days: int = MIN_BASELINE_DAYS,
    as_of: dt.date | None = None,
) -> dict[str, Any]:
    """``market``(``"kospi"``/``"kosdaq"``)의 `market_flow` 확정치에서 ``as_of``
    (기본값: 현재 KST 날짜) **미만**의 최근 ``lookback_days``거래일을 모아 날짜별
    ``flow_live_score``를 재계산하고, ``aggregate_flow_baseline``으로
    ``today_score``의 percentile을 매긴다(세션 기반 얇은 래퍼 — 실제 산식은
    ``aggregate_flow_baseline`` 참고).

    ``as_of``를 별도로 받는 이유는 테스트 격리뿐이다(모듈 docstring 참고) —
    운영 경로는 항상 기본값(오늘)을 쓴다. 날짜별로 개인/외국인/기관계 중 일부
    투자자 행이 결측이면 0으로 취급한다(``routers/flow_rank.py::
    _sum_investor_net_values``와 동일한 "결측 분류는 0" 관례).
    """
    cutoff = as_of if as_of is not None else dt.datetime.now(KST).date()

    stmt = select(MarketFlow.date, MarketFlow.investor, MarketFlow.net_value).where(
        MarketFlow.market == market,
        MarketFlow.investor.in_(_INVESTORS),
        MarketFlow.date < cutoff,
    )
    rows = (await session.execute(stmt)).all()

    daily: dict[dt.date, dict[str, float]] = {}
    for d, investor, net_value in rows:
        if net_value is None:
            continue
        daily.setdefault(d, {})[investor] = daily.get(d, {}).get(investor, 0.0) + net_value

    recent_dates = sorted(daily.keys(), reverse=True)[:lookback_days]

    historical_scores: list[float] = []
    for d in recent_dates:
        investors = daily[d]
        score = flow_live_score(
            investors.get(_INVESTOR_INDIVIDUAL, 0.0),
            investors.get(_INVESTOR_FOREIGN, 0.0),
            investors.get(_INVESTOR_INSTITUTION, 0.0),
        )
        if score is not None:
            historical_scores.append(score)

    return aggregate_flow_baseline(
        today_score, historical_scores, lookback_days_requested=lookback_days, min_days=min_days
    )
