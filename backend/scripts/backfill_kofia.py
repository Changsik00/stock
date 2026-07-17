"""3년치 KOFIA freesis(예탁금/신용융자/대차잔고) 시계열 초기 적재 (PLAN.md §6, Phase 1.5-2).

- investor_deposit: 증시자금추이(STATSCU0100000060) TMPV2
- credit_loan_kospi/credit_loan_kosdaq: 신용공여 잔고 추이(STATSCU0100000070) TMPV3/TMPV4
- lending_balance: 대차거래추이(STATSCU0100000140, 종목필터 미지정="전체") TMPV6

세 요청 모두 세션/쿠키 없이 단일 POST(``/meta/getMetaDataList.do``)로 전체 기간을
한 번에 받아올 수 있다 (실측: 3.5년 범위 요청 시 865건, 페이징 없음). 요청 간
0.8초 딜레이를 둬 freesis에 과도한 요청을 보내지 않는다 (clients/kofia.py 참고).

Usage: python -m scripts.backfill_kofia [years]  (기본 3년)
"""

import asyncio
import datetime as dt
import logging
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backfill_kofia")

from app.clients import kofia  # noqa: E402
from app.collectors.macro import upsert_series_rows  # noqa: E402
from app.db import async_session_factory  # noqa: E402

REQUEST_DELAY_SECONDS = 0.8


async def main(years: int = 3) -> None:
    end = dt.date.today()
    start = end - dt.timedelta(days=365 * years)

    async with async_session_factory() as session:
        total = 0

        with httpx.Client() as client:
            logger.info("증시자금추이(investor_deposit) 기간 조회: %s ~ %s", start, end)
            deposit_rows = kofia.fetch_investor_deposit(client, start, end)
            for row in deposit_rows:
                row["source"] = "kofia"
            n = await upsert_series_rows(session, deposit_rows, "investor_deposit")
            logger.info("investor_deposit: %d건 적재", n)
            total += n

            time.sleep(REQUEST_DELAY_SECONDS)

            logger.info("신용공여 잔고 추이(credit_loan) 기간 조회: %s ~ %s", start, end)
            credit_rows = kofia.fetch_credit_loan(client, start, end)
            for series, rows in credit_rows.items():
                for row in rows:
                    row["source"] = "kofia"
                n = await upsert_series_rows(session, rows, series)
                logger.info("%s: %d건 적재", series, n)
                total += n

            time.sleep(REQUEST_DELAY_SECONDS)

            logger.info("대차거래추이(lending_balance) 기간 조회: %s ~ %s", start, end)
            lending_rows = kofia.fetch_lending_balance(client, start, end)
            for row in lending_rows:
                row["source"] = "kofia"
            n = await upsert_series_rows(session, lending_rows, "lending_balance")
            logger.info("lending_balance: %d건 적재", n)
            total += n

        await session.commit()
        logger.info("총 %d건 적재 완료", total)


if __name__ == "__main__":
    years = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    asyncio.run(main(years))
