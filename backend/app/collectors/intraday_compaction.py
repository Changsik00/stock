"""장중 누적 스냅샷 다운샘플링 배치 — PLAN.md §5.14.

`collectors/intraday_snapshot.py`가 매 틱마다 ``intraday_sample``에 원본
(``resolution_seconds=0``, 60초/7분 틱)을 쌓기만 하면 테이블이 무한정 비대해진다.
사용자 확인 보관 정책: **최근 7일은 원본 그대로, 8일 전부터는 15분 단위로 압축**
(TradingView/HTS·Prometheus/Grafana의 "최근 고해상도, 과거 저해상도" 룰업과 동일한
원리 — 모듈 상단 PLAN.md §5.14 설계 절 참고).

이 잡은 다른 collector들과 동일하게 ``collectors/base.py``의 ``run_job``이 세션
커밋/롤백을 관장하는 collect_fn 계약(``async def fn(session, target_date) -> int``)을
따른다 — `intraday_snapshot.py`의 record_* 함수들(즉시 자체 commit)과는 트랜잭션
경계가 다르다는 점에 주의.

**멱등성**: 압축 대상은 항상 ``resolution_seconds=0``이고 ``time < 기준일 - 7일``인
행뿐이다. 15분 버킷 평균을 ``resolution_seconds=900``으로 upsert한 직후, 방금 압축에
쓰인 원본 행을 곧바로 삭제한다 — 그러면 다음 실행부터는 그 구간에 압축 대상
(``resolution_seconds=0``)이 하나도 남지 않으므로, 매일 다시 돌려도 이미 압축된
구간을 재처리하지 않는다(안전하게 매일 반복 실행 가능)."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..market_hours import KST
from ..models import IntradaySample
from .base import REGISTRY

# 사용자 확인 보관 정책(PLAN.md §5.14): 최근 7일은 원본, 8일 전부터 압축 대상.
COMPACT_AFTER_DAYS = 7
BUCKET_SECONDS = 900  # 15분
BUCKET_MINUTES = BUCKET_SECONDS // 60


def _bucket_start(ts: dt.datetime) -> dt.datetime:
    """timestamp를 15분 버킷의 시작 시각으로 내림한다(초/마이크로초는 0으로)."""
    bucket_minute = (ts.minute // BUCKET_MINUTES) * BUCKET_MINUTES
    return ts.replace(minute=bucket_minute, second=0, microsecond=0)


async def compact_intraday_samples(
    session: AsyncSession, target_date: dt.date, series_keys: list[str] | None = None
) -> int:
    """``target_date`` 기준 ``target_date - COMPACT_AFTER_DAYS`` 이전(KST 자정
    기준)의 원본(``resolution_seconds=0``) 행을 series_key + 15분 버킷으로 묶어
    평균값을 압축본(``resolution_seconds=900``)으로 upsert하고, 압축에 쓰인 원본을
    삭제한다. 반환값은 생성/갱신된 압축 버킷 개수(REGISTRY의 다른 collect_fn들과
    동일하게 "rows written" 의미로 collect_log.rows에 기록됨).

    ``series_keys``를 주면 그 목록에만 스코프한다(기본 None = 전체 series_key,
    실제 배치 실행 시의 동작 그대로). **2026-07-23 추가** — 테스트가 먼 미래
    target_date(예: 2099년)를 써서 "8일 이전" 커트라인을 흉내내면, 그 기준으론
    실제 운영 데이터(오늘 쌓인 원본)도 전부 "오래된 것"이 돼버려 테스트 실행마다
    진짜 운영 데이터를 조기 압축해버리는 사고가 있었다(실측 확인: 2026-07-23
    새벽 원본 32건이 테스트 실행 중 15분 버킷으로 조기 압축됨). 테스트는 반드시
    이 파라미터로 자기 series_key만 스코프해서 이 사고를 재현하지 않는다."""
    cutoff = dt.datetime.combine(target_date - dt.timedelta(days=COMPACT_AFTER_DAYS), dt.time.min, tzinfo=KST)

    conditions = [
        IntradaySample.resolution_seconds == 0,
        IntradaySample.time < cutoff,
    ]
    if series_keys is not None:
        conditions.append(IntradaySample.series_key.in_(series_keys))

    rows = (
        await session.execute(
            select(IntradaySample.series_key, IntradaySample.time, IntradaySample.value).where(*conditions)
        )
    ).all()

    if not rows:
        return 0

    buckets: dict[tuple[str, dt.datetime], list[float]] = defaultdict(list)
    for series_key, time, value in rows:
        bucket_key = (series_key, _bucket_start(time))
        buckets[bucket_key].append(float(value))

    for (series_key, bucket_time), values in buckets.items():
        avg_value = sum(values) / len(values)
        stmt = pg_insert(IntradaySample).values(
            series_key=series_key,
            time=bucket_time,
            value=avg_value,
            resolution_seconds=BUCKET_SECONDS,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[IntradaySample.series_key, IntradaySample.time],
            set_={"value": stmt.excluded.value, "resolution_seconds": stmt.excluded.resolution_seconds},
        )
        await session.execute(stmt)

    # 압축에 쓰인 원본만 삭제 — 압축본(resolution_seconds=900, 위에서 upsert)은
    # 그대로 남는다. 같은 (series_key, time) PK를 원본과 압축본이 동시에 가질 일이
    # 없다는 전제(models.py IntradaySample 참고, 버킷 시작 시각이 원본 60초 틱의
    # 정확한 timestamp와 우연히 같을 확률은 사실상 0에 가깝다).
    await session.execute(IntradaySample.__table__.delete().where(*conditions))

    return len(buckets)


REGISTRY["intraday_compaction"] = compact_intraday_samples
