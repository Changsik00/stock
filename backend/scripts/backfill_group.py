"""group_snapshot 초기 적재 — **과거 백필은 이 소스로 불가능하다**.

PLAN.md §4.6/§6 3.6-3이 "과거 미지원이면 오늘 것만 적재"를 지시했다. 실호출 검증
결과(2026-07-18) ``finance.naver.com/sise/sise_group.naver``는 날짜 파라미터를
전혀 받지 않는다 — 항상 "지금 시세" 스냅샷만 준다(clients/naver_group.py 모듈
docstring 참고, scripts/backfill_flow_rank.py와 동일한 제약 패턴).

그래서 이 스크립트는 collectors/group_snapshot.py를 한 번 실행해 **오늘 시점의
업종 79개 + 테마 266개 스냅샷만** 적재한다. 매일 배치(스케줄러 or 수동 트리거)를
반복 실행하면 group_snapshot에 날짜가 하루씩 누적되므로, 실질적인 "과거 백필"은
시간이 지나야만 자연히 채워진다.

Usage:
    python -m scripts.backfill_group
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
logger = logging.getLogger("backfill_group")

from app.collectors.base import run_job  # noqa: E402
from app.collectors.group_snapshot import collect_group_snapshot  # noqa: E402

JOB_NAME = "group_snapshot"


async def run() -> None:
    logger.info(
        "group_snapshot 백필: 소스가 날짜 쿼리를 지원하지 않아 과거 백필이 불가능해 "
        "오늘 시점 스냅샷만 적재합니다 (자세한 배경은 이 파일과 "
        "app/clients/naver_group.py 모듈 docstring 참고)."
    )
    result = await run_job(JOB_NAME, dt.date.today(), collect_group_snapshot)

    if result["status"] != "ok":
        logger.error("실패: %s", result.get("message"))
        return

    logger.info("완료: %d행 적재. %s", result["rows"], result.get("message") or "")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
