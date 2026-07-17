"""flow_path 백필 — 가용한 모든 날짜에 대해 ETF look-through 계산을 실행한다
(PLAN.md §4.5/§6 Phase 3.5-3).

flow_path.date로 쓸 "가용한 날짜"의 정의: flow_rank(순매수 랭킹) ∪ etf_holdings(구성
스냅샷) ∪ etf_stats(NAV/AUM/순유입) 세 테이블에 실제로 존재하는 날짜의 합집합이다.
collectors/flow_path.py는 target_date에 정확히 맞는 holdings/stats 행이 없어도
"가장 가까운 스냅샷"으로 대체 계산하므로(§4.5 T-1 PDF 원칙), 세 테이블 중 어느
하나에만 있는 날짜라도 계산 자체는 항상 시도할 가치가 있다 — 다만 direct_net은
flow_rank에 그 날짜가 없으면 전부 NULL로 나온다(정상 동작, collectors/flow_path.py
docstring 참고).

Usage:
    python -m scripts.backfill_flow_path
"""

import asyncio
import datetime as dt
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_flow_path")

from sqlalchemy import select  # noqa: E402

from app.collectors.base import run_job  # noqa: E402
from app.collectors.flow_path import collect_flow_path  # noqa: E402
from app.db import async_session_factory  # noqa: E402
from app.models import EtfHolding, EtfStat, FlowRank  # noqa: E402

JOB_NAME = "flow_path"


async def _distinct_dates() -> list[dt.date]:
    dates: set[dt.date] = set()
    async with async_session_factory() as session:
        for model in (FlowRank, EtfHolding, EtfStat):
            rows = (await session.execute(select(model.date).distinct())).scalars().all()
            dates.update(rows)
    return sorted(dates)


async def run() -> None:
    dates = await _distinct_dates()
    if not dates:
        logger.warning("flow_rank/etf_holdings/etf_stats에 날짜가 하나도 없음 — 백필할 것이 없습니다.")
        return

    logger.info("계산 대상 날짜 %d개: %s", len(dates), ", ".join(d.isoformat() for d in dates))

    ok = 0
    fail = 0
    for target_date in dates:
        result = await run_job(JOB_NAME, target_date, collect_flow_path)
        if result["status"] == "ok":
            ok += 1
            logger.info(
                "%s ok: %d행 — %s", target_date.isoformat(), result["rows"], result.get("message") or ""
            )
        else:
            fail += 1
            logger.error("%s 실패: %s", target_date.isoformat(), result.get("message"))

    logger.info("완료: 성공 %d / 실패 %d (총 %d개 날짜)", ok, fail, len(dates))


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
