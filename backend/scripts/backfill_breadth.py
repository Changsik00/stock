"""market_breadth 초기 적재 — **과거 소급은 이 소스로 불가능하다**.

clients/naver_breadth.py 모듈 docstring에서 실호출로 확인한 대로,
``finance.naver.com/sise/sise_index.naver``는 날짜 쿼리(``date=YYYYMMDD``)를
무시하고 항상 "지금" 시점의 값만 준다(scripts/backfill_flow_rank.py가 같은 이유로
sise_deal_rank_iframe.naver에 대해 내린 결론과 동일한 제약).

그래서 이 스크립트는 PLAN.md의 대안 지시("소급 가능하면 소급, 안 되면 오늘 것만
적재하고 보고")에 따라 **collectors/breadth.py를 한 번 실행해 오늘(호출 시점) 값만
적재**한다. 매일 배치(스케줄러, 평일 18:00 Asia/Seoul 장마감 후)를 반복 실행하면
market_breadth에 날짜가 하루씩 자연히 누적된다 — 이 스크립트를 여러 날짜 인자로
나눠 실행해도 과거 값은 얻을 수 없다(항상 같은 "지금" 값을 돌려받는다).

장중에 이 스크립트를 실행하면 그 시점의 잠정 등락 수가 target_date(기본 오늘)에
찍힌다는 점에 유의 — 정확한 "장마감 확정치"를 원하면 장마감(15:30) 이후에 실행할 것
(§3.5 원칙. 장중 조회 자체는 이 스크립트가 아니라 GET /api/markets/breadth/live로
한다 — 그 경로는 DB에 아무것도 쓰지 않는다).

Usage:
    python -m scripts.backfill_breadth
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
logger = logging.getLogger("backfill_breadth")

from app.collectors.base import run_job  # noqa: E402
from app.collectors.breadth import collect_breadth  # noqa: E402

JOB_NAME = "breadth"


async def run() -> None:
    logger.info(
        "breadth 백필: 소스가 날짜 쿼리를 지원하지 않아 과거 소급이 불가능합니다 — "
        "호출 시점(지금)의 코스피/코스닥 등락 종목수만 오늘 날짜로 적재합니다. "
        "자세한 배경은 이 파일과 app/clients/naver_breadth.py의 모듈 docstring 참고."
    )
    result = await run_job(JOB_NAME, dt.date.today(), collect_breadth)

    if result["status"] != "ok":
        logger.error("실패: %s", result.get("message"))
        return

    logger.info("완료: %d행 적재. %s", result["rows"], result.get("message") or "")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
