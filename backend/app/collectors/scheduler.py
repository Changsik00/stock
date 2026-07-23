"""APScheduler AsyncIOScheduler — 평일 18:00 Asia/Seoul에 REGISTRY의 전 잡 실행 +
평일 07:30 Asia/Seoul에 "macro" 잡만 재실행(PLAN.md §5.22).

기본적으로 꺼져 있다. main.py의 lifespan이 ``ENABLE_SCHEDULER=1`` 환경변수가 설정된
경우에만 ``start_scheduler()``를 호출한다 (개발 중 의도치 않은 배치/외부 API 호출을
막기 위함, PLAN.md §5.1).

**2026-07-24 두 번째 cron 추가(미국장 조기 수집, PLAN.md §5.22)**: 사용자가
"나스닥/다우 어제자 정보가 아침에 안 맞는다"고 지적했다. 원인은 타임존: 미국
정규장은 한국시간 밤 22:30~23:30에 열려 다음날 새벽 05:00~06:00에 마감한다
(서머타임에 따라 ±1시간) — 즉 "어젯밤 미국장"은 한국시간 새벽에 이미 완전히
끝나 있는데, 이 모듈의 유일한 배치가 그날 저녁 18:00 한 번뿐이라 이미 새벽에
끝난 데이터를 그날 저녁까지 수집하지 않는 구조였다. 사용자가 아침에 대시보드를
열면 전날 미국장이 어땠는지 그날 저녁까지 알 수 없었다.

해결: ``collectors/macro.py::collect_macro``(환율/유가/미국지수 4종/KOFIA를
한 잡으로 묶어 ``REGISTRY["macro"]``에 등록됨)는 이미 ``LOOKBACK_DAYS=10`` 창 +
upsert로 **하루 두 번 실행해도 안전**(idempotent, 해당 모듈 docstring 참고)하므로
새 수집 로직 없이 **같은 잡을 아침에 한 번 더 도는 두 번째 cron만 추가**한다.
시각은 미국장 마감(새벽 05~06시, 서머타임 변동 감안)보다 넉넉히 늦고, 한국 NXT
프리마켓 시작(08:00)보다는 이른 **07:30 KST**로 잡는다. 아래 ``_run_all_jobs``
(18:00 전체 배치)와 달리 ``_run_macro_job``은 REGISTRY 전체를 순회하지 않고
"macro" 항목 하나만 ``run_job``으로 실행한다 — 나머지 잡들은 KRX 장 마감(정규장
15:30 이후 각종 확정치) 기준이라 18:00 스케줄에 남아야 맞다.

**2026-07-22 심각한 버그 수정(misfire_grace_time)**: 사용자가 "개인 방향성(파생ETF)
차트가 이틀치(07-15/07-20)뿐이라 이상하다"고 지적해 추적하다가, 지난 일주일 중
평일 18:00 배치가 **실제로 실행된 날이 거의 없다**는 걸 발견했다 — collect_log를
전수 조회하니 07-17·07-21 두 날짜만 전체 11개 잡이 다 찍혀 있었는데, 그 두 번 다
타임스탬프가 18:00대가 아니라 오후 3시대(사람이 수동으로 `_run_all_jobs`를
직접 실행한 시각, §5.6-4 참고)였다. 정작 정규 스케줄이 실제로 발동한 유일한
증거(worker 로그, 07-21 18:03:56 KST)는 "Run time of job ... was missed by
0:03:56" 경고만 남기고 **``_run_all_jobs`` 자체가 호출된 흔적(로그 첫 줄인
"scheduled batch starting..." INFO조차)이 전혀 없었다** — 즉 트리거는 맞았는데
실행은 스킵됐다.

원인: 이 ``add_job``에 ``misfire_grace_time``을 지정하지 않았다 — APScheduler
기본값은 매우 짧아서(사실상 초 단위), 이벤트 루프가 정확히 그 순간 한가하지
않으면(도커 컨테이너 CPU 경합 등으로 몇 분만 늦어도) "너무 늦었다"고 판단해
**아예 실행하지 않고 조용히 건너뛴다** — 예외도, 로그도 없이. 결과: 여러
날의 EOD 배치(수급 상위/거래대금 상위/ETF/등락종목수/지수일봉/매크로 등
11개 전부)가 통째로 비어 있었고, 이번 세션 내내 반복된 "어제/그제 걸로
멈춰있다" 지적들(§5.6-4 등) 중 다수가 사실 이 버그가 근본 원인이었다.

수정: ``misfire_grace_time``을 넉넉히(1시간) 줘서 이벤트 루프가 잠깐 늦어도
"1시간 이내"면 그냥 늦게라도 실행하게 한다 — 하루 한 번뿐인 배치라 정시가
아니라 "그날 안에 한 번은 확실히 도는 것"이 훨씬 중요하다."""

