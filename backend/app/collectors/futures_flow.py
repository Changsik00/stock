"""K200 선물 투자자별(개인/외국인/기관계) 순매수 수집 → market_flow upsert
(market='k200_futures', source='naver') — PLAN.md §4.5 4.5-2.

REGISTRY["futures_flow"]로 등록된다 (collectors/market_flow.py·breadth.py와 동일한
패턴 — routers/admin.py가 이 모듈을 import해야 REGISTRY 등록 side effect가 발생하고
POST /api/admin/collect/futures_flow 및 스케줄러에서 실행 가능해진다).

소스는 clients/naver_futures_flow.py(m.stock.naver.com/api/index/FUT/trend) —
그 모듈 docstring에 소스 선정 경과·단위 확정(억원->백만원 변환 근거) 전부 있다.
코스피/코스닥 market_flow(kiwoom ka10051, 13분류)와 달리 이 소스는 개인/외국인/
기관계 3분류만 준다.

collect_fn 계약(collectors/base.py): session에 upsert만 수행하고 commit/rollback은
하지 않는다 — base.run_job이 재시도(3회) + collect_log 기록 + 트랜잭션을 전담한다.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import naver_futures_flow
from ..models import IntradaySample, MarketFlow
from .base import REGISTRY

logger = logging.getLogger(__name__)

MARKET = "k200_futures"
SOURCE = "naver"


def _fetch_blocking(target_date: dt.date) -> dict | None:
    """clients.naver_futures_flow.fetch_futures_flow의 블로킹(requests) 호출 —
    asyncio.to_thread로 감싸 이벤트 루프를 막지 않기 위한 얇은 래퍼(monkeypatch 대상,
    collectors/breadth.py의 ``_fetch_breadth_blocking`` 관례와 동일)."""
    return naver_futures_flow.fetch_futures_flow(target_date)


_KST = dt.timezone(dt.timedelta(hours=9))
_SAMPLE_KEY = "futures_외국인"  # collectors/intraday_snapshot.FUTURES_SERIES_KEY와 동일(순환 임포트 회피용 복제)


async def build_reconcile_message(session: AsyncSession, target_date: dt.date, confirmed: int | None) -> str | None:
    """Phase 5.115 — 그날 ``futures_외국인`` 장중 샘플과 일별 확정값(백만원)을 대조한 요약.

    마지막 샘플(시각 포함), 15:29 이전 마지막 샘플, 확정값, 두 차이를 한 줄로 만든다.
    새 테이블 없이 collect_log.message에만 남긴다(집계 SQL은 PLAN.md 5.115 참고).
    샘플이 없으면 None."""
    start = dt.datetime.combine(target_date, dt.time.min, tzinfo=_KST)
    rows = (
        await session.execute(
            select(IntradaySample.time, IntradaySample.value)
            .where(
                IntradaySample.series_key == _SAMPLE_KEY,
                IntradaySample.time >= start,
                IntradaySample.time < start + dt.timedelta(days=1),
            )
            .order_by(IntradaySample.time)
        )
    ).all()
    if not rows or confirmed is None:
        return None
    last_t, last_v = rows[-1]
    cut = dt.time(15, 29, 59)
    before = [r for r in rows if r[0].astimezone(_KST).time() <= cut]
    last_t = last_t.astimezone(_KST)
    msg = f"reconcile 확정={confirmed:+,.0f} 마지막샘플({last_t:%H:%M})={float(last_v):+,.0f} 차이={confirmed - float(last_v):+,.0f}"
    if before:
        bt, bv = before[-1]
        msg += f" | 15:29이전({bt.astimezone(_KST):%H:%M})={float(bv):+,.0f} 차이={confirmed - float(bv):+,.0f}"
    return msg + f" (샘플 {len(rows)}개, 백만원)"


async def collect(session: AsyncSession, target_date: dt.date) -> int | tuple[int, str | None]:
    """target_date의 K200 선물 투자자별 순매수 3행(개인/외국인/기관계)을
    market_flow(market='k200_futures')에 upsert.

    Returns:
        적재(upsert)한 행 수 (휴장일이거나 소스가 데이터를 주지 않으면 0). 장중 샘플이 있으면
        ``(rows, reconcile 메시지)`` — base.run_job이 collect_log.message에 남긴다.
    """
    result = await asyncio.to_thread(_fetch_blocking, target_date)
    if result is None:
        logger.info("futures_flow: no data for %s, skipping", target_date)
        return 0

    rows_written = 0
    for flow in result["flows"]:
        stmt = pg_insert(MarketFlow).values(
            market=MARKET,
            date=target_date,
            investor=flow["investor"],
            net_value=flow["net_value"],
            net_volume=flow["net_volume"],
            source=SOURCE,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[MarketFlow.market, MarketFlow.date, MarketFlow.investor],
            set_={
                "net_value": stmt.excluded.net_value,
                "net_volume": stmt.excluded.net_volume,
                "source": stmt.excluded.source,
            },
        )
        await session.execute(stmt)
        rows_written += 1

    confirmed = next((f["net_value"] for f in result["flows"] if f["investor"] == "외국인"), None)
    try:
        message = await build_reconcile_message(session, target_date, confirmed)
    except Exception as e:  # noqa: BLE001 — 대조는 부가 기능, 적재 성공을 막지 않는다
        logger.warning("futures_flow: reconcile 실패: %s", e)
        message = None
    if message:
        logger.info("futures_flow: %s", message)
        return rows_written, message
    return rows_written


REGISTRY["futures_flow"] = collect
