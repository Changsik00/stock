"""시장(코스피/코스닥)별 투자자 순매수 수집 → market_flow upsert (source='pykrx').

PLAN.md §6 Phase 1-4. REGISTRY["market_flow"]로 등록된다 (collectors/macro.py와
동일한 패턴 — routers/admin.py가 이 모듈을 import해야 REGISTRY 등록 side effect가
발생하고 POST /api/admin/collect/market_flow 및 스케줄러에서 실행 가능해진다;
admin.py에 이미 그 import 자리 주석이 있다: `# from ..collectors import market_flow
as _market_flow_collector`).

collect_fn 계약(collectors/base.py): 이 함수는 session에 upsert만 수행하고
**commit/rollback은 하지 않는다** — base.run_job이 재시도(3회, 지수 백오프) +
collect_log 기록 + 트랜잭션을 전담한다. 이 모듈 단독으로 검증/백필할 때는
호출자가 직접 session.commit()을 하거나 collectors.base.run_job을 통해 호출해야
한다 (backend/scripts/backfill_market_flow.py 참고).
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients.pykrx_client import get_market_investor_flow
from ..models import MarketFlow
from .base import REGISTRY

logger = logging.getLogger(__name__)

MARKETS = ("kospi", "kosdaq")

SOURCE = "pykrx"


async def collect(session: AsyncSession, target_date: dt.date) -> int:
    """kospi/kosdaq의 target_date 투자자별 순매수를 market_flow에 upsert.

    Returns:
        적재(upsert)한 행 수 (시장 2개 x 투자자 최대 12개 = 최대 24행/일; 휴장일이거나
        pykrx가 데이터를 못 가져오면 해당 시장은 0행으로 건너뛴다).
    """
    rows_written = 0
    for market in MARKETS:
        flows = await get_market_investor_flow(market, target_date)
        if not flows:
            logger.info("market_flow: no data for %s %s, skipping", market, target_date)
            continue

        for flow in flows:
            stmt = pg_insert(MarketFlow).values(
                market=market,
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

    return rows_written


REGISTRY["market_flow"] = collect
