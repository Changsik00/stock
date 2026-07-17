"""value_rank 초기 적재 — **과거 백필은 이 소스로 불가능하다**.

PLAN.md §4.6 3.6-1이 요구한 "백필: 소스가 과거 지원 안 하면 오늘 것만"에 따라,
이 스크립트는 collectors/value_rank.py를 한 번 실행해 소스(모바일
``m.stock.naver.com/api/stocks/quantTop/{market}``)가 지금 시점에 제공하는
최신 거래대금 상위 스냅샷만 적재한다 — clients/naver_value_rank.py 모듈
docstring 참고: 이 API는 날짜 파라미터가 없어 "현재/가장 최근 거래일" 하나만
준다(collectors/flow_rank.py의 sise_deal_rank_iframe과 동일한 제약).

매일 배치(스케줄러 또는 수동 트리거)를 반복 실행하면 value_rank에 날짜가
하루씩 누적된다 — 진짜 과거 백필은 시간이 지나야만 자연히 채워진다.

이어서 flow_rank도 한 번 재수집한다(PLAN.md 지시 "flow_rank 재수집도 한 번
돌려 market 채우기") — collectors/flow_rank.py가 이번 작업에서 market 컬럼을
채우도록 수정됐으니, 재실행하면 소스가 지금 주는 최근 2거래일치가 market이
채워진 채로 upsert된다(그 이전 날짜의 과거 적재분은 여전히 market=NULL로
남는다 — 소스 자체가 날짜 쿼리를 지원하지 않아 재수집으로 소급할 수 없음).

Usage:
    python -m scripts.backfill_value_rank
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
logger = logging.getLogger("backfill_value_rank")

from app.collectors.base import run_job  # noqa: E402
from app.collectors.flow_rank import collect_flow_rank  # noqa: E402
from app.collectors.value_rank import collect_value_rank  # noqa: E402

VALUE_RANK_JOB = "value_rank"
FLOW_RANK_JOB = "flow_rank"


async def run() -> None:
    logger.info(
        "value_rank 백필: 소스가 날짜 쿼리를 지원하지 않아 '과거 N일'이 아니라 "
        "소스가 지금 제공하는 최신 거래대금 상위 스냅샷만 적재합니다 (자세한 배경은 "
        "이 파일의 모듈 docstring과 app/clients/naver_value_rank.py 참고)."
    )
    result = await run_job(VALUE_RANK_JOB, dt.date.today(), collect_value_rank)
    if result["status"] != "ok":
        logger.error("value_rank 실패: %s", result.get("message"))
        return
    logger.info("value_rank 완료: %d행 적재. %s", result["rows"], result.get("message") or "")

    logger.info("flow_rank 재수집(시장 컬럼 채우기 목적)을 이어서 실행합니다.")
    fr_result = await run_job(FLOW_RANK_JOB, dt.date.today(), collect_flow_rank)
    if fr_result["status"] != "ok":
        logger.error("flow_rank 재수집 실패: %s", fr_result.get("message"))
        return
    logger.info(
        "flow_rank 재수집 완료: %d행 적재(market 컬럼 포함). %s",
        fr_result["rows"],
        fr_result.get("message") or "",
    )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
