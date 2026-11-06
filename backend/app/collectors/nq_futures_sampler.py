"""나스닥선물(NQ=F) 5분봉 섀도 적재기 (PLAN.md §5.91) — 관찰 로그, 매매 입력 아님.

``intraday_sample``(series_key='nq_futures', resolution_seconds=300)을 재사용해
마이그레이션 없이 쌓는다. yfinance 5분봉은 최근 60일만 제공하므로 지금부터 쌓지
않으면 사라진다. 첫 실행(해당 series_key 행이 없음)은 60d 백필, 이후는 5d를 받아
upsert한다(마지막 봉은 진행 중이라 값이 바뀌므로 ON CONFLICT DO UPDATE).

``intraday_compaction``은 ``resolution_seconds=0`` 원본만 압축/삭제하므로 300초 행은
영향받지 않는다. 실패(429 등)는 로그만 남기고 삼킨다 — 앱/자동매매에 무영향.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import us_indices
from ..models import IntradaySample
from ..quant.nq_shadow import NQ_BAR_SECONDS, NQ_SERIES_KEY, build_nq_shadow
from .base import REGISTRY

logger = logging.getLogger(__name__)

BACKFILL_PERIOD = "60d"
REFRESH_PERIOD = "5d"


def parse_nq_bars(bars: list[dict]) -> list[dict]:
    """``fetch_nasdaq_futures_5m_history`` 결과를 intraday_sample 행으로 변환.
    tz 없는 시각·NaN/비양수 종가·중복 시각은 버린다."""
    rows: dict[dt.datetime, dict] = {}
    for b in bars:
        t, c = b.get("time"), b.get("close")
        if not isinstance(t, dt.datetime) or t.tzinfo is None:
            continue
        if c is None or c != c or c <= 0:
            continue
        rows[t] = {
            "series_key": NQ_SERIES_KEY,
            "time": t,
            "value": float(c),
            "resolution_seconds": NQ_BAR_SECONDS,
        }
    return [rows[k] for k in sorted(rows)]


async def collect_nq_futures(session: AsyncSession, target_date: dt.date | None = None) -> int:
    """5분봉을 받아 upsert. 반환=쓴 행 수. 실패는 로그만 남기고 0."""
    try:
        existing = (
            await session.execute(
                select(func.count()).select_from(IntradaySample).where(IntradaySample.series_key == NQ_SERIES_KEY)
            )
        ).scalar_one()
        period = BACKFILL_PERIOD if existing == 0 else REFRESH_PERIOD
        raw = await asyncio.to_thread(us_indices.fetch_nasdaq_futures_5m_history, period)
        rows = parse_nq_bars(raw)
        if not rows:
            return 0
        for i in range(0, len(rows), 2000):
            chunk = rows[i : i + 2000]
            stmt = pg_insert(IntradaySample).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=[IntradaySample.series_key, IntradaySample.time],
                set_={"value": stmt.excluded.value, "resolution_seconds": stmt.excluded.resolution_seconds},
            )
            await session.execute(stmt)
        await session.commit()
        return len(rows)
    except Exception as e:  # noqa: BLE001 - 섀도 적재 실패는 앱에 영향 주지 않는다
        logger.warning("nq_futures 적재 실패(무시): %s", e)
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0


async def load_recent_nq_bars(
    session: AsyncSession, asof: dt.datetime, lookback_min: int = 90
) -> list[tuple[dt.datetime, float]]:
    stmt = (
        select(IntradaySample.time, IntradaySample.value)
        .where(
            IntradaySample.series_key == NQ_SERIES_KEY,
            IntradaySample.time >= asof - dt.timedelta(minutes=lookback_min),
            IntradaySample.time <= asof,
        )
        .order_by(IntradaySample.time)
    )
    return [(t, float(v)) for t, v in (await session.execute(stmt)).all()]


async def get_nq_shadow(session: AsyncSession, asof: dt.datetime | None = None) -> dict:
    """signal_snapshot['nq_shadow'] 값 조회. 어떤 실패도 전 필드 None으로 대체."""
    asof = asof or dt.datetime.now(dt.timezone.utc)
    try:
        async with session.begin_nested():  # 조회 실패가 바깥 트랜잭션을 오염시키지 않게
            bars = await load_recent_nq_bars(session, asof)
        return build_nq_shadow(bars, asof)
    except Exception as e:  # noqa: BLE001
        logger.warning("nq_shadow 조회 실패(무시): %s", e)
        return {"nq_change_15m_pct": None, "nq_change_30m_pct": None, "nq_last": None, "ts": None}


REGISTRY["nq_futures"] = collect_nq_futures
