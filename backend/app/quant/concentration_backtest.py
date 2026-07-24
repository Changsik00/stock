"""코스피/코스닥 쏠림 비율 백테스트 (PLAN.md §5.23).

**배경**: §5.18/§5.19가 만든 실시간 "쏠림 비율" 지표(코스피_배율/(코스피_배율+
코스닥_배율), 배율=오늘_활동량/최근20거래일_평소활동량, 활동량=|외국인
순매수|+|기관계 순매수|)를 두고 사용자가 "50% 기준으로 코스닥/코스피 중 어디가
좋은지 표시하면 되지 않냐"고 제안했다. 이 프로젝트는 §5.15에서 정확히 같은
실수를 이미 한 번 겪었다 — "백워데이션 → 차익매도유의" 배지가 그럴듯해
보였지만 실제 3년치 백테스트로는 43.8% vs 43.0%(사실상 동일)라 예측력이
전혀 없어 배지를 통째로 제거했다. 그래서 이번에도 "우세" 언어를 UI에 붙이기
전에 §5.15와 동일한 엄밀도로 먼저 백테스트한다(이 모듈). 결과 해석과 UI 반영
여부는 이 모듈의 책임이 아니라 메인 세션의 몫이다(PLAN.md §5.23-2) — 이
모듈은 순수 통계 계산기다(regime_backtest.py와 동일한 철학, 위 모듈 docstring
참고).

**왜 롤링 베이스라인이 필요한가(스냅샷 베이스라인이면 왜 안 되는가)**:
`regime_backtest.compute_activity_baseline`은 "호출 시점 기준 가장 최근
20거래일" 평균을 낸다 — 60초마다 갱신되는 라이브 위젯에는 그걸로 충분하다
(그 순간의 "평소"가 항상 그 시점 기준 최근 20일이니까). 하지만 이 함수를
그대로 과거 특정 날짜 D에 적용하면, "오늘(스크립트 실행 시점)" 기준 최근
20일을 D 시점의 베이스라인으로 쓰게 되어 D보다 한참 뒤(심지어 미래)의
데이터가 D 시점 판단에 섞여 들어간다 — 전형적인 lookahead bias다. 그래서 이
모듈은 매 날짜 D마다 "D 이전(D 미포함) 20거래일"만 사용하는 진짜 롤링
베이스라인을 재구성한다(`_rolling_baseline`). 이건 라이브 로직의 근사가
아니라, "그날 실시간으로 이 위젯이 떠 있었다면 정확히 이 값을 보여줬을 것"이라는
의미에서 라이브 로직의 충실한 과거 재현이다 — 매 순간 "지금 기준 최근 20일"을
쓰는 라이브 방식과 매 날짜마다 "그날 기준 최근 20일"을 쓰는 롤링 방식은 같은
정의를 시점만 다르게 적용한 것이기 때문이다.

**수익률 계산은 재사용, 중복 금지**: "다음날 수익률" 정의(저장된 다음 거래일
종가 대비 등락률)는 이미 `regime_backtest._daily_returns`가 구현하고 있고,
§5.15에서 실 DB로 검증까지 끝난 로직이다. 이 모듈은 그 함수를 그대로
import해서 코스피/코스닥 양쪽에 호출한다 — 백테스트 모듈 두 개에 같은 로직을
복붙하지 않는다는 이 프로젝트의 house rule을 따른다.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import MarketFlow
from . import regime_backtest

# §5.19 라이브 지표와 동일한 투자자 조합/윈도우 — "이 지표"를 백테스트하는
# 것이지 새 지표를 만드는 게 아니므로 반드시 라이브와 일치해야 한다.
ACTIVITY_INVESTORS = ("외국인", "기관계")
BASELINE_WINDOW_DAYS = 20

# PLAN.md §5.23 가설에 명시된 4구간 경계 그대로: 코스피강쏠림 >=60%,
# 코스피약쏠림 50~60%, 코스닥약쏠림 40~50%, 코스닥강쏠림 <40%. 50%가 "양쪽
# 다 평소 대비 똑같이 늘거나 줄었다"는 중립점이라, 그로부터 ±10%p를 "약",
# 그 바깥을 "강"으로 나눈다(§5.18/§5.19가 이미 쓰던 50% 중립 기준을 그대로
# 4구간으로 세분화한 것뿐 — 새 임계값을 발명하지 않았다).
CONCENTRATION_BUCKET_ORDER: list[str] = ["코스피강쏠림", "코스피약쏠림", "코스닥약쏠림", "코스닥강쏠림"]


def _bucket_label(concentration_pct: float) -> str:
    """쏠림%(0~100, 50=중립, 100=코스피만 평소보다 유독 활발, 0=코스닥만
    유독 활발)를 4구간 중 하나로 분류한다. 경계값은 PLAN.md §5.23 가설에
    명시된 그대로: >=60 코스피강쏠림, [50,60) 코스피약쏠림, [40,50) 코스닥약쏠림,
    <40 코스닥강쏠림 — 즉 각 경계는 왼쪽(작은 값) 구간에 포함되지 않고
    오른쪽(큰 값) 구간에 포함된다(>= 비교)."""
    if concentration_pct >= 60:
        return "코스피강쏠림"
    if concentration_pct >= 50:
        return "코스피약쏠림"
    if concentration_pct >= 40:
        return "코스닥약쏠림"
    return "코스닥강쏠림"


async def _daily_activity_series(session: AsyncSession, market: str) -> dict[dt.date, float]:
    """``market_flow(market, investor in ACTIVITY_INVESTORS)``에서 날짜별
    ``|외국인 순매수| + |기관계 순매수|``(§5.18/§5.19와 동일한 "방향 무관
    절댓값 활동량" 정의) 시계열 전체를 만든다. net_value가 NULL인 행은
    건너뛴다.

    `regime_backtest.compute_activity_baseline`과 집계 정의는 같지만, 그
    함수는 "호출 시점 기준 최근 N일"만 반환하는 라이브 전용 공개 API라
    과거 전체 시계열을 필요로 하는 이 백테스트 용도로는 맞지 않는다(그 함수를
    억지로 재사용하면 날짜 필터링 인터페이스를 새로 얹어야 해서 오히려
    프로덕션에서 쓰는 공개 API를 건드리게 된다). 쿼리 자체가 짧아 그대로 이
    모듈에 두는 쪽이 더 안전하다(PLAN.md §5.23-1 작업 지시 참고)."""
    stmt = select(MarketFlow.date, MarketFlow.net_value).where(
        MarketFlow.market == market, MarketFlow.investor.in_(ACTIVITY_INVESTORS)
    )
    rows = (await session.execute(stmt)).all()
    daily: dict[dt.date, float] = {}
    for d, net_value in rows:
        if net_value is None:
            continue
        daily[d] = daily.get(d, 0.0) + abs(net_value)
    return daily


def _rolling_baseline(
    dates_sorted: list[dt.date],
    daily_activity: dict[dt.date, float],
    window: int = BASELINE_WINDOW_DAYS,
) -> dict[dt.date, float | None]:
    """``dates_sorted``(오름차순 정렬된 거래일 목록) 각 날짜 D에 대해, D
    "이전"(D 미포함) ``window``개 거래일의 ``daily_activity`` 평균을 계산한다.
    앞쪽에 ``window``개보다 적은 과거 날짜만 있으면(시계열 시작 부근) ``None``.

    **미래참조 방지가 이 함수의 핵심 존재 이유**이므로 일부러 단순한 인덱스
    슬라이싱 루프로 작성한다 — pandas rolling 등으로 "영리하게" 구현하다
    창(window) 정렬을 한 칸 잘못 맞추는(D 자신이나 D 이후 값이 섞이는) 실수를
    피하기 위해서다. ``dates_sorted[i]``의 베이스라인은 오직
    ``dates_sorted[i-window:i]``(``daily_activity``에 값이 있는 것만) 평균이며,
    ``dates_sorted[i]`` 자신이나 ``dates_sorted[i+1:]``은 절대 참조하지 않는다."""
    result: dict[dt.date, float | None] = {}
    for i, d in enumerate(dates_sorted):
        if i < window:
            result[d] = None
            continue
        prior_dates = dates_sorted[i - window : i]
        values = [daily_activity[p] for p in prior_dates if p in daily_activity]
        result[d] = sum(values) / len(values) if values else None
    return result


async def compute_daily_concentration_series(session: AsyncSession) -> list[dict]:
    """코스피/코스닥 각각의 롤링 베이스라인 대비 배율로 날짜별 쏠림%를
    과거 전체 기간 재구성한다(PLAN.md §5.23, lookahead 없는 버전 — 라이브
    ``get_market_concentration_series``의 시각 단위 계산과 같은 공식을 "그날의
    당시 20일 평소"로 재현한 것).

    양쪽 시장 모두 market_flow 데이터가 있는 날짜만 대상으로 하고(교집합),
    두 베이스라인이 모두 계산 가능(20거래일 이상 과거 데이터 존재)하고 양수인
    날짜만 결과에 포함한다(§5.19의 "정규화 기준이 없으면 억지로 채우지 않는다"
    철학 그대로).

    Returns ``[{"date": dt.date, "concentration_pct": float}, ...]`` (날짜
    오름차순). 코스피_배율 = 코스피_활동량 / 코스피_베이스라인, 코스닥_배율도
    동일, ``concentration_pct = 코스피_배율 / (코스피_배율+코스닥_배율) * 100``
    (분모가 0 이하인 날은 건너뜀).
    """
    kospi_activity = await _daily_activity_series(session, "kospi")
    kosdaq_activity = await _daily_activity_series(session, "kosdaq")

    common_dates = sorted(set(kospi_activity) & set(kosdaq_activity))

    kospi_baseline = _rolling_baseline(common_dates, kospi_activity)
    kosdaq_baseline = _rolling_baseline(common_dates, kosdaq_activity)

    series: list[dict] = []
    for d in common_dates:
        kb = kospi_baseline[d]
        qb = kosdaq_baseline[d]
        if kb is None or kb <= 0 or qb is None or qb <= 0:
            continue
        kospi_multiple = kospi_activity[d] / kb
        kosdaq_multiple = kosdaq_activity[d] / qb
        denom = kospi_multiple + kosdaq_multiple
        if denom <= 0:
            continue
        concentration_pct = kospi_multiple / denom * 100
        series.append({"date": d, "concentration_pct": concentration_pct})
    return series


async def compute_concentration_buckets(session: AsyncSession) -> list[dict]:
    """쏠림%를 4구간(``CONCENTRATION_BUCKET_ORDER``)으로 나눠 각 구간의 "다음날"
    코스피/코스닥/상대 수익률 통계를 계산한다(PLAN.md §5.23-1 백테스트 본체).

    쏠림% 계산 날짜와 코스피/코스닥 index_ohlcv 수익률이 모두 존재하는 날만
    집계에 들어간다(둘 중 하나라도 없으면 건너뜀 — `regime_backtest.
    compute_streak_buckets`의 "표본 날짜와 index_ohlcv 날짜가 둘 다 있어야
    한다" 스킵 규칙과 동일한 이유: market_flow와 index_ohlcv가 저장된 날짜
    범위/휴장일 처리가 완전히 같지 않을 수 있어서다).

    표본이 없는 버킷(``n == 0``)은 모든 수치 필드가 ``None``
    (`compute_streak_buckets`의 빈 버킷 관례와 동일).

    Returns ``[{"bucket": str, "n": int, "avg_kospi_return_pct": float|None,
    "kospi_positive_rate_pct": float|None, "avg_kosdaq_return_pct": float|None,
    "kosdaq_positive_rate_pct": float|None, "avg_relative_return_pct": float|None,
    "kospi_outperforms_rate_pct": float|None}, ...]`` (``CONCENTRATION_BUCKET_ORDER``
    순서 그대로).
    """
    concentration_series = await compute_daily_concentration_series(session)
    kospi_returns = await regime_backtest._daily_returns(session, "kospi")
    kosdaq_returns = await regime_backtest._daily_returns(session, "kosdaq")

    # 버킷별로 (코스피수익률, 코스닥수익률) 쌍을 모은다.
    buckets: dict[str, list[tuple[float, float]]] = {b: [] for b in CONCENTRATION_BUCKET_ORDER}
    for point in concentration_series:
        d = point["date"]
        k_ret = kospi_returns.get(d)
        q_ret = kosdaq_returns.get(d)
        if k_ret is None or q_ret is None:
            continue
        label = _bucket_label(point["concentration_pct"])
        buckets[label].append((k_ret, q_ret))

    result: list[dict] = []
    for label in CONCENTRATION_BUCKET_ORDER:
        pairs = buckets[label]
        n = len(pairs)
        if n == 0:
            result.append(
                {
                    "bucket": label,
                    "n": 0,
                    "avg_kospi_return_pct": None,
                    "kospi_positive_rate_pct": None,
                    "avg_kosdaq_return_pct": None,
                    "kosdaq_positive_rate_pct": None,
                    "avg_relative_return_pct": None,
                    "kospi_outperforms_rate_pct": None,
                }
            )
            continue

        kospi_vals = [k for k, _ in pairs]
        kosdaq_vals = [q for _, q in pairs]
        relative_vals = [k - q for k, q in pairs]

        avg_kospi = sum(kospi_vals) / n
        avg_kosdaq = sum(kosdaq_vals) / n
        avg_relative = sum(relative_vals) / n
        kospi_positive_rate = sum(1 for v in kospi_vals if v > 0) / n * 100
        kosdaq_positive_rate = sum(1 for v in kosdaq_vals if v > 0) / n * 100
        kospi_outperforms_rate = sum(1 for k, q in pairs if k > q) / n * 100

        result.append(
            {
                "bucket": label,
                "n": n,
                "avg_kospi_return_pct": round(avg_kospi, 3),
                "kospi_positive_rate_pct": round(kospi_positive_rate, 1),
                "avg_kosdaq_return_pct": round(avg_kosdaq, 3),
                "kosdaq_positive_rate_pct": round(kosdaq_positive_rate, 1),
                "avg_relative_return_pct": round(avg_relative, 3),
                "kospi_outperforms_rate_pct": round(kospi_outperforms_rate, 1),
            }
        )
    return result
