"""지수 일봉(코스피/코스닥/코스피200선물/코스피200 현물지수) 3년치 초기 적재
(PLAN.md §6/§4.5-3, index_ohlcv).

소스: collectors/ohlcv.py의 fetch_market_rows() 그대로 재사용한다(MARKETS도 그
모듈에서 import) — kospi/kosdaq/k200_futures/kospi200 모두 네이버(clients/
naver_index.py) 1차, kospi/kosdaq만 실패 시 yfinance(^KS11/^KQ11)로 폴백
(k200_futures/kospi200은 yfinance 심볼이 없어 폴백 없음). kospi200은 2026-07-19
§4.5-3 작업으로 MARKETS에 추가됐다 — 이 스크립트 코드 변경 없이 자동으로
포함된다.
2026-07-17: 코스닥 volume이 yfinance(^KQ11)에서 최근 2개월을 제외하곤 800~1,300
수준의 쓰레기 값이었던 게 발견되어 1차 소스를 네이버로 뒤집었다 — 이 스크립트를
재실행하면 upsert라 기존 kospi/kosdaq 행이 네이버 값으로 전량 덮어써진다(단위가
섞이지 않도록 부분이 아닌 전체 기간을 다시 채우는 것이 중요, ohlcv.py 모듈
docstring 참고). KRX Open API는 403(서비스 승인 미비, 2026-07)이라 이번 백필에서
사용하지 않는다.

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
            rows, source = await asyncio.to_thread(fetch_market_rows, market, start, end)
            n = await _upsert_rows(session, market, rows)
            logger.info(
                "%s: %d행 적재, source=%s (%s ~ %s)",
                market,
                n,
                source,
                rows[0]["date"] if rows else None,
                rows[-1]["date"] if rows else None,
            )
            if source != "naver":
                logger.warning("%s: 1차 소스(네이버) 실패, %s로 폴백됨", market, source)
            total += n

        await session.commit()
        logger.info("총 %d행 적재 완료", total)


if __name__ == "__main__":
    years = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    asyncio.run(main(years))
