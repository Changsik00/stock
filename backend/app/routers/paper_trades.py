"""GET/POST/DELETE /api/paper-trades* — 가상 매매 기록(paper trade) 장부
(PLAN.md §5.51).

**이 라우터는 실제 주문 TR을 호출하지 않는다.** `KiwoomClient.place_buy_order`/
`place_sell_order`(§5.48, kt10000/kt10001)는 이 파일 어디에서도 import·호출하지
않는다 — 사용자가 §5.50 통합 뷰(호가·ETF 괴리율·나스닥선물 등)를 보고 이미
직접 내린 진입/청산 판단을 진입가·수량·청산가로 "기록"만 하고 손익을 계산해
주는 순수 로컬 장부다. house rule(§5)도 그대로 따른다 — 어떤 응답 필드/문구도
"사라"/"지금이 기회"/"강하다"/"약하다"/"유리하다" 같은 매매 판단을 만들지
않는다. 이미 기록된 가격으로 손익만 계산해 보여줄 뿐, 종합 판단은 전적으로
사용자 몫이다.

대상 종목은 §5.50 `PAIR_SETS`의 6개 코드(005930/000660/0193W0/0193T0/0193L0/
0197X0)로 한정한다 — 이 프로젝트에서 라이브 가격을 안정적으로 가져올 수 있는
종목이 지금은 이 6개뿐이라, 범용 "아무 종목이나 기록" 장부로 확장하지 않는다
(필요해지면 후속 확장, `models.PaperTrade` docstring 참고).

현재가 조회(`_get_live_price`)는 새 TR을 추가하지 않고 §5.50-2가 이미 만든
두 함수를 재조합한다 — 본주는 `routers.stocks._warm_stock_intraday`(ka10080
분봉의 마지막 close), ETF(레버리지/인버스)는 `routers.markets._fetch_etf_nav_safe`
(naver_etf 60초 캐시의 now_value). 두 경로 모두 실패하면 예외를 삼키고 None을
반환해, 열린 포지션 목록 조회가 크래시하지 않는다(§5.50-2와 동일한 관례).
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import PaperTrade
from .markets import PAIR_SETS, _fetch_etf_nav_safe
from .stocks import _warm_stock_intraday

router = APIRouter(prefix="/api/paper-trades", tags=["paper-trades"])

# PAIR_SETS 두 세트(samsung/hynix)에 등장하는 6개 코드 전부 — 본주 2 + 레버리지 2 +
# 인버스 2. 이 장부가 다룰 수 있는 종목을 이 집합으로 한정한다(§5.51 설계).
_ALLOWED_CODES: dict[str, str] = {
    leg["code"]: leg["name"] for pair_set in PAIR_SETS.values() for leg in pair_set.values()
}

_STOCK_CODES = {_set["stock"]["code"] for _set in PAIR_SETS.values()}

_ALLOWED_SIDES = {"buy", "sell"}
_ALLOWED_STATUS_FILTERS = {"open", "closed", "all"}


class PaperTradeCreate(BaseModel):
    code: str
    side: str  # "buy"(롱) | "sell"(숏)
    entry_price: float
    entry_qty: float
    note: str | None = None


class PaperTradeClose(BaseModel):
    exit_price: float


def _validate_code(code: str) -> str:
    """§5.50 PAIR_SETS 6개 코드만 허용 — 그 외는 400."""
    if code not in _ALLOWED_CODES:
        raise HTTPException(400, f"code must be one of {sorted(_ALLOWED_CODES)}")
    return _ALLOWED_CODES[code]


async def _get_live_price(code: str) -> float | None:
    """본주는 `_warm_stock_intraday`(ka10080 분봉 마지막 close), ETF(레버리지/
    인버스)는 `_fetch_etf_nav_safe`(naver_etf now_value)로 "지금" 가격을
    가져온다. 새 TR 없음 — §5.50-2가 이미 만든 함수를 그대로 재조합한다.
    어느 경로든 실패하면(장 마감, 키움/네이버 오류 등) None을 반환할 뿐 예외를
    전파하지 않는다 — 이 함수를 부르는 GET 목록 조회가 크래시하지 않게 한다."""
    if code in _STOCK_CODES:
        try:
            intraday = await _warm_stock_intraday(code, 1)
        except Exception:  # noqa: BLE001 - 열린 포지션 목록 조회가 크래시하지 않도록
            return None
        bars = intraday.get("bars") if intraday else None
        return bars[-1]["close"] if bars else None

    nav_data = await _fetch_etf_nav_safe()
    row = nav_data.get(code)
    return row.get("now_value") if row else None


def _compute_pnl(side: str, entry_price: float, qty: float, ref_price: float) -> tuple[float, float]:
    """손익 계산 — side="buy"(롱)는 기준가가 진입가보다 오르면 이익,
    side="sell"(숏)는 기준가가 진입가보다 내리면 이익. 기준가는 청산가(실현
    손익) 또는 현재가(미실현 손익) 둘 다에 쓰인다. pnl_pct는 진입 원금
    (entry_price*qty) 대비 비율(%). Returns (pnl, pnl_pct)."""
    if side == "buy":
        pnl = (ref_price - entry_price) * qty
    else:
        pnl = (entry_price - ref_price) * qty
    cost = entry_price * qty
    pnl_pct = round(pnl / cost * 100, 4) if cost else 0.0
    return round(pnl, 4), pnl_pct


def _serialize_open(row: PaperTrade, current_price: float | None) -> dict:
    unrealized_pnl = unrealized_pnl_pct = None
    if current_price is not None:
        unrealized_pnl, unrealized_pnl_pct = _compute_pnl(
            row.side, float(row.entry_price), float(row.entry_qty), current_price
        )
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "side": row.side,
        "entry_price": float(row.entry_price),
        "entry_qty": float(row.entry_qty),
        "entry_at": row.entry_at.isoformat(),
        "exit_price": None,
        "exit_at": None,
        "status": row.status,
        "note": row.note,
        "current_price": current_price,
        "unrealized_pnl": unrealized_pnl,
        "unrealized_pnl_pct": unrealized_pnl_pct,
        "realized_pnl": None,
        "realized_pnl_pct": None,
    }


def _serialize_closed(row: PaperTrade) -> dict:
    realized_pnl = realized_pnl_pct = None
    if row.exit_price is not None:
        realized_pnl, realized_pnl_pct = _compute_pnl(
            row.side, float(row.entry_price), float(row.entry_qty), float(row.exit_price)
        )
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "side": row.side,
        "entry_price": float(row.entry_price),
        "entry_qty": float(row.entry_qty),
        "entry_at": row.entry_at.isoformat(),
        "exit_price": float(row.exit_price) if row.exit_price is not None else None,
        "exit_at": row.exit_at.isoformat() if row.exit_at else None,
        "status": row.status,
        "note": row.note,
        "current_price": None,
        "unrealized_pnl": None,
        "unrealized_pnl_pct": None,
        "realized_pnl": realized_pnl,
        "realized_pnl_pct": realized_pnl_pct,
    }


@router.post("")
async def create_paper_trade(
    body: PaperTradeCreate, session: AsyncSession = Depends(get_session)
) -> dict:
    """진입 기록 — 실제 주문을 내지 않는다. 사용자가 이미 직접 내린 진입 판단을
    진입가/수량으로 기록만 한다. `code`는 §5.50 PAIR_SETS 6개 코드만 허용(그 외
    400), `side`는 "buy"(롱)/"sell"(숏)만 허용(그 외 400). `name`은 PAIR_SETS에서
    찾아 채운다. `entry_at`은 서버 now(UTC). status="open"으로 insert 후 생성된
    행을 반환한다."""
    name = _validate_code(body.code)
    if body.side not in _ALLOWED_SIDES:
        raise HTTPException(400, f"side must be one of {sorted(_ALLOWED_SIDES)}")
    if body.entry_price <= 0 or body.entry_qty <= 0:
        raise HTTPException(400, "entry_price and entry_qty must be positive")

    row = PaperTrade(
        code=body.code,
        name=name,
        side=body.side,
        entry_price=body.entry_price,
        entry_qty=body.entry_qty,
        entry_at=dt.datetime.now(dt.timezone.utc),
        status="open",
        note=body.note,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _serialize_open(row, current_price=None)


@router.post("/{trade_id}/close")
async def close_paper_trade(
    trade_id: int, body: PaperTradeClose, session: AsyncSession = Depends(get_session)
) -> dict:
    """청산 기록 — 실제 주문을 내지 않는다. 사용자가 이미 직접 내린 청산 판단을
    청산가로 기록만 한다. id가 없으면 404, 이미 closed면(중복 청산 방지) 409.
    `exit_at`은 서버 now(UTC), status→closed. 갱신된 행 + realized_pnl/
    realized_pnl_pct를 반환한다."""
    row = await session.get(PaperTrade, trade_id)
    if row is None:
        raise HTTPException(404, f"paper trade {trade_id} not found")
    if row.status == "closed":
        raise HTTPException(409, f"paper trade {trade_id} is already closed")
    if body.exit_price <= 0:
        raise HTTPException(400, "exit_price must be positive")

    row.exit_price = body.exit_price
    row.exit_at = dt.datetime.now(dt.timezone.utc)
    row.status = "closed"
    await session.commit()
    await session.refresh(row)
    return _serialize_closed(row)


@router.delete("/{trade_id}", status_code=204, response_model=None)
async def delete_paper_trade(trade_id: int, session: AsyncSession = Depends(get_session)) -> None:
    """오기록 삭제 — 사람이 손으로 입력하는 장부라 오타 정정 수단이 필요하다
    (§5.51 설계). id가 없으면 404."""
    row = await session.get(PaperTrade, trade_id)
    if row is None:
        raise HTTPException(404, f"paper trade {trade_id} not found")
    await session.delete(row)
    await session.commit()


@router.get("")
async def list_paper_trades(
    status: str = Query("all", description="'open'|'closed'|'all'"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """목록 — entry_at 내림차순(최근 기록이 먼저). status="open" 행마다
    `_get_live_price(code)`로 현재가/미실현손익을 계산해 붙인다(가격을 못
    가져오면 current_price/unrealized_pnl/unrealized_pnl_pct 셋 다 None —
    크래시하지 않는다). status="closed" 행은 저장된 exit_price로 실현손익을
    계산한다.

    Returns ``{"status": "open"|"closed"|"all", "rows": [{"id", "code", "name",
    "side", "entry_price", "entry_qty", "entry_at", "exit_price", "exit_at",
    "status", "note", "current_price", "unrealized_pnl", "unrealized_pnl_pct",
    "realized_pnl", "realized_pnl_pct"}, ...]}``."""
    if status not in _ALLOWED_STATUS_FILTERS:
        raise HTTPException(400, f"status must be one of {sorted(_ALLOWED_STATUS_FILTERS)}")

    stmt = select(PaperTrade).order_by(PaperTrade.entry_at.desc())
    if status != "all":
        stmt = stmt.where(PaperTrade.status == status)
    rows = (await session.execute(stmt)).scalars().all()

    result = []
    for row in rows:
        if row.status == "open":
            current_price = await _get_live_price(row.code)
            result.append(_serialize_open(row, current_price))
        else:
            result.append(_serialize_closed(row))

    return {"status": status, "rows": result}
