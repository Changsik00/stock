"""업종/테마별 일별 스냅샷 수집 -> group_snapshot upsert (PLAN.md §4.6/§6 3.6-3).

소스: clients/naver_group.py(``sise_group.naver`` 목록 + ``sise_group_detail.naver``
상세). 업종(upjong) 79개 + 테마(theme) 266개를 각각 한 번씩 목록 조회한 뒤(페이징
없음 — naver_group.py 모듈 docstring 참고), 345개 그룹 전부에 대해 상세 페이지를
순차 조회해 구성 종목 거래대금을 합산한다(value). 목록 조회는 요청 간 0.4초, 상세
조회는 요청 간 0.3초 간격(PLAN.md 지시, 서버 부담 방지)을 둔다 — 345개 x ~0.3~0.5초
≈ 2~3분 소요.

value(거래대금)는 상세 페이지 합산으로 채운다. 개별 그룹의 상세 조회가 실패해도
(네트워크 오류, 파싱 실패 등) 그 그룹만 value=None으로 남기고 나머지 344개는 계속
수집한다(etf_master.py의 개별 ETF 실패 흡수 패턴과 동일한 취지) — 실패 건수는
collect_log의 message에 남긴다. market_sum(시가총액)은 목록/상세 페이지 어디에도
컬럼이 없어 항상 NULL로 적재한다(naver_group.py 모듈 docstring 참고).

target_date는 오늘(수집 시점) 날짜를 그대로 쓴다 — 이 소스는 "지금 시세" 스냅샷만
제공하고 과거 날짜 쿼리를 지원하지 않는다(sise_deal_rank_iframe과 동일한 제약).
그래서 scripts/backfill_group.py도 오늘 것만 적재한다(PLAN.md "과거 미지원이면
오늘 것만" 지시).

REGISTRY["group_snapshot"]로 등록된다 (collectors/flow_rank.py와 동일한 패턴 —
routers/admin.py가 이미 이 모듈을 import해 실행 가능하다).
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

# 네이버 요청 간 간격 (PLAN.md 지시) — 목록 조회(그룹 타입당 1회, 총 2회) 사이,
# 그리고 상세 조회(그룹당 1회, 총 345회) 사이에 각각 적용한다.
NAVER_REQUEST_DELAY_SECONDS = 0.4
DETAIL_REQUEST_DELAY_SECONDS = 0.3


def _fetch_group_blocking(group_type: str) -> list[dict]:
    time.sleep(NAVER_REQUEST_DELAY_SECONDS)
    return naver_group.fetch_group_snapshot(group_type)


def _fetch_values_blocking(rows_by_type: list[tuple[str, dict]]) -> int:
    """블로킹: (group_type, row) 목록 전체에 대해 그룹 상세 페이지를 순차 조회해
    row["value"]를 in-place로 채운다(요청 간 DETAIL_REQUEST_DELAY_SECONDS 간격).

    한 그룹의 상세 조회가 실패해도 나머지 그룹 수집을 막지 않는다 — 실패한 그룹은
    value=None으로 남는다(etf_master.py의 fetch_targets_with_analysis와 동일한
    개별 실패 흡수 패턴). Returns: 실패 건수.
    """
    fail_count = 0
    for i, (group_type, row) in enumerate(rows_by_type):
        if i > 0:
            time.sleep(DETAIL_REQUEST_DELAY_SECONDS)
        try:
            row["value"] = naver_group.fetch_group_value(group_type, row["no"])
        except Exception as e:  # noqa: BLE001 - isolate per-group detail failures
            logger.warning(
                "group_value(%s, no=%s, %s) 조회 실패: %s", group_type, row.get("no"), row.get("name"), e
            )
            row["value"] = None
            fail_count += 1
    return fail_count


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
            value=row.get("value"),
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
    """업종(upjong)·테마(theme) 그룹의 현재 등락률+거래대금 스냅샷을 group_snapshot에
    적재한다.

    target_date는 소스가 과거 날짜 쿼리를 지원하지 않아 그대로 date 컬럼에 쓰인다
    (수집 시점 = 스냅샷 시점이라는 전제, 모듈 docstring 참고).
    """
    counts: dict[str, int] = {}
    rows_by_group_type: dict[str, list[dict]] = {}
    rows_by_type: list[tuple[str, dict]] = []
    for group_type in GROUP_TYPES:
        rows = await asyncio.to_thread(_fetch_group_blocking, group_type)
        counts[group_type] = len(rows)
        rows_by_group_type[group_type] = rows
        rows_by_type.extend((group_type, row) for row in rows)

    fail_count = await asyncio.to_thread(_fetch_values_blocking, rows_by_type)

    total = 0
    for group_type, rows in rows_by_group_type.items():
        total += await _upsert_rows(session, target_date, group_type, rows)

    message = (
        f"업종 {counts.get('upjong', 0)}개, 테마 {counts.get('theme', 0)}개 적재 "
        f"(거래대금은 그룹 상세 페이지 구성 종목 합산; 상세 조회 실패 {fail_count}건은 "
        "value NULL. 시가총액은 소스에 컬럼 없어 항상 NULL)"
    )
    logger.info("group_snapshot: %s", message)
    return total, message


REGISTRY["group_snapshot"] = collect_group_snapshot
