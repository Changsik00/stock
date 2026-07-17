"""ETF 마스터·구성종목(top10)·순유입 초기 적재 (PLAN.md §6 Phase 3.5-1).

collectors/etf_master.py를 1회 실행한다. 다른 backfill_*.py 스크립트(과거 N년을
날짜별로 순회)와 달리 **여러 날짜를 순회하지 않는다** — 이유는 소스 자체의 한계다:

- 구성종목(top10)은 네이버가 "현재" 스냅샷만 제공한다. 과거 특정 날짜의 top10
  구성을 조회하는 API가 없어 처음부터 여러 날짜를 만들어낼 방법이 없다 — 실행한
  날의 스냅샷 하나만 적재된다(PLAN.md §4.5 요구사항: "구성종목은 오늘 스냅샷만").
- 순유입(net_inflow)도 마찬가지로 ``etfAnalysis.cumulativeNetInflowList``가 매
  요청마다 "referenceDate 하루치"만 준다(clients/naver_etf.py 모듈독스트링 —
  1w/1m/3m/6m/ytd/1y는 다일 누적이라 diff 없이 일별화 불가). 즉 이 스크립트를
  실행한 시점의 referenceDate 하루치만 소급 적재되고, 그 이전 날짜는 이 소스로는
  영영 채울 수 없다.

그래서 "백필"의 의미가 다른 스크립트들과 다르다: 실제 다년치 이력을 만드는 게
아니라, **오늘 하루의 마스터/구성/순유입 스냅샷을 최초 적재**하는 것이 이 스크립트의
전부다. 이후 매일 스케줄러(collectors/scheduler.py, 이 작업은 건드리지 않음)로
``etf_master`` job이 반복 실행되면 그때부터 etf_stats.net_inflow가 자연스럽게
일별 시계열로 쌓인다.

Usage:
    python -m scripts.backfill_etf                    # 오늘 날짜로 1회 실행
    python -m scripts.backfill_etf --date 2026-07-17   # 특정 날짜 라벨로 실행
"""

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
logger = logging.getLogger("backfill_etf")

from app.collectors.base import run_job  # noqa: E402
from app.collectors.etf_master import collect_etf_master  # noqa: E402

JOB_NAME = "etf_master"


async def run(target_date: dt.date) -> None:
    logger.info("etf_master 1회 수집 시작 (target_date=%s)", target_date)
    result = await run_job(JOB_NAME, target_date, collect_etf_master)
    if result["status"] == "ok":
        logger.info(
            "완료: %d행 적재%s",
            result["rows"],
            f" ({result['message']})" if result.get("message") else "",
        )
    else:
        logger.error("실패: %s", result.get("message"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date", type=str, default=None, help="collect_log에 남길 target_date (기본: 오늘)"
    )
    args = parser.parse_args()
    target_date = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    asyncio.run(run(target_date))


if __name__ == "__main__":
    main()
