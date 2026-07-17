"""GET /api/etf/* — stubs. Implemented in Phase 3 (PLAN.md §6, Phase 3)."""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/etf", tags=["etf"])

_NOT_IMPLEMENTED = "ETF 데이터는 아직 준비되지 않았습니다 (Phase 3 예정)."


@router.get("/list")
def etf_list():
    raise HTTPException(501, _NOT_IMPLEMENTED)
