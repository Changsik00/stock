"""GET /api/admin/status, POST /api/admin/collect/{job} (PLAN.md §5.3)."""

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Importing a collector module here (for its `REGISTRY["job"] = collect_fn` side effect)
# is what makes that job runnable via POST /api/admin/collect/{job} and picked up by the
# scheduler. Add new collector imports below as they land, e.g.:
#   from ..collectors import market_flow as _market_flow_collector  # noqa: F401
from ..collectors import etf_master as _etf_master_collector  # noqa: F401
from ..collectors import flow_path as _flow_path_collector  # noqa: F401
from ..collectors import flow_rank as _flow_rank_collector  # noqa: F401
from ..collectors import macro as _macro_collector  # noqa: F401
from ..collectors import market_flow as _market_flow_collector  # noqa: F401
from ..collectors import ohlcv as _ohlcv_collector  # noqa: F401
from ..collectors.base import REGISTRY, run_job
from ..db import get_session
from ..models import CollectLog

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
