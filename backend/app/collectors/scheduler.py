"""APScheduler AsyncIOScheduler — 평일 18:00 Asia/Seoul에 REGISTRY의 전 잡 실행 +
평일 07:30 Asia/Seoul에 "macro" 잡만 재실행(PLAN.md §5.22) +
평일 19:30 Asia/Seoul에 "flow_rank" 잡만 재실행(PLAN.md §5.46).

기본적으로 꺼져 있다. main.py의 lifespan이 ``ENABLE_SCHEDULER=1`` 환경변수가 설정된
경우에만 ``start_scheduler()``를 호출한다 (개발 중 의도치 않은 배치/외부 API 호출을
막기 위함, PLAN.md §5.1).

**2026-07-31 세 번째 cron 추가(flow_rank 저녁 재수집, PLAN.md §5.46)**: 변동성
스트레스 점검 중 ``collect_log``를 직접 조회하다가, "flow_rank 수집기가 실패를
성공으로 거짓 기록한다"는 최초 의심과 달리 **수집기 자체(``collectors/flow_rank.py::
collect_flow_rank``)는 이미 정직하게 동작 중**임을 확인했다 — 소스(네이버
``sise_deal_rank_iframe.naver``, 날짜 쿼리 불가·"최근 2거래일 고정" 응답만 줌)가
반환한 날짜가 target_date와 다르면 ``message``에 "실제 적재된 날짜: ...
(요청한 target_date=...는 무시됨)"이라고 이미 정확히 남기고 있었다. 실제 증거:
2026-07-30 18:03 KST경 실행분의 collect_log.message가 "실제 적재된 날짜:
2026-07-28, 2026-07-29 (요청한 target_date=2026-07-30는 무시됨)"이었다 — 즉
그날 저녁 18:00 정기 배치 시점엔 네이버가 아직 그날(07-30) 최종 랭킹을 올리지
않아, 배치가 이미 알고 있던 07-28/07-29를 다시 적재했을 뿐 새 거래일로 전진하지
못했다. 문제는 두 가지였다: (a) 이 정보가 ``message`` 텍스트에만 있고 ``status``는
그대로 ``'ok'``라 아무도 걸러내지 못한다, (b) 더 근본적으로 **이 소스 특성상
18:00 정기 배치 시점엔 그날 데이터가 아직 없을 수 있다는 걸 알면서도 재시도
메커니즘이 없었다**.

이건 바로 위 §5.22(미국장 조기 수집)가 이미 풀었던 "정기 배치 시점에 소스가 아직
준비 안 됐을 수 있다"는 것과 똑같은 클래스의 문제라, 같은 해법을 그대로 적용한다:
``collectors/flow_rank.py::collect_flow_rank``는 모듈 자체 docstring이 명시하듯
"``run_job``에 어떤 target_date를 넘겨도 동일하게 동작"하는 idempotent 잡이므로
(소스가 실제로 반환한 날짜를 그대로 적재할 뿐, target_date는 쿼리에 쓰이지 않음),
새 수집 로직 없이 **같은 ``collect_flow_rank``를 저녁 늦은 시각에 한 번 더 돌리는
세 번째 cron만 추가**한다. §5.22의 아침 케이스는 "소스가 새벽에 이미 준비됐는데
배치가 너무 늦게(18:00) 돈다"는 정반대 문제였던 것과 달리, 이번은 "배치(18:00)가
소스보다 먼저 돈다"는 문제라 시각을 18:00보다 **뒤로** 늦춘다. 정확한 네이버
발행 시각에 대한 직접 증거는 없지만(§5.22처럼 "미국장 마감 05~06시"급의 명확한
외부 기준이 없음), 18:00과 지나치게 붙으면 소스가 여전히 준비 안 됐을 위험이
남고, 반대로 너무 늦은 밤으로 잡으면 "당일 저녁 신선도"라는 목적(다음날 아침이
아니라 그날 저녁 안에 확정치를 반영)에서 벗어난다. 그 중간으로 18:00 대비
1시간 30분의 여유를 준 **19:30 KST**를 선택했다 — 코스피/코스닥 정규장은
15:30에 마감하므로 19:30이면 마감 후 4시간이 지나 있어 네이버가 최종 랭킹을
정리할 시간은 충분히 준다고 보되, 자정 근처까지 늦추지는 않는다.
``misfire_grace_time``은 §5.22/18:00 잡과 동일한 이유(이 파일 2026-07-22
섹션 참고 — 이벤트 루프가 몇 분만 늦어도 그레이스가 짧으면 조용히 스킵된다)로
동일하게 3600초를 준다.

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


async def _run_flow_rank_catchup_job() -> None:
    """평일 19:30 KST 저녁 재실행분 — PLAN.md §5.46, 18:00 정기 배치 시점엔 네이버가
    아직 그날 최종 수급 랭킹을 발행하지 않았을 수 있어(실측: 07-30 18:03 실행분
    message가 07-28/07-29 재적재를 남김) 소스가 좀 더 준비됐을 시각에 한 번 더
    당겨온다.

    REGISTRY 전체를 순회하는 ``_run_all_jobs``와 달리 "flow_rank" 잡 하나만
    실행한다 — 나머지 잡은 이 타이밍 문제와 무관하므로 18:00 스케줄에 남아야 맞다.
    ``collect_flow_rank``는 target_date를 실제로 쓰지 않고 소스가 반환한 날짜를
    그대로 적재하는 idempotent 잡이라(collectors/flow_rank.py 모듈 docstring
    참고) 같은 target_date를 넘겨도 안전하다.
    """
    target_date = dt.date.today()
    collect_fn = REGISTRY.get("flow_rank")
    if collect_fn is None:
        # register_all()이 collectors/flow_rank.py를 import하지 않았다면(비정상
        # 상황) KeyError로 스케줄러 잡 자체를 죽이는 대신 경고만 남기고 넘어간다 —
        # 이 잡은 스케줄러 프로세스 안에서 반복 실행되므로, 한 번 실패했다고
        # 이후 실행까지 막히면 안 된다.
        logger.warning(
            "flow_rank job not found in REGISTRY (registered: %s) — skipping 19:30 run",
            sorted(REGISTRY),
        )
        return
    logger.info(
        "scheduled flow_rank catch-up batch starting for %s (source freshness re-run)",
        target_date,
    )
    await run_job("flow_rank", target_date, collect_fn)


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
    scheduler.add_job(
        _run_flow_rank_catchup_job,
        CronTrigger(day_of_week="mon-fri", hour=19, minute=30, timezone="Asia/Seoul"),
        id="flow_rank_catchup",
        replace_existing=True,
        # 18:00/07:30 잡과 같은 이유(모듈 docstring의 2026-07-22 버그 참고)로 넉넉히
        # 준다 — 이 잡도 하루 한 번뿐이라 "늦게라도 반드시 돈다"가 "정시"보다 중요하다.
        misfire_grace_time=3600,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "scheduler started: weekday 18:00 Asia/Seoul daily batch (%d jobs registered: %s) "
        "+ weekday 07:30 Asia/Seoul macro-only catch-up (PLAN.md §5.22) "
        "+ weekday 19:30 Asia/Seoul flow_rank-only catch-up (PLAN.md §5.46)",
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
