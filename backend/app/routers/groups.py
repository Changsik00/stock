"""GET /api/groups — 업종/테마별 최신(또는 지정일) 등락률 스냅샷 (PLAN.md §4.6/§6 3.6-3).

DB 전용 조회다(§5.4 "DB 캐싱 우선") — collectors/group_snapshot.py가 미리 적재해 둔
group_snapshot 테이블을 그대로 읽어 반환할 뿐, 이 라우터에서 네이버를 직접 호출하지
않는다.

**주의(작업 지시)**: 이 라우터는 아직 ``main.py``에 등록하지 않는다 — 병렬로 진행
중인 다른 작업과의 main.py 충돌을 피하기 위해 통합 단계에서 별도로 배선한다. 그래서
테스트도 이 모듈의 ``router``를 ``TestClient``/``ASGITransport``에 직접 include해서
검증한다(tests/test_groups_router.py 참고).
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import GroupSnapshot

router = APIRouter(tags=["groups"])

GROUP_TYPES = {"upjong", "theme"}


@router.get("/api/groups")
async def group_snapshot_list(
    type: str = Query("upjong", description="upjong(업종) 또는 theme(테마)"),
    date: dt.date | None = Query(None, description="생략 시 해당 group_type의 최신 날짜"),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    if type not in GROUP_TYPES:
        raise HTTPException(400, f"type must be one of {sorted(GROUP_TYPES)}")

    target_date = date
    if target_date is None:
        target_date = (
            await session.execute(
                select(func.max(GroupSnapshot.date)).where(GroupSnapshot.group_type == type)
            )
        ).scalar()

    if target_date is None:
        return []

    stmt = (
        select(GroupSnapshot)
        .where(GroupSnapshot.group_type == type, GroupSnapshot.date == target_date)
        .order_by(GroupSnapshot.change_rate.desc().nullslast())
    )
    rows = (await session.execute(stmt)).scalars().all()

    return [
        {
            "name": r.name,
            "change_rate": float(r.change_rate) if r.change_rate is not None else None,
            "value": r.value,
            "market_sum": r.market_sum,
        }
        for r in rows
    ]
