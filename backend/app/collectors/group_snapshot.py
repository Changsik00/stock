"""업종/테마별 일별 스냅샷 수집 -> group_snapshot upsert (PLAN.md §4.6/§6 3.6-3).

소스: clients/naver_group.py(``sise_group.naver``). 업종(upjong) 79개 + 테마(theme)
266개를 각각 한 번씩 조회한다(페이징 없음 — naver_group.py 모듈 docstring 참고).
요청 간 0.3~0.5초 간격(PLAN.md 지시, 서버 부담 방지)을 두므로 두 그룹 타입 사이에만
쉬면 된다(그룹 타입 내부는 페이지 하나라 추가 호출이 없음).

value(거래대금)·market_sum(시가총액)은 소스 목록 페이지에 없어 항상 NULL로
적재한다(naver_group.py 모듈 docstring 참고 — GroupSnapshot 모델이 애초에 이 값들을
NULL 허용으로 설계한 이유).

target_date는 오늘(수집 시점) 날짜를 그대로 쓴다 — 이 소스는 "지금 시세" 스냅샷만
제공하고 과거 날짜 쿼리를 지원하지 않는다(sise_deal_rank_iframe과 동일한 제약).
그래서 scripts/backfill_group.py도 오늘 것만 적재한다(PLAN.md "과거 미지원이면
오늘 것만" 지시).

REGISTRY["group_snapshot"]로 등록된다 (collectors/flow_rank.py와 동일한 패턴 —
routers/admin.py가 이 모듈을 import해야 실제로 실행 가능해진다; admin.py 배선은 이
작업 범위 밖).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import naver_group
from ..models import GroupSnapshot
from .base import REGISTRY

logger = logging.getLogger(__name__)

GROUP_TYPES = naver_group.GROUP_TYPES

# 네이버 요청 간 0.3~0.5초 간격 (PLAN.md 지시).
NAVER_REQUEST_DELAY_SECONDS = 0.4


def _fetch_group_blocking(group_type: str) -> list[dict]:
    time.sleep(NAVER_REQUEST_DELAY_SECONDS)
    return naver_group.fetch_group_snapshot(group_type)


async def _upsert_rows(
    session: AsyncSession, date: dt.date, group_type: str, rows: list[dict]
) -> int:
    count = 0
    for row in rows:
        stmt = pg_insert(GroupSnapshot).values(
            date=date,
            group_type=group_type,
            name=row["name"],
            change_rate=row["change_rate"],
            value=None,
            market_sum=None,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                GroupSnapshot.date,
                GroupSnapshot.group_type,
                GroupSnapshot.name,
            ],
            set_={
                "change_rate": stmt.excluded.change_rate,
                "value": stmt.excluded.value,
                "market_sum": stmt.excluded.market_sum,
            },
        )
        await session.execute(stmt)
        count += 1
    return count


async def collect_group_snapshot(session: AsyncSession, target_date: dt.date) -> tuple[int, str | None]:
    """업종(upjong)·테마(theme) 그룹의 현재 등락률 스냅샷을 group_snapshot에 적재한다.

    target_date는 소스가 과거 날짜 쿼리를 지원하지 않아 그대로 date 컬럼에 쓰인다
    (수집 시점 = 스냅샷 시점이라는 전제, 모듈 docstring 참고).
    """
    total = 0
    counts: dict[str, int] = {}
    for group_type in GROUP_TYPES:
        rows = await asyncio.to_thread(_fetch_group_blocking, group_type)
        counts[group_type] = len(rows)
        total += await _upsert_rows(session, target_date, group_type, rows)

    message = (
        f"업종 {counts.get('upjong', 0)}개, 테마 {counts.get('theme', 0)}개 적재 "
        "(거래대금/시총은 소스 목록 페이지에 없어 NULL)"
    )
    logger.info("group_snapshot: %s", message)
    return total, message


REGISTRY["group_snapshot"] = collect_group_snapshot
