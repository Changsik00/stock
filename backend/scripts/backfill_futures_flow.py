"""최근 N년(기본 3년) 영업일의 K200 선물 투자자별(개인/외국인/기관계) 순매수
초기 적재. PLAN.md §4.5 4.5-2. collectors/futures_flow.py(네이버
m.stock.naver.com/api/index/FUT/trend)를 하루씩 순회 호출한다.

- 진행률을 매 거래일마다 로그로 출력한다.
- 특정 일자 수집이 실패해도(재시도 3회 모두 실패) 그 날짜만 건너뛰고 계속
  진행한다 — 실패/재시도/로그 기록은 collectors.base.run_job에 위임한다(§6.5,
  이 스크립트는 이를 재구현하지 않는다).
- 재실행 시 이미 market_flow에 해당 (market='k200_futures', date) 행이 있는
  날짜는 다시 호출하지 않고 건너뛴다(휴장일 판별이 안 되므로 순수 날짜 존재
  여부로 skip 판단 — backfill_market_flow.py와 동일한 타협).
- 이 소스(clients/naver_futures_flow.py)는 Kiwoom 클라이언트처럼 내장
  rate limiter가 없으므로, 요청 간 REQUEST_DELAY_SECONDS(기본 0.5초, PLAN.md
  §4.5-2 지시)를 매 영업일 사이에 직접 둔다.

Usage:
    python -m scripts.backfill_futures_flow                       # 최근 3년
    python -m scripts.backfill_futures_flow --years 1              # 최근 1년
    python -m scripts.backfill_futures_flow --start 2026-06-15 --end 2026-07-14
    python -m scripts.backfill_futures_flow --days 90               # 최근 90일 (CI용)
"""

import argparse
import asyncio
import datetime as dt
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_futures_flow")

from sqlalchemy import select  # noqa: E402

from app.collectors.base import run_job  # noqa: E402
from app.collectors.futures_flow import MARKET, collect  # noqa: E402
from app.db import async_session_factory  # noqa: E402
from app.models import MarketFlow  # noqa: E402

JOB_NAME = "futures_flow"
REQUEST_DELAY_SECONDS = 0.5


def _weekdays(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        if d.weekday() < 5:  # Mon-Fri; 공휴일은 naver_futures_flow가 빈 결과로 알려줌
            yield d
        d += dt.timedelta(days=1)


async def _already_collected(target_date: dt.date) -> bool:
    async with async_session_factory() as session:
        stmt = select(MarketFlow.market).where(
            MarketFlow.market == MARKET, MarketFlow.date == target_date
        ).limit(1)
        result = await session.execute(stmt)
        return result.first() is not None


async def run(start: dt.date, end: dt.date) -> None:
    days = list(_weekdays(start, end))
    total = len(days)
    logger.info("backfill_futures_flow: %s ~ %s (%d 영업일 후보)", start, end, total)

    ok = skipped = empty = failed = 0

    for i, target_date in enumerate(days, start=1):
        if await _already_collected(target_date):
            skipped += 1
            logger.info("[%d/%d] %s 이미 적재됨, 건너뜀", i, total, target_date)
            continue

        result = await run_job(JOB_NAME, target_date, collect)
        time.sleep(REQUEST_DELAY_SECONDS)

        if result["status"] != "ok":
            failed += 1
            logger.warning("[%d/%d] %s 실패: %s", i, total, target_date, result.get("message"))
            continue

        if result["rows"] == 0:
            empty += 1
            logger.info("[%d/%d] %s 데이터 없음(휴장일 추정)", i, total, target_date)
        else:
            ok += 1
            logger.info("[%d/%d] %s -> %d행 적재", i, total, target_date, result["rows"])

    logger.info(
        "완료: 적재 %d일 / 스킵(기존) %d일 / 데이터없음 %d일 / 실패 %d일 (총 %d 영업일 후보)",
        ok,
        skipped,
        empty,
        failed,
        total,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, default=3, help="lookback window in years (default: 3)")
    parser.add_argument(
        "--days", type=int, default=None, help="lookback window in days (overrides --years if given)"
    )
    parser.add_argument("--start", type=str, default=None, help="override start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=None, help="override end date YYYY-MM-DD (default: today)")
    args = parser.parse_args()

    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    if args.start:
        start = dt.date.fromisoformat(args.start)
    elif args.days is not None:
        start = end - dt.timedelta(days=args.days)
    else:
        start = end - dt.timedelta(days=365 * args.years)

    asyncio.run(run(start, end))


if __name__ == "__main__":
    main()
