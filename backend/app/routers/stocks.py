"""GET /api/stocks/* — stubs. Implemented in Phase 2 (PLAN.md §6, Phase 2)."""

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/stocks", tags=["stocks"])

_NOT_IMPLEMENTED = "종목 데이터는 아직 준비되지 않았습니다 (Phase 2 예정)."


@router.get("/search")
def search_stocks(q: str = Query(...)):
    raise HTTPException(501, _NOT_IMPLEMENTED)


@router.get("/{code}/series")
def stock_series(code: str, days: int = Query(180, ge=1, le=1500)):
    raise HTTPException(501, _NOT_IMPLEMENTED)


@router.get("/{code}/whale")
def stock_whale(code: str):
    raise HTTPException(501, _NOT_IMPLEMENTED)
