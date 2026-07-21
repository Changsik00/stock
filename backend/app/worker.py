"""일별 배치 전용 워커 프로세스 진입점 — `python -m app.worker`.

## 왜 API 프로세스에서 분리했나 (2026-07-21)

원래는 `main.py`의 lifespan이 `ENABLE_SCHEDULER=1`일 때 `backend` 서비스(uvicorn
--reload) 안에서 APScheduler 일별 배치(평일 18:00 KST)를 같이 띄웠다. 그런데
--reload는 파일이 바뀔 때마다 프로세스를 통째로 재시작하고, APScheduler의 잡 상태는
프로세스 메모리에만 있다 — 개발 중 코드 수정이 잦으면(서브에이전트 작업 하루에도
수십 번) 18:00을 무사히 통과하는 순간이 없어서 **일별 배치가 사실상 한 번도 못
돈다**. 실측: 도커 전환 후 이틀간 backend 컨테이너가 71회 재시작됐고, 그동안
`_run_all_jobs`가 단 한 번도 "executed" 로그를 남기지 못함 → DB의 모든 일별
테이블이 3~6일씩 뒤처져 있었다(PLAN.md §7 리스크 참고).

해결: 일별 배치를 **--reload 없는 별도 프로세스**(docker-compose.yml의 `worker`
서비스)로 분리한다. API 서버가 몇 번을 재시작하든 이 프로세스는 영향받지 않는다.
장중 60초/7분 라이브 리프레시(`live_refresh.py`)는 그대로 `backend`(API) 프로세스에
남긴다 — 그건 API 프로세스 자신의 인메모리 캐시를 데우는 것이라 분리하면 의미가
없다(API가 재시작되면 어차피 그 캐시도 새로 데워야 하고, 60초~7분 주기라 재시작
후 금방 회복된다 — 18:00 배치처럼 "그 순간을 놓치면 하루를 통째로 날리는" 성격이
아니다).
"""

from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv

from .collectors import register_all
from .collectors.scheduler import start_scheduler

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    register_all()  # REGISTRY를 채운다 — 안 하면 "0 jobs registered"로 배치가 공회전한다.
    start_scheduler()
    logger.info("worker: 일별 배치 스케줄러 기동 완료 — 평일 18:00 Asia/Seoul 대기 중")
    await asyncio.Event().wait()  # 영구 대기 (SIGTERM으로 컨테이너가 종료시킴)


if __name__ == "__main__":
    asyncio.run(main())
