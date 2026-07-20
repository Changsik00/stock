"""GET /api/groups — 업종/테마별 최신(또는 지정일) 등락률 스냅샷 (PLAN.md §4.6/§6 3.6-3).

DB 전용 조회다(§5.4 "DB 캐싱 우선") — collectors/group_snapshot.py가 미리 적재해 둔
group_snapshot 테이블을 그대로 읽어 반환할 뿐, 이 라우터에서 네이버를 직접 호출하지
않는다.

**주의(작업 지시)**: 이 라우터는 아직 ``main.py``에 등록하지 않는다 — 병렬로 진행
중인 다른 작업과의 main.py 충돌을 피하기 위해 통합 단계에서 별도로 배선한다. 그래서
테스트도 이 모듈의 ``router``를 ``TestClient``/``ASGITransport``에 직접 include해서
검증한다(tests/test_groups_router.py 참고).

## GET /api/groups/live (PLAN.md §4.7 3단 갱신 주기, 2026-07-20 장중 실측)

장중 실측 결과 ``sise_group.naver`` 목록 페이지의 등락률(change_rate)이 장중
시세에 맞춰 갱신됨을 확인 — 5~10분 캐시로 편입한다. **그룹 상세 페이지(구성
종목 거래대금 합산, clients/naver_group.fetch_group_value)는 그룹당 1회씩
345회 호출에 2~3분이 걸려 5~10분 주기에 맞지 않으므로 라이브 엔드포인트에서는
호출하지 않는다** — 목록 페이지(그룹 타입당 1회, 총 2회)만 재조회해 등락률만
갱신하고, value(거래대금)/market_sum은 항상 null로 둔다(호출자가 필요하면
직전 EOD `/api/groups` 스냅샷의 value와 이름 기준으로 병합 — 이 라우터는 병합
책임이 없다, 프런트 DashboardPage가 담당). DB(group_snapshot)에는 쓰지 않는다
(§3.5 원칙).

**장 마감 게이트(2026-07-20, 신규 5~10분 티어 전체의 기본 원칙)**: 장 마감이면
``is_market_closed``로 걸러 네이버를 아예 호출하지 않는다. DB 폴백이 없으므로
마지막 캐시(있으면)를 ``market_closed: true``로 재사용하고, 캐시조차 없으면
빈 rows + ``market_closed: true``로 응답한다(502 아님).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import naver_group
from ..db import get_session
from ..market_hours import KST, is_market_closed
from ..models import GroupSnapshot

router = APIRouter(tags=["groups"])

GROUP_TYPES = {"upjong", "theme"}

# 5~10분 장중 라이브 캐시 TTL — collectors/live_refresh.py 신규 인터벌 잡과 맞춘다.
LIVE_TTL_SECONDS = 420  # 7분

_groups_live_cache: dict[str, dict] = {
    "upjong": {"ts": 0.0, "data": None},
    "theme": {"ts": 0.0, "data": None},
}
_groups_live_cache_lock = asyncio.Lock()


def _fetch_group_list_blocking(group_type: str) -> list[dict]:
    return naver_group.fetch_group_snapshot(group_type)


async def _warm_groups_live(group_type: str) -> dict:
    """groups/live 캐시(group_type별 독립)를 채우고 payload를 반환한다 — 라우트
    핸들러와 collectors/live_refresh.py의 5~10분 인터벌 잡이 공유한다."""
    now = time.monotonic()
    async with _groups_live_cache_lock:
        entry = _groups_live_cache[group_type]
        cached = entry["data"]
        if cached is not None and (now - entry["ts"]) < LIVE_TTL_SECONDS:
            return cached

        now_kst = dt.datetime.now(KST)
        if is_market_closed(now_kst):
            if cached is not None:
                payload = {**cached, "market_closed": True}
            else:
                payload = {
                    "type": group_type,
                    "rows": [],
                    "market_closed": True,
                    "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                }
            entry["data"] = payload
            entry["ts"] = now
            return payload

        try:
            rows = await asyncio.to_thread(_fetch_group_list_blocking, group_type)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"groups live fetch failed: {str(e)[:200]}") from e

        payload = {
            "type": group_type,
            "rows": [
                {"name": r["name"], "change_rate": r["change_rate"], "value": None, "market_sum": None}
                for r in rows
            ],
            "market_closed": False,
            "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        entry["data"] = payload
        entry["ts"] = now
        return payload


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


@router.get("/api/groups/live")
async def group_snapshot_live(
    type: str = Query("upjong", description="upjong(업종) 또는 theme(테마)"),
) -> dict:
    """업종/테마 등락률 장중 라이브(PLAN.md §4.7, 2026-07-20 실측 편입).

    목록 페이지만 재조회해 7분 메모리 캐시로 감싼다(모듈 docstring 참고 —
    거래대금 합산은 라이브에서 하지 않는다). DB(group_snapshot)에는 쓰지 않는다.

    Returns ``{"type": ..., "rows": [{"name", "change_rate", "value": null,
    "market_sum": null}, ...], "cached_at": iso8601}``.
    """
    if type not in GROUP_TYPES:
        raise HTTPException(400, f"type must be one of {sorted(GROUP_TYPES)}")
    return await _warm_groups_live(type)
