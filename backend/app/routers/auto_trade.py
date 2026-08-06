"""GET/POST /api/auto-trade/* — 완전자동매매 엔진(SOL AI반도체TOP2플러스,
0167A0, 트레일링 스탑) 상태 조회 · 킬스위치 · 매매일지 (PLAN.md §5.54).

**이 라우터 자체는 주문을 내지 않는다** — 실제 주문(`place_buy_order`/
`place_sell_order`)은 오직 `collectors/auto_trader.run_auto_trade`(60초 폴링,
`collectors/live_refresh.py`에 배선)만 호출한다. 이 라우터는 그 엔진의 상태를
조회하고(`GET /state`), 킬스위치를 켜고 끄고(`POST /toggle`), 감사 로그를
읽는(`GET /log`) 세 가지만 한다.

`AutoTradeState`는 id=1 고정 싱글턴 행(마이그레이션이 `enabled=False`로 시드).
행이 어떤 이유로든 없으면(마이그레이션 실행 전 등) 안전한 기본값(꺼짐)으로
방어적으로 생성한다."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..collectors.auto_trader import STATE_ID
from ..db import get_session
from ..models import AutoTradeLog, AutoTradeState
from .stocks import _warm_stock_intraday

router = APIRouter(prefix="/api/auto-trade", tags=["auto-trade"])


class AutoTradeToggle(BaseModel):
    enabled: bool


def _serialize_state(row: AutoTradeState, current_price: float | None) -> dict:
    unrealized_pnl = unrealized_pnl_pct = None
    if current_price is not None and row.entry_price is not None and row.entry_qty:
        entry_price = float(row.entry_price)
        qty = row.entry_qty
        unrealized_pnl = round((current_price - entry_price) * qty, 4)
        unrealized_pnl_pct = round((current_price - entry_price) / entry_price * 100, 4) if entry_price else None

    return {
        "enabled": row.enabled,
        "status": row.status,
        "code": row.code,
        "entry_price": float(row.entry_price) if row.entry_price is not None else None,
        "entry_qty": row.entry_qty,
        "entry_at": row.entry_at.isoformat() if row.entry_at else None,
        "entry_order_no": row.entry_order_no,
        "peak_price": float(row.peak_price) if row.peak_price is not None else None,
        # PLAN.md §5.55-4(2026-08-06) — 진입 시점 외인 현물 누적 순매수 부호
        # ("positive"|"negative"|None). 이후 반전되면 조기 청산되는 근거값이라
        # 대시보드에서도 참고할 수 있게 그대로 노출한다.
        "entry_foreign_flow_sign": row.entry_foreign_flow_sign,
        "current_price": current_price,
        "unrealized_pnl": unrealized_pnl,
        "unrealized_pnl_pct": unrealized_pnl_pct,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def _get_or_create_state(session: AsyncSession) -> AutoTradeState:
    """id=1 싱글턴 행을 가져온다. 마이그레이션이 이미 `enabled=False`로 시드해
    두지만, 어떤 이유로든(마이그레이션 실행 전 등) 행이 없으면 방어적으로
    안전한 기본값(꺼짐)을 생성한다 — 이 함수가 새로 만드는 행도 항상
    ``enabled=False``다(킬스위치 기본 OFF 원칙, PLAN.md §5.54)."""
    row = await session.get(AutoTradeState, STATE_ID)
    if row is None:
        row = AutoTradeState(id=STATE_ID, enabled=False, status="idle")
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


@router.get("/state")
async def get_auto_trade_state(session: AsyncSession = Depends(get_session)) -> dict:
    """현재 자동매매 엔진 상태(`enabled`/`status`/진입 정보). `status`가
    "holding"|"trailing"이면 `_warm_stock_intraday`(§5.3 signals와 같은 소스)로
    현재가를 가져와 평가손익까지 계산해 붙인다 — 가격 조회가 실패해도(장 마감
    등) `current_price`/`unrealized_pnl`/`unrealized_pnl_pct`가 None일 뿐
    크래시하지 않는다(`routers/paper_trades.py::_get_live_price`와 동일한
    관대함).

    Returns ``{"enabled", "status", "code", "entry_price", "entry_qty",
    "entry_at", "entry_order_no", "peak_price", "entry_foreign_flow_sign",
    "current_price", "unrealized_pnl", "unrealized_pnl_pct", "updated_at"}``.
    """
    row = await _get_or_create_state(session)

    current_price = None
    if row.status in ("holding", "trailing"):
        try:
            intraday = await _warm_stock_intraday(row.code, 1)
        except Exception:  # noqa: BLE001 - 상태 조회 자체가 크래시하지 않도록
            intraday = None
        bars = intraday.get("bars") if intraday else None
        current_price = bars[-1]["close"] if bars else None

    return _serialize_state(row, current_price)


@router.post("/toggle")
async def toggle_auto_trade(body: AutoTradeToggle, session: AsyncSession = Depends(get_session)) -> dict:
    """킬스위치 on/off. 끌 때는 즉시 반영돼 다음 폴링부터 엔진이 아무 것도
    하지 않는다. **켤 때 이미 holding/trailing 중이면 그 상태를 그대로
    유지한다** — 포지션 정보(entry_price/entry_at/peak_price 등)를 건드리지
    않는다. 사용자가 끄고 켜는 것만으로 실제 보유 포지션 기록이 유실되면
    안 되기 때문이다(PLAN.md §5.54 안전 설계)."""
    row = await _get_or_create_state(session)
    row.enabled = body.enabled
    await session.commit()
    await session.refresh(row)
    return _serialize_state(row, current_price=None)


@router.get("/log")
async def get_auto_trade_log(
    limit: int = Query(200, ge=1, le=1000, description="최대 반환 행 수"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """매매일지(`AutoTradeLog`) — ts 내림차순(최신이 먼저). 킬스위치가 꺼져
    있거나 idle 상태에서 진입 조건 미충족인 폴링은 애초에 기록되지 않는다
    (`collectors/auto_trader.py` 모듈 docstring "노이즈 방지" 원칙).

    Returns ``{"rows": [{"id", "ts", "event_type", "code", "price", "reason",
    "signal_snapshot", "order_response"}, ...]}``.
    """
    stmt = select(AutoTradeLog).order_by(AutoTradeLog.ts.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return {
        "rows": [
            {
                "id": r.id,
                "ts": r.ts.isoformat(),
                "event_type": r.event_type,
                "code": r.code,
                "price": float(r.price) if r.price is not None else None,
                "reason": r.reason,
                "signal_snapshot": r.signal_snapshot,
                "order_response": r.order_response,
            }
            for r in rows
        ]
    }
