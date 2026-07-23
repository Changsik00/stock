"""검증 기반 시장 우세 판정 — 스트릭/버킷 백테스트 함수 (PLAN.md §5.15-1).

2026-07-23 메인 세션이 `index_ohlcv`/`market_flow` 실데이터로 애드혹 스크립트로
검증한 로직을 정식 함수로 승격한다. 순수 계산부는 전부 DB 세션을 인자로 받는
함수라 데이터가 쌓일 때마다 언제든 재계산할 수 있다(§5.7 스켈핑 후보
track-record와 같은 "쌓일수록 갱신" 철학).

**검증 결과 요약(2026-07-23, PLAN.md §5.15 참고)** — 코스닥·외국인 연속
순매수/매도일수만 다음날 코스닥 지수 방향에 뚜렷한 신호를 보였다(3일+연속매수
다음날 상승확률 65.2%, n=69; 2일연속매도 41.8%, n=98 — 실제 이 모듈로 재현해
정확히 일치함을 확인). 코스피·외국인, 코스피·기관, 코스닥·기관은 버킷 간 부호가
들쭉날쭉해 신호로 채택하지 않는다 — 이 모듈은 4개 조합(코스피/코스닥 ×
외국인/기관계) 전부를 동일한 방식으로 계산해 반환하지만, "신뢰할 수 있는
신호"인지 판단하는 건 호출자(routers/markets.py의 GET /api/markets/regime)의
몫이다(이 모듈 자체는 판정하지 않는다 — 순수 통계 계산기).

**스트릭 계산 규칙**(2026-07-23 애드혹 스크립트 그대로): 그날 net_value(투자자
순매수 금액, 원 단위 무관 부호만 사용)가 양수면 매수 스트릭을 이어가고(이전이
매수 스트릭이면 +1, 아니면 1로 새로 시작), 음수면 매도 스트릭을 이어간다(이전이
매도 스트릭이면 -1, 아니면 -1로 새로 시작). 0이면 스트릭이 리셋된다(0).
`next_streak()`이 이 규칙 하나를 구현하고, 과거 시계열 계산(`_streak_series`)과
오늘 라이브 반영(routers/markets.py `_warm_regime`) 둘 다 이 함수를 공유한다 —
로직이 두 곳에서 갈라지지 않도록.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import IndexOhlcv, MarketFlow

# 버킷 순서 — PLAN.md §5.15 검증 결과에 쓰인 이름 그대로(스트릭 정수값 -> 버킷
# 라벨 매핑은 아래 bucket_label 참고).
BUCKET_ORDER: list[str] = ["3+매수", "2매수", "1매수", "1매도", "2매도", "3+매도"]


def bucket_label(streak: int) -> str | None:
    """스트릭 정수값(양수=연속매수 일수, 음수=연속매도 일수, 0=스트릭 없음)을
    6개 버킷 중 하나로 분류한다. 0은 버킷이 없다(``None``) — 그날 순매수/매도
    부호가 리셋된(또는 데이터가 없는) 날이라 어느 쪽 버킷에도 속하지 않는다."""
    if streak >= 3:
        return "3+매수"
    if streak == 2:
        return "2매수"
    if streak == 1:
        return "1매수"
    if streak == -1:
        return "1매도"
    if streak == -2:
        return "2매도"
    if streak <= -3:
        return "3+매도"
    return None


def next_streak(streak: int, net_value: float | int | None) -> int:
    """직전 스트릭 값에 오늘 net_value(순매수 금액, 양수=매수/음수=매도)를 반영한
    다음 스트릭 값. 부호가 이전과 같으면 한 칸 더 누적하고, 바뀌면(또는 이전이
    0이면) ±1로 새로 시작한다. ``net_value``가 None이면 그날은 건너뛴다는 뜻이라
    스트릭을 바꾸지 않고 그대로 반환한다(과거 시계열 집계에서 결측일을 다루는
    방식과 동일 — 아래 ``_streak_series`` 참고)."""
    if net_value is None:
        return streak
    if net_value > 0:
        return streak + 1 if streak > 0 else 1
    if net_value < 0:
        return streak - 1 if streak < 0 else -1
    return 0


async def _daily_returns(session: AsyncSession, market: str) -> dict[dt.date, float]:
    """``index_ohlcv(market)``의 날짜별 종가에서 "다음날 수익률(%)" 시계열을
    만든다. 반환 dict의 키는 "그날" 날짜이고 값은 "그다음으로 저장된 거래일"
    종가 대비 등락률이다 — 저장된 행 사이에 결측 거래일이 있어도 그냥 다음
    저장 행을 쓴다(달력일 보정 없음, 2026-07-23 애드혹 스크립트와 동일한 단순
    방식). 종가가 NULL이거나 0인 행은 그 시작점에서 건너뛴다."""
    stmt = (
        select(IndexOhlcv.date, IndexOhlcv.close)
        .where(IndexOhlcv.market == market)
        .order_by(IndexOhlcv.date)
    )
    rows = (await session.execute(stmt)).all()
    returns: dict[dt.date, float] = {}
    for (d0, c0), (d1, c1) in zip(rows, rows[1:]):
        if c0 is None or c1 is None:
            continue
        c0f = float(c0)
        if c0f == 0:
            continue
        returns[d0] = (float(c1) - c0f) / c0f * 100.0
    return returns


async def _streak_series(session: AsyncSession, market: str, investor: str) -> list[tuple[dt.date, int]]:
    """``market_flow(market, investor)``의 날짜별 net_value에서 연속 순매수/매도
    스트릭 시계열을 만든다 — ``[(date, streak), ...]``, 날짜 오름차순. net_value가
    None인 행은 건너뛴다(스트릭에 영향을 주지 않고, 반환 리스트에도 들어가지
    않는다 — ``next_streak``의 None 처리와 일관)."""
    stmt = (
        select(MarketFlow.date, MarketFlow.net_value)
        .where(MarketFlow.market == market, MarketFlow.investor == investor)
        .order_by(MarketFlow.date)
    )
    rows = (await session.execute(stmt)).all()
    out: list[tuple[dt.date, int]] = []
    streak = 0
    for d, net_value in rows:
        if net_value is None:
            continue
        streak = next_streak(streak, net_value)
        out.append((d, streak))
    return out


async def compute_current_streak(session: AsyncSession, market: str, investor: str) -> int:
    """``market_flow``에 저장된 가장 최근 확정치까지 반영한 "현재" 스트릭.
    오늘자 라이브(장중 잠정치) 반영은 이 함수의 책임이 아니다 — 호출자
    (routers/markets.py ``GET /api/markets/regime``)가 이 값에 ``next_streak``을
    한 번 더 적용해 "오늘 반영한 스트릭"을 계산한다(PLAN.md §5.15-2). 데이터가
    전혀 없으면 0."""
    series = await _streak_series(session, market, investor)
    return series[-1][1] if series else 0


async def compute_baseline(session: AsyncSession, market: str) -> dict:
    """``market``의 전체 기간 "다음날 수익률" 베이스라인 — 스트릭/투자자와
    무관하게 순수 index_ohlcv 가격만으로 계산한다. 코스피/코스닥 각 시장 하나의
    베이스라인을 그 시장의 모든 투자자 버킷 비교 기준으로 쓴다.

    Returns ``{"n": int, "avg_return_pct": float|None, "positive_rate_pct": float|None}``.
    """
    returns = await _daily_returns(session, market)
    values = list(returns.values())
    n = len(values)
    if n == 0:
        return {"n": 0, "avg_return_pct": None, "positive_rate_pct": None}
    avg = sum(values) / n
    positive = sum(1 for v in values if v > 0) / n * 100
    return {"n": n, "avg_return_pct": round(avg, 3), "positive_rate_pct": round(positive, 1)}


async def compute_streak_buckets(session: AsyncSession, market: str, investor: str) -> list[dict]:
    """``market`` × ``investor``의 연속 순매수/매도 스트릭을 6개 버킷(3+매수/
    2매수/1매수/1매도/2매도/3+매도)으로 나눠, 각 버킷에 속한 날짜들의 "다음날
    수익률" 평균·표본수·상승확률을 계산한다(PLAN.md §5.15-1, 2026-07-23 애드혹
    검증 로직 승격 — 이 함수의 결과가 그날 검증한 수치와 정확히 일치함을 실제
    DB로 확인했다: 코스닥·외국인 3+매수 n=69·avg +0.522%·상승확률 65.2% 등).

    스트릭이 기록된 날짜와 index_ohlcv 날짜가 둘 다 있는 날만 집계에 들어간다
    (둘 중 하나라도 없으면 그날은 건너뜀 — market_flow가 index_ohlcv보다 저장된
    날짜가 더 많을 수 있어서, 예: 휴장일 처리 차이). 표본이 없는 버킷은 n=0,
    avg_return_pct/positive_rate_pct는 None.

    Returns ``[{"bucket": str, "n": int, "avg_return_pct": float|None,
    "positive_rate_pct": float|None}, ...]`` (``BUCKET_ORDER`` 순서 그대로).
    """
    returns = await _daily_returns(session, market)
    streaks = await _streak_series(session, market, investor)

    buckets: dict[str, list[float]] = {b: [] for b in BUCKET_ORDER}
    for d, streak in streaks:
        ret = returns.get(d)
        if ret is None:
            continue
        label = bucket_label(streak)
        if label is None:
            continue
        buckets[label].append(ret)

    result: list[dict] = []
    for label in BUCKET_ORDER:
        values = buckets[label]
        n = len(values)
        if n == 0:
            result.append({"bucket": label, "n": 0, "avg_return_pct": None, "positive_rate_pct": None})
            continue
        avg = sum(values) / n
        positive = sum(1 for v in values if v > 0) / n * 100
        result.append(
            {
                "bucket": label,
                "n": n,
                "avg_return_pct": round(avg, 3),
                "positive_rate_pct": round(positive, 1),
            }
        )
    return result
