"""flow_rank 초기 적재 — **과거 30일 백필은 이 소스로 불가능하다**.

PLAN.md §4.5/§6 Phase 3.5-2가 요구한 "최근 30일 백필"을 시도했으나, 실호출
검증 결과(2026-07-18) `finance.naver.com/sise/sise_deal_rank_iframe.naver`는
날짜 파라미터를 전혀 받지 않는다(`date=`/`day=`/`sdate=`/`gubun=` 등을 모두
시도했지만 무시됨 — clients/naver_rank.py 모듈 docstring 참고) — 항상 "최근
2거래일" 고정 응답만 준다.

그래서 이 스크립트는 PLAN.md의 대안 지시("안 되면 오늘 것만 적재하고 보고")에
따라 **collectors/flow_rank.py를 한 번 실행해, 소스가 지금 시점에 제공하는
최근 2거래일치만 적재**한다. 매일 배치(스케줄러 or 수동 트리거)를 반복 실행하면
flow_rank에 날짜가 하루씩 누적되므로, 실질적인 "과거 백필"은 시간이 지나야만
자연히 채워진다 — 이 스크립트를 여러 날 나눠 실행해도 소용없다(항상 같은 최근
2거래일을 돌려받는다).

Usage:
    python -m scripts.backfill_flow_rank
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
logger = logging.getLogger("backfill_flow_rank")

from app.collectors.base import run_job  # noqa: E402
from app.collectors.flow_rank import collect_flow_rank  # noqa: E402

JOB_NAME = "flow_rank"


async def run() -> None:
    logger.info(
        "flow_rank 백필: 소스가 날짜 쿼리를 지원하지 않아 '최근 30일'이 아니라 "
        "소스가 지금 제공하는 최근 2거래일만 적재합니다 (자세한 배경은 이 파일의 "
        "모듈 docstring과 app/clients/naver_rank.py 참고)."
    )
    result = await run_job(JOB_NAME, dt.date.today(), collect_flow_rank)

    if result["status"] != "ok":
        logger.error("실패: %s", result.get("message"))
        return

    logger.info("완료: %d행 적재. %s", result["rows"], result.get("message") or "")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
