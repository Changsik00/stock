"""3년치 매크로(환율/유가) 시계열 초기 적재 (PLAN.md §6, Phase 1-2).

- 환율(usdkrw): ECOS 기간 조회 한 번 (`ECOS_API_KEY` 미설정 시 sample 키 -> 최대 10건만
  반환되는 것이 정상 동작임, clients/ecos.py 참고)
- 유가(wti/brent): yfinance 기간 조회 한 번씩, 실패 시 FRED CSV로 자동 폴백

Usage: python -m scripts.backfill_macro [years]  (기본 3년)
"""

import asyncio
import datetime as dt
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backfill_macro")

from app.clients import commodities, ecos  # noqa: E402
from app.collectors.macro import OIL_SERIES, upsert_series_rows  # noqa: E402
from app.db import async_session_factory  # noqa: E402


async def main(years: int = 3) -> None:
    end = dt.date.today()
    start = end - dt.timedelta(days=365 * years)

    async with async_session_factory() as session:
        total = 0

        logger.info("ECOS 환율(usdkrw) 기간 조회: %s ~ %s", start, end)
        usdkrw_rows = ecos.fetch_usdkrw(start, end)
        for row in usdkrw_rows:
            row["source"] = "ecos"
        n = await upsert_series_rows(session, usdkrw_rows, "usdkrw")
        logger.info("usdkrw: %d건 적재", n)
        total += n

        for series in OIL_SERIES:
            logger.info("유가(%s) 기간 조회: %s ~ %s", series, start, end)
            rows = commodities.fetch_oil_series(series, start, end)
            n = await upsert_series_rows(session, rows, series)
            source = rows[0]["source"] if rows else "-"
            logger.info("%s: %d건 적재 (source=%s)", series, n, source)
            total += n

        await session.commit()
        logger.info("총 %d건 적재 완료", total)


if __name__ == "__main__":
    years = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    asyncio.run(main(years))
