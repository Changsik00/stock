"""수급 가속도(실시간 반응성 지표) — PLAN.md §5.17.

**배경**: §5.15의 연속 순매수/매도 스트릭(``regime_backtest.py``)은 하루 단위
이산값이라 오늘 하루 안에서 매수/매도 "속도"가 빨라지는지 느려지는지는 전혀
보여주지 못한다. 사용자 제안("기준점에서 변동에 대한 반응성을 지표로 실시간으로
계속 뭔가 대응해 줘야 하지 않을까?")에 따라, §5.14로 쌓기 시작한 ``intraday_sample``
60초 틱을 재료로 삼아 "지금 순매수 속도가 가속/감속 중인지"를 별도 지표로 계산한다.

**스트릭과 절대 섞지 않는다**(§5.15의 명시적 원칙 계승): 스트릭은 느리지만
검증된 신호, 가속도는 빠르지만 아직 정규화/검증 안 된 신호 — 종합 판정
(``routers.markets._judge_regime``)에는 넣지 않고 별도 필드로만 노출한다.

**지표 정의**: ``intraday_sample.value``는 "오늘 누적 순매수"(계속 오르내리는
절대 수준 — ``collectors.intraday_snapshot.record_flow_snapshot``이
``_warm_flow_live``가 반환한 ka10051 당일 누적치를 그대로 담는다, 단위는
백만원). 그래서 시간 구간의 **차분**이 그 구간의 순매수 "속도"다.

- ``recent_velocity`` = 지금 값 - 30분 전 값
- ``prior_velocity`` = 30분 전 값 - 60분 전 값
- ``acceleration`` = ``recent_velocity`` - ``prior_velocity`` (양수=순매수 속도가
  빨라지는 중, 음수=느려지는 중 — 부호 하나로 일관되게 해석 가능)

세 시점(지금/30분전/60분전) 각각 "그 시각 이전 가장 가까운 틱"을 찾는다(정확히
그 시각의 틱이 없을 수 있음 — 60초 간격이라 오차는 최대 1분 이내). 세 시점 중
하나라도 데이터가 없으면(예: 장 시작 직후라 아직 60분치가 안 쌓임) 억지로
계산하지 않고 ``None``을 반환한다.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import IntradaySample


async def _latest_value_at_or_before(
    session: AsyncSession, series_key: str, at: dt.datetime
) -> float | None:
    """``series_key``의 틱 중 ``time <= at``인 것 중 가장 최근(time 내림차순)
    값 하나. 없으면 None."""
    stmt = (
        select(IntradaySample.value)
        .where(IntradaySample.series_key == series_key, IntradaySample.time <= at)
        .order_by(IntradaySample.time.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    return float(row[0])


async def compute_flow_acceleration(
    session: AsyncSession, series_key: str, now: dt.datetime, window_minutes: int = 30
) -> dict | None:
    """``series_key``의 최근 수급 가속도를 계산한다(모듈 docstring 지표 정의
    참고). 지금/``window_minutes``분 전/``2*window_minutes``분 전 세 시점 중
    하나라도 값을 못 찾으면 ``None``.

    Returns ``{"window_minutes": int, "recent_velocity": float,
    "prior_velocity": float, "acceleration": float}`` (전부 series_key와 동일한
    단위 — flow_* 시리즈는 백만원)."""
    t_recent = now - dt.timedelta(minutes=window_minutes)
    t_prior = now - dt.timedelta(minutes=2 * window_minutes)

    now_value = await _latest_value_at_or_before(session, series_key, now)
    recent_value = await _latest_value_at_or_before(session, series_key, t_recent)
    prior_value = await _latest_value_at_or_before(session, series_key, t_prior)

    if now_value is None or recent_value is None or prior_value is None:
        return None

    recent_velocity = now_value - recent_value
    prior_velocity = recent_value - prior_value
    acceleration = recent_velocity - prior_velocity

    return {
        "window_minutes": window_minutes,
        "recent_velocity": recent_velocity,
        "prior_velocity": prior_velocity,
        "acceleration": acceleration,
    }
