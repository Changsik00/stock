"""코스피/코스닥 등락 종목수(breadth) 일별 확정치 수집 → market_breadth upsert
(PLAN.md §3.5/§4.6 3.6-2).

REGISTRY["breadth"]로 등록된다 (collectors/market_flow.py와 동일한 패턴 —
routers/admin.py가 이 모듈을 import해야 REGISTRY 등록 side effect가 발생하고
POST /api/admin/collect/breadth 및 스케줄러(평일 18:00, 장마감 후)에서 실행됨).

**장중 값을 이 테이블에 쌓지 않는다는 원칙(§3.5)**은 이 collect_fn 자체가 아니라
"언제 호출하느냐"로 지킨다 — 다른 배치(macro/market_flow 등)와 마찬가지로
스케줄러가 18:00 Asia/Seoul(장마감 후)에만 돌리고, 관리자가 수동으로
POST /api/admin/collect/breadth를 장중에 호출하면 그 시점의 잠정치가 그대로
찍힌다(다른 배치 잡들도 동일한 "장마감 후 실행" 관례에 의존하지, 시각을 코드로
강제하지 않는다 — collectors/market_flow.py, collectors/ohlcv.py 참고).
장중 조회는 이 collector를 거치지 않는 routers/markets.py의
GET /api/markets/breadth/live(온디맨드 + 60초 캐시)가 전담한다.

collect_fn 계약(collectors/base.py): session에 upsert만 수행하고
commit/rollback은 하지 않는다 — base.run_job이 재시도(3회) + collect_log 기록 +
트랜잭션을 전담한다.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import naver_breadth
from ..models import MarketBreadth
from .base import REGISTRY

logger = logging.getLogger(__name__)

MARKETS = ("kospi", "kosdaq")


def _fetch_breadth_blocking(market: str) -> dict:
    """clients.naver_breadth.fetch_breadth의 블로킹(requests) 호출 — asyncio.to_thread로
    감싸 이벤트 루프를 막지 않기 위한 얇은 래퍼(monkeypatch 대상, test_breadth_collector.py
    참고 — collectors/flow_rank.py의 ``_fetch_*_blocking`` 관례와 동일)."""
    return naver_breadth.fetch_breadth(market)


async def collect_breadth(session: AsyncSession, target_date: dt.date) -> tuple[int, str | None]:
    """kospi/kosdaq의 (장마감 후 호출 시) 확정 등락 종목수를 market_breadth에 upsert.

    소스(finance.naver.com/sise/sise_index.naver)가 날짜 쿼리를 지원하지 않으므로
    (clients/naver_breadth.py 모듈 docstring) target_date는 저장용 키로만 쓰이고,
    실제로 받는 값은 항상 "지금" 시점의 값이다 — 다른 소스와 마찬가지로 호출 시점이
    장마감 후가 아니면 target_date에 잠정치가 찍힐 수 있다는 뜻(message로 안내).

    한 시장의 수집이 실패해도(사이트 개편 등) 다른 시장은 계속 진행한다(§7 collect_log로
    부분 실패 감지 — market_flow.py의 "실패해도 계속" 관례와 동일하되, 여기서는 시장별로
    독립 upsert이므로 부분 실패를 개별 로그 메시지로 남긴다).

    Returns:
        (적재한 행 수(최대 2), message) — message는 실패한 시장이 있으면 안내,
        없으면 None.
    """
    rows_written = 0
    failed_markets: list[str] = []

    for market in MARKETS:
        try:
            breadth = await asyncio.to_thread(_fetch_breadth_blocking, market)
        except Exception as e:  # noqa: BLE001 - 한 시장 실패가 다른 시장을 막지 않도록
            logger.warning("breadth: %s 수집 실패: %s", market, e)
            failed_markets.append(market)
            continue

        stmt = pg_insert(MarketBreadth).values(
            market=market,
            date=target_date,
            adv=breadth["adv"],
            dec=breadth["dec"],
            flat=breadth["flat"],
            limit_up=breadth["limit_up"],
            limit_down=breadth["limit_down"],
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[MarketBreadth.market, MarketBreadth.date],
            set_={
                "adv": stmt.excluded.adv,
                "dec": stmt.excluded.dec,
                "flat": stmt.excluded.flat,
                "limit_up": stmt.excluded.limit_up,
                "limit_down": stmt.excluded.limit_down,
            },
        )
        await session.execute(stmt)
        rows_written += 1

    message = f"수집 실패: {failed_markets}" if failed_markets else None
    return rows_written, message


REGISTRY["breadth"] = collect_breadth
