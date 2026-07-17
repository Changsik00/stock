"""지수 일봉(코스피/코스닥/코스피200선물) 3년치 초기 적재 (PLAN.md §6, index_ohlcv).

소스: collectors/ohlcv.py의 fetch_market_rows() 그대로 재사용한다 —
kospi/kosdaq은 yfinance(^KS11/^KQ11) 1차 + 네이버(clients/naver_index.py) 폴백,
k200_futures는 네이버만(코스피 200 선물 근월물, 심볼 FUT). KRX Open API는
403(서비스 승인 미비, 2026-07)이라 이번 백필에서 사용하지 않는다.

market당 1회 요청으로 전체 기간이 한 번에 온다(페이징 없음, 실측: kospi/kosdaq/
k200_futures 모두 3년 요청 시 730행 내외) — market 간 0.5초 딜레이만 둔다
(backfill_kofia.py의 REQUEST_DELAY_SECONDS 패턴과 동일한 이유: 소스에 과도한
요청을 보내지 않기 위함).

Usage: python -m scripts.backfill_index_ohlcv [years]  (기본 3년)
"""

import asyncio
import datetime as dt
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backfill_index_ohlcv")

from app.collectors.ohlcv import MARKETS, _upsert_rows, fetch_market_rows  # noqa: E402
from app.db import async_session_factory  # noqa: E402

REQUEST_DELAY_SECONDS = 0.5


async def main(years: int = 3) -> None:
    end = dt.date.today()
    start = end - dt.timedelta(days=365 * years)

    async with async_session_factory() as session:
        total = 0
        for i, market in enumerate(MARKETS):
            if i > 0:
                time.sleep(REQUEST_DELAY_SECONDS)
            logger.info("%s 일봉 조회: %s ~ %s", market, start, end)
            rows = await asyncio.to_thread(fetch_market_rows, market, start, end)
            n = await _upsert_rows(session, market, rows)
            logger.info(
                "%s: %d행 적재 (%s ~ %s)",
                market,
                n,
                rows[0]["date"] if rows else None,
                rows[-1]["date"] if rows else None,
            )
            total += n

        await session.commit()
        logger.info("총 %d행 적재 완료", total)


if __name__ == "__main__":
    years = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    asyncio.run(main(years))
