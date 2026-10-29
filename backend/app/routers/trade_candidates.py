"""수동 매수 종목 후보 관리 (PLAN.md §5.89-1) — 추천(읽기전용)/후보(사용자 고정) 조회·추가·삭제."""

from __future__ import annotations

import datetime as dt
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients.kiwoom import KiwoomClient
from ..db import get_session
from ..market_hours import KST
from ..models import TradeCandidate

router = APIRouter(prefix="/api/trade-candidates", tags=["trade-candidates"])

CODE_RE = re.compile(r"^[0-9A-Z]{6}$")


class CandidateIn(BaseModel):
    code: str
    name: str | None = None
    multiple: int = 1


def _item(r: TradeCandidate) -> dict:
    return {
        "id": r.id,
        "code": r.code,
        "name": r.name,
        "multiple": r.multiple,
        "source": r.source,
        "scenario": r.scenario,
        "reason": r.reason,
        "valid_date": r.valid_date.isoformat() if r.valid_date else None,
    }


async def _lookup_name(code: str) -> str | None:
    try:
        async with KiwoomClient() as client:
            data = await client.stock_info(code)
    except Exception:  # noqa: BLE001
        return None
    name = str((data or {}).get("stk_nm") or "").strip()
    return name or None


@router.get("/")
async def list_candidates(
    source: str | None = Query(None, description="recommended | manual"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    today = dt.datetime.now(KST).date()
    out: dict[str, list] = {"recommended": [], "manual": []}
    if source in (None, "recommended"):
        rows = (
            await session.execute(
                select(TradeCandidate)
                .where(
                    TradeCandidate.source == "recommended",
                    TradeCandidate.active.is_(True),
                    TradeCandidate.valid_date == today,
                )
                .order_by(TradeCandidate.id)
            )
        ).scalars()
        out["recommended"] = [_item(r) for r in rows]
    if source in (None, "manual"):
        rows = (
            await session.execute(
                select(TradeCandidate)
                .where(TradeCandidate.source == "manual", TradeCandidate.active.is_(True))
                .order_by(TradeCandidate.id)
            )
        ).scalars()
        out["manual"] = [_item(r) for r in rows]
    return out


@router.post("/")
async def add_candidate(body: CandidateIn, session: AsyncSession = Depends(get_session)) -> dict:
    code = body.code.strip().upper()
    if not CODE_RE.match(code):
        raise HTTPException(400, "종목코드는 영숫자 6자리여야 합니다 (예: 233740, 0167A0)")
    dup = (
        await session.execute(
            select(TradeCandidate.id).where(
                TradeCandidate.source == "manual", TradeCandidate.code == code
            )
        )
    ).first()
    if dup:
        raise HTTPException(409, f"이미 후보에 있는 종목입니다: {code}")
    name = (body.name or "").strip() or await _lookup_name(code)
    if not name:
        raise HTTPException(400, f"종목명을 조회하지 못했습니다: {code}")
    row = TradeCandidate(
        code=code, name=name[:100], multiple=body.multiple, source="manual", active=True
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _item(row)


@router.delete("/{candidate_id}")
async def delete_candidate(candidate_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    row = await session.get(TradeCandidate, candidate_id)
    if row is None:
        raise HTTPException(404, "후보를 찾을 수 없습니다")
    if row.source != "manual":
        raise HTTPException(400, "추천 종목은 삭제할 수 없습니다")
    await session.delete(row)
    await session.commit()
    return {"deleted": candidate_id}
