"""GET /api/admin/status, POST /api/admin/collect/{job} (PLAN.md §5.3)."""

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..collectors import register_all
from ..collectors.base import REGISTRY, run_job
from ..db import get_session
from ..models import CollectLog

# 모든 collectors.* 모듈을 임포트해 REGISTRY를 채운다(collectors/__init__.py 참고) —
# app/worker.py도 같은 함수를 호출해 두 프로세스가 항상 같은 잡 목록을 보게 한다.
register_all()

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/status")
async def status(session: AsyncSession = Depends(get_session)) -> list[dict]:
    stmt = select(CollectLog).order_by(CollectLog.ran_at.desc()).limit(20)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "job": r.job,
            "target_date": r.target_date.isoformat(),
            "status": r.status,
            "rows": r.rows,
            "message": r.message,
            "ran_at": r.ran_at.isoformat() if r.ran_at else None,
        }
        for r in rows
    ]


@router.post("/collect/{job}")
async def trigger_collect(
    job: str,
    date: dt.date | None = Query(None, description="target date, default today"),
) -> dict:
    collect_fn = REGISTRY.get(job)
    if collect_fn is None:
        raise HTTPException(
            404, f"unknown job {job!r}. registered jobs: {sorted(REGISTRY)}"
        )
    target_date = date or dt.date.today()
    return await run_job(job, target_date, collect_fn)