from __future__ import annotations

import datetime as dt
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .base import REGISTRY, run_job

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _run_all_jobs() -> None:
    target_date = dt.date.today()
    logger.info("scheduled batch starting for %s (%d jobs)", target_date, len(REGISTRY))
    for job_name, collect_fn in REGISTRY.items():
        await run_job(job_name, target_date, collect_fn)


async def _run_macro_job() -> None:
    """평일 07:30 KST 조기 실행분 — PLAN.md §5.22, 전날 새벽에 이미 마감한 미국장
    데이터를 그날 저녁 18:00까지 기다리지 않고 아침에 한 번 더 당겨온다.

    REGISTRY 전체를 순회하는 ``_run_all_jobs``와 달리 "macro" 잡 하나만 실행한다 —
    나머지 잡은 KRX 정규장 마감 이후 확정치 기준이라 이 시각에 돌릴 이유가 없다.
    """
    target_date = dt.date.today()
    collect_fn = REGISTRY.get("macro")
    if collect_fn is None:
        # register_all()이 collectors/macro.py를 import하지 않았다면(비정상 상황)
        # KeyError로 스케줄러 잡 자체를 죽이는 대신 경고만 남기고 넘어간다 — 이
        # 잡은 스케줄러 프로세스 안에서 반복 실행되므로, 한 번 실패했다고 이후
        # 실행까지 막히면 안 된다.
        logger.warning(
            "macro job not found in REGISTRY (registered: %s) — skipping 07:30 run",
            sorted(REGISTRY),
        )
        return
    logger.info("scheduled early macro batch starting for %s (US market catch-up)", target_date)
    await run_job("macro", target_date, collect_fn)


def start_scheduler() -> AsyncIOScheduler:
    """Create, start, and return the module-level scheduler (idempotent)."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
    scheduler.add_job(
        _run_all_jobs,
        CronTrigger(day_of_week="mon-fri", hour=18, minute=0, timezone="Asia/Seoul"),
        id="daily_batch",
        replace_existing=True,
        # 2026-07-22 버그 수정(모듈 docstring 참고) — 기본값(사실상 초 단위)이면
        # 이벤트 루프가 몇 분만 늦어도 그날 배치를 통째로 조용히 건너뛴다. 하루
        # 1회뿐인 배치라 "늦게라도 반드시 돈다"가 "정시"보다 훨씬 중요하다.
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _run_macro_job,
        CronTrigger(day_of_week="mon-fri", hour=7, minute=30, timezone="Asia/Seoul"),
        id="macro_morning",
        replace_existing=True,
        # 18:00 잡과 같은 이유(모듈 docstring의 2026-07-22 버그 참고)로 넉넉히 준다 —
        # 이 잡도 하루 한 번뿐이라 "늦게라도 반드시 돈다"가 "정시"보다 중요하다.
        misfire_grace_time=3600,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "scheduler started: weekday 18:00 Asia/Seoul daily batch (%d jobs registered: %s) "
        "+ weekday 07:30 Asia/Seoul macro-only catch-up (PLAN.md §5.22)",
        len(REGISTRY),
        sorted(REGISTRY),
    )
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("scheduler stopped")
