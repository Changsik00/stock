"""시장 전체(코스피/코스닥) 공매도 거래 현황 수집 → short_selling_market upsert.

PLAN.md §5.32-2. REGISTRY["short_selling_market"]로 등록된다 —
``collectors/program_flow.py``와 같은 구조("최근 N일 자가치유 창을 한 번에
받아 upsert")를 따르되, 이 데이터는 macro_series의 단일 값 시계열 모양이
아니라 시장당 6개 필드(거래량/거래대금/전체거래량/전체거래대금/두 비중)를
갖고 있어(``clients/krx_short_selling.py`` 모듈 docstring 참고) macro_series를
재사용하지 않고 전용 테이블(``models.ShortSellingMarket``)에 적재한다 — 대차잔고
(macro_series.lending_balance, KOFIA)와는 별개 지표이므로 ``collectors/macro.py``의
기존 로직은 건드리지 않는다.

**소스**: KRX 정보데이터시스템 공매도 통계 포털(``data.krx.co.kr``,
로그인 불필요 — clients/krx_short_selling.py 모듈 docstring "실측 경과" 절
참고), requests(블로킹)라 ``asyncio.to_thread``로 감싼다(collectors/macro.py와
동일한 패턴).

**조회 창**: LOOKBACK_DAYS(달력일)만큼 매번 다시 받아 upsert한다 — 하루 배치
실패로 며칠 비어도 다음 배치가 자동으로 메운다(collectors/macro.py의
LOOKBACK_DAYS와 동일한 취지). 공매도 순보유잔고와 달리 이 시장 전체 거래
데이터는 당일 확정치라 별도 T+2 지연이 없다.

collect_fn 계약(collectors/base.py): session에 upsert만 수행하고 commit/rollback은
run_job이 전담한다.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import krx_short_selling
from ..models import ShortSellingMarket
from .base import REGISTRY

logger = logging.getLogger(__name__)

MARKETS = ("kospi", "kosdaq")

# 며칠치를 한 번에 다시 받아 upsert할지 — program_flow.py의 "최근 ~100거래일"
# 자가치유 취지와 같되, 이 소스는 거래일 개수가 아니라 달력일로 범위를 받는
# 파라미터라(strtDd/endDd) 120 달력일(대략 80거래일)로 넉넉히 잡는다.
LOOKBACK_DAYS = 120


async def _upsert_market_rows(session: AsyncSession, market: str, rows: list[dict]) -> int:
    count = 0
    for row in rows:
        stmt = pg_insert(ShortSellingMarket).values(
            market=market,
            date=row["date"],
            short_volume=row["short_volume"],
            short_value=row["short_value"],
            total_volume=row["total_volume"],
            total_value=row["total_value"],
            volume_ratio=row["volume_ratio"],
            value_ratio=row["value_ratio"],
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[ShortSellingMarket.market, ShortSellingMarket.date],
            set_={
                "short_volume": stmt.excluded.short_volume,
                "short_value": stmt.excluded.short_value,
                "total_volume": stmt.excluded.total_volume,
                "total_value": stmt.excluded.total_value,
                "volume_ratio": stmt.excluded.volume_ratio,
                "value_ratio": stmt.excluded.value_ratio,
            },
        )
        await session.execute(stmt)
        count += 1
    return count


async def collect(session: AsyncSession, target_date: dt.date) -> int:
    """kospi/kosdaq의 최근 LOOKBACK_DAYS 달력일 공매도 거래 현황을 upsert.

    시장 하나의 조회 실패가 다른 시장 적재를 막지 않도록 개별 try/except로
    감싼다(collectors/macro.py의 KOFIA 시리즈별 격리와 동일한 취지).
    """
    start = target_date - dt.timedelta(days=LOOKBACK_DAYS)
    rows_written = 0

    for market in MARKETS:
        try:
            rows = await asyncio.to_thread(
                krx_short_selling.fetch_market_short_selling, market, start, target_date
            )
        except Exception as e:  # noqa: BLE001 - requests/파싱 예외 전부 개별 격리
            logger.warning("short_selling_market: %s 수집 실패: %s", market, e)
            continue

        rows_written += await _upsert_market_rows(session, market, rows)

    return rows_written


REGISTRY["short_selling_market"] = collect
