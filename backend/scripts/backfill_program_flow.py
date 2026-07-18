"""프로그램매매(차익/비차익) 히스토리 백필 — ka90010 페이지네이션 활용.

PLAN.md §4.5-4. `collectors/program_flow.py`의 `_fetch_page`(ka90010 한 콜당
최근 ~100거래일 한꺼번에 응답)를 `cont-yn`/`next-key`로 연속조회하며 원하는
lookback 구간을 커버할 때까지 반복한다.

날짜마다 1콜씩 순회하는 `backfill_market_flow.py`(ka10051) 패턴과 달리, 이
TR은 **한 페이지에 최근 100거래일치가 통째로** 온다(2026-07-19 실측 —
`clients/kiwoom.py`의 `program_trading_by_date` docstring, 25페이지 연속조회로
2016년까지 확인). 그래서 3년(약 750영업일) 백필도 시장당 페이지 ~8개 x
시장 2개 ≈ 16콜이면 끝난다 — market_flow의 3년 백필(~1,500콜, ~25분)과
비교해 압도적으로 저렴하다(예상 소요: 20~40초, rate limit 1 req/s 기준).

전체를 run_job 한 번으로 감싸(collect_log에는 job='program_flow',
target_date=end 한 건만 기록) 실패 시 collectors.base.run_job의 재시도
(3회, 지수 백오프)에 맡긴다 — 콜 수 자체가 적어 전체 재시도 비용도 낮다.

Usage:
    python -m scripts.backfill_program_flow                        # 최근 3년
    python -m scripts.backfill_program_flow --years 1               # 최근 1년
    python -m scripts.backfill_program_flow --start 2023-07-19 --end 2026-07-19
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_program_flow")

from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.clients.kiwoom import KiwoomClient  # noqa: E402
from app.collectors.base import run_job  # noqa: E402
from app.collectors.macro import upsert_series_rows  # noqa: E402
from app.collectors.program_flow import (  # noqa: E402
    MARKETS,
    SERIES_ARB,
    SERIES_NONARB,
    _fetch_page,
)

JOB_NAME = "program_flow"

# 3년 ≈ 8페이지/시장이면 충분하지만, 페이지당 실제 거래일 수가 예상보다 적은
# 경우(휴장일 밀집 등)를 대비해 여유 있게 상한을 둔다. 이 값에 도달하면
# 안전하게 중단하고 그때까지 적재한 결과를 보고한다.
MAX_PAGES_PER_MARKET = 40


async def _backfill_market(
    session: AsyncSession, client: KiwoomClient, market: str, end: dt.date, start: dt.date
) -> tuple[int, int, dt.date | None, dt.date | None]:
    """market 하나를 [start, end] 구간이 커버될 때까지 연속조회하며 upsert.

    Returns:
        (적재한 행 수, 소비한 페이지 수, 실제 커버한 최소 날짜, 최대 날짜).
    """
    rows_written = 0
    pages = 0
    cont_yn: str | None = None
    next_key: str | None = None
    min_date: dt.date | None = None
    max_date: dt.date | None = None

    while True:
        parsed, headers = await _fetch_page(client, market, end, cont_yn=cont_yn, next_key=next_key)
        pages += 1

        page_min = min((item["date"] for item in parsed), default=None)
        page_max = max((item["date"] for item in parsed), default=None)
        logger.info(
            "[%s] page %d: %d행 (range %s..%s), cont-yn=%s",
            market,
            pages,
            len(parsed),
            page_min,
            page_max,
            headers.get("cont-yn"),
        )

        if parsed:
            min_date = page_min if min_date is None else min(min_date, page_min)
            max_date = page_max if max_date is None else max(max_date, page_max)

            arb_rows = [
                {"date": item["date"], "value": item["arb_net"], "source": "kiwoom"}
                for item in parsed
            ]
            nonarb_rows = [
                {"date": item["date"], "value": item["nonarb_net"], "source": "kiwoom"}
                for item in parsed
            ]
            rows_written += await upsert_series_rows(session, arb_rows, SERIES_ARB[market])
            rows_written += await upsert_series_rows(session, nonarb_rows, SERIES_NONARB[market])

        reached_start = min_date is not None and min_date <= start
        if reached_start or headers.get("cont-yn") != "Y" or not headers.get("next-key"):
            break
        if pages >= MAX_PAGES_PER_MARKET:
            logger.warning("[%s] 안전 상한(%d페이지) 도달, 중단", market, MAX_PAGES_PER_MARKET)
            break

        cont_yn = headers["cont-yn"]
        next_key = headers["next-key"]

    return rows_written, pages, min_date, max_date


async def run(start: dt.date, end: dt.date) -> None:
    logger.info("backfill_program_flow: %s ~ %s", start, end)

    async def _collect_fn(session: AsyncSession, target_date: dt.date) -> tuple[int, str]:
        total_rows = 0
        summary_parts = []
        async with KiwoomClient() as client:
            for market in MARKETS:
                rows, pages, min_date, max_date = await _backfill_market(
                    session, client, market, target_date, start
                )
                total_rows += rows
                summary_parts.append(f"{market}: {pages}p/{rows}행/{min_date}..{max_date}")
        return total_rows, "; ".join(summary_parts)

    result = await run_job(JOB_NAME, end, _collect_fn)
    logger.info("완료: %s", result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, default=3, help="lookback window in years (default: 3)")
    parser.add_argument("--start", type=str, default=None, help="override start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=None, help="override end date YYYY-MM-DD (default: today)")
    args = parser.parse_args()

    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    start = dt.date.fromisoformat(args.start) if args.start else end - dt.timedelta(days=365 * args.years)

    asyncio.run(run(start, end))


if __name__ == "__main__":
    main()
