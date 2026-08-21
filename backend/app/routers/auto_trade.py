"""GET/POST /api/auto-trade/* — 완전자동매매 엔진(SOL AI반도체TOP2플러스,
0167A0, 트레일링 스탑) 상태 조회 · 킬스위치 · 매매일지 · 수동 매수/매도
(PLAN.md §5.54, §5.56).

주기적 자동 감지·실행(`place_buy_order`/`place_sell_order` 호출)은 여전히
오직 `collectors/auto_trader.run_auto_trade`/`watch_stop_loss`(60초/30초 폴링,
`collectors/live_refresh.py`에 배선)만 담당한다 — 이 자동 감지 엔진 자체는
이 라우터가 건드리지 않는다. 이 라우터는 그 엔진의 상태를 조회하고
(`GET /state`), 킬스위치를 켜고 끄고(`POST /toggle`), 감사 로그를 읽고
(`GET /log`), **추가로(PLAN.md §5.56, 2026-08-11)** 사용자가 대시보드에서
직접 매수/매도를 실행할 수 있는 수동 개입 경로(`POST /manual-buy`,
`POST /manual-sell`)를 제공한다 — 자동 감지를 대체하는 게 아니라 그 위에
얹는 수동 버튼이다. `manual-buy`/`manual-sell` 둘 다 자동 엔진과 동일한
`_position_lock`을 잡고, 동일한 안전장치(킬스위치는 진입에만 적용, 예산 가드,
`_best_fill_price`/`_execute_sell` 재사용)를 그대로 따른다 — 아래 각 엔드포인트
docstring 참고.

`AutoTradeState`는 id=1 고정 싱글턴 행(마이그레이션이 `enabled=False`로 시드).
행이 어떤 이유로든 없으면(마이그레이션 실행 전 등) 안전한 기본값(꺼짐)으로
방어적으로 생성한다."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients.kiwoom import MAX_ORDER_NOTIONAL_KRW, KiwoomClient
from ..collectors import intraday_snapshot
from ..collectors.auto_trader import (
    AUTO_TRADE_TOTAL_BUDGET_KRW,
    STATE_ID,
    TARGET_CODE,
    TARGET_QTY,
    TRADE_EVENT_TYPES,
    _best_fill_price,
    _cancel_unfilled_order_silently,
    _execute_sell,
    _log,
    _order_still_unfilled,
    _position_lock,
)
from ..db import get_session
from ..market_hours import KST
from ..models import AutoTradeLog, AutoTradeState
from ..quant.auto_trade_rules import check_entry_budget, foreign_flow_sign
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


@router.post("/manual-buy")
async def manual_buy_auto_trade(session: AsyncSession = Depends(get_session)) -> dict:
    """사용자가 대시보드에서 직접 매수 버튼을 눌러 실행하는 수동 진입
    (PLAN.md §5.54 위에 얹는 수동 개입 — 자동 감지 엔진 `run_auto_trade`/
    `watch_stop_loss` 자체는 건드리지 않는다). 자동 진입 경로(`_handle_idle`)와
    **동일한 안전장치(킬스위치, 예산 가드)를 전부 통과해야 하고, 동일한
    `_position_lock`을 잡아 동시 자동 진입/청산과 경합하지 않는다.**

    킬스위치가 꺼진 채로 수동 매수를 허용하면, 이후 그 포지션을 감시할
    `run_auto_trade`/`watch_stop_loss`(둘 다 맨 첫 줄에서 `state.enabled`를
    확인하고 꺼져 있으면 손절 판정조차 하지 않는다)도 함께 꺼진 상태가 돼
    포지션이 무방비로 남는다 — 그래서 킬스위치가 꺼져 있으면 수동 매수도
    거부한다(우회 경로 없음).

    검증 순서(하나라도 실패하면 주문 시도 자체를 하지 않고 에러 응답):
    1. 킬스위치 꺼짐 -> 409.
    2. status가 "idle"이 아님(이미 보유 중) -> 409.
    3. 현재가 조회 실패 -> 502.
    4. `check_entry_budget`(누적 예산 25,000원 + 주문 1건 캡 5만원 둘 다) 실패 -> 400.
    5. 키움 주문 자체가 실패(예외) -> 502, 상태는 갱신하지 않고 `event_type="error"` 로그만 남김.

    통과하면 `_best_fill_price`(매도 1호가)로 지정가를 산출해 `place_buy_order`를
    호출하고, `_handle_idle`이 매수 성공 후 하는 것과 동일하게 state를 갱신한다
    (status="holding", entry_price/entry_qty/entry_at/entry_order_no,
    entry_foreign_flow_sign까지). `AutoTradeLog`에 `event_type="manual_entry"`로
    기록한다.

    Returns 갱신된 state(`GET /state`와 동일한 직렬화 형태)."""
    async with _position_lock:
        state = await _get_or_create_state(session)

        if not state.enabled:
            raise HTTPException(
                status_code=409,
                detail=(
                    "킬스위치가 꺼져 있어 신규 진입 불가 — 수동 매수도 킬스위치를 우회하지 "
                    "않습니다(킬스위치가 꺼진 채로 진입하면 이후 자동 손절/청산 감시도 함께 "
                    "꺼진 상태라 포지션이 무방비로 남기 때문). 먼저 킬스위치를 켜세요."
                ),
            )
        if state.status != "idle":
            raise HTTPException(
                status_code=409,
                detail=f"이미 보유 중(status={state.status!r})이라 수동 매수 불가 — 한 번에 포지션 하나만 허용합니다.",
            )

        code = state.code or TARGET_CODE

        try:
            intraday = await _warm_stock_intraday(code, 1)
        except Exception as e:  # noqa: BLE001 - 조회 실패는 명확한 에러로 응답, 주문 시도 안 함
            raise HTTPException(status_code=502, detail=f"현재가 조회 실패: {e}") from e

        bars = intraday.get("bars") if intraday else None
        if not bars:
            raise HTTPException(status_code=502, detail="현재가 데이터 없음(장 마감 등) — 수동 매수 불가")

        current_price = bars[-1]["close"]
        notional = TARGET_QTY * current_price
        if not check_entry_budget(state.status, notional, AUTO_TRADE_TOTAL_BUDGET_KRW, MAX_ORDER_NOTIONAL_KRW):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"예산 가드 실패: notional={notional}(qty={TARGET_QTY} x price={current_price}), "
                    f"budget={AUTO_TRADE_TOTAL_BUDGET_KRW}, max_order_notional={MAX_ORDER_NOTIONAL_KRW}"
                ),
            )

        try:
            async with KiwoomClient() as client:
                order_price = await _best_fill_price(client, code, "buy")
                if order_price is None:
                    raise RuntimeError("매도호가를 확인할 수 없어 주문가를 정할 수 없음")
                order_response = await client.place_buy_order(code, TARGET_QTY, int(order_price))
                order_no = str(order_response.get("ord_no")) if order_response.get("ord_no") else None
                unfilled = await _order_still_unfilled(client, order_no)
                if unfilled:
                    await _cancel_unfilled_order_silently(client, code, order_no, TARGET_QTY)
        except Exception as e:  # noqa: BLE001 - 주문 실패는 상태 변경 없이 로그만
            await _log(
                session,
                event_type="error",
                code=code,
                price=current_price,
                reason=f"수동 매수 주문 시도 실패: {e}",
                signal_snapshot={"current_price": current_price, "source": "manual"},
            )
            raise HTTPException(status_code=502, detail=f"매수 주문 실패: {e}") from e

        if unfilled:
            # PLAN.md §5.71 — `_handle_idle`과 동일한 이유(collectors/
            # auto_trader.py 모듈 상단 "미체결 확인" 절 참고). 주문은 접수됐지만
            # 체결 미확인(취소 시도함) — status를 holding으로 넘기지 않는다.
            await _log(
                session,
                event_type="buy_unconfirmed",
                code=code,
                price=order_price,
                reason=(
                    f"사용자가 대시보드에서 수동으로 매수 주문을 실행함(지정가 {order_price}원, "
                    f"{TARGET_QTY}주)했으나 체결 미확인(취소 시도)"
                ),
                signal_snapshot={"current_price": current_price, "source": "manual"},
                order_response=order_response,
            )
            raise HTTPException(
                status_code=502,
                detail="매수 주문을 제출했지만 체결이 확인되지 않았습니다(취소 시도함) — 잠시 후 다시 시도하세요.",
            )

        now = dt.datetime.now(dt.timezone.utc)
        state.status = "holding"
        state.entry_price = order_price
        state.entry_qty = TARGET_QTY
        state.entry_at = now
        state.entry_order_no = str(order_response.get("ord_no")) if order_response.get("ord_no") else None
        state.peak_price = None

        # `_handle_idle`과 동일 — 진입 시점 외인 현물 누적 순매수 부호를 기록해
        # 둔다(이후 반전 감지용). 조회 실패해도 진입 자체는 이미 체결됐으니 막지 않는다.
        try:
            foreign_series = await intraday_snapshot.get_foreign_position_series(session, days=1)
            spot_points = foreign_series.get("spot") or []
            entry_flow_value = spot_points[-1]["value"] if spot_points else None
        except Exception:  # noqa: BLE001 - 조회 실패는 기록 생략, 진입 자체는 계속 진행
            entry_flow_value = None
        state.entry_foreign_flow_sign = foreign_flow_sign(entry_flow_value)

        await session.commit()
        await session.refresh(state)

        await _log(
            session,
            event_type="manual_entry",
            code=code,
            price=order_price,
            reason=(
                f"사용자가 대시보드에서 수동으로 매수 주문을 실행함(지정가 {order_price}원, "
                f"{TARGET_QTY}주), 진입 시점 외인 현물 수급 부호={state.entry_foreign_flow_sign!r}"
            ),
            signal_snapshot={"current_price": current_price, "source": "manual"},
            order_response=order_response,
        )

        return _serialize_state(state, current_price=order_price)


@router.post("/manual-sell")
async def manual_sell_auto_trade(session: AsyncSession = Depends(get_session)) -> dict:
    """사용자가 대시보드에서 직접 매도 버튼을 눌러 실행하는 수동 청산. **킬스위치
    상태는 확인하지 않는다** — 매도(청산)는 이 프로젝트의 절대 원칙대로 항상
    최우선/무조건 허용된다(킬스위치가 꺼져 있어도 보유 포지션은 팔 수 있어야
    한다). `_position_lock`을 잡아 자동 손절/트레일청산과 동시에 중복 매도가
    나가지 않게 한다.

    검증: status가 "holding"/"trailing"이 아니면 -> 409(보유 중이 아님).

    통과하면 손절/트레일청산/강제청산이 전부 공유하는 `_execute_sell`을 그대로
    호출한다(재구현 없음) — `event_type="exit_manual"`, reason은 평가 문구 없는
    사실 서술만. 주문 자체가 실패하면(`_execute_sell` 내부에서 이미
    `event_type="error"` 로그를 남기고 상태는 건드리지 않는다) 502로 응답한다.

    Returns 갱신된 state(`GET /state`와 동일한 직렬화 형태)."""
    async with _position_lock:
        state = await _get_or_create_state(session)

        if state.status not in ("holding", "trailing"):
            raise HTTPException(
                status_code=409,
                detail=f"보유 중이 아님(status={state.status!r})이라 수동 매도 불가",
            )

        code = state.code or TARGET_CODE

        # 자동매매 폴링과 동일한 방식으로 현재가를 조회한다(로그/스냅샷 용도 —
        # 실제 매도 주문가는 _execute_sell 내부의 _best_fill_price가 별도로
        # 실시간 호가를 조회해 정한다). 조회 실패해도 매도 자체는 막지 않는다
        # (매도는 항상 최우선 허용 원칙 — 가격 조회 실패로 매도가 막히면 안 됨).
        current_price = None
        try:
            intraday = await _warm_stock_intraday(code, 1)
            bars = intraday.get("bars") if intraday else None
            current_price = bars[-1]["close"] if bars else None
        except Exception:  # noqa: BLE001 - 조회 실패해도 매도는 계속 진행
            current_price = None
        if current_price is None:
            current_price = float(state.entry_price) if state.entry_price is not None else 0.0

        signal_snapshot = {"current_price": current_price, "source": "manual"}
        result: dict = {}
        await _execute_sell(
            session,
            state,
            code,
            current_price,
            "exit_manual",
            "사용자가 대시보드에서 수동으로 매도 주문을 실행함",
            signal_snapshot,
            result,
        )
        if result.get("action") == "sell_failed":
            raise HTTPException(status_code=502, detail=f"매도 주문 실패: {result.get('error')}")
        if result.get("action") == "sell_unconfirmed":
            # PLAN.md §5.71 — _execute_sell이 주문 제출은 했지만 체결을 확인
            # 못 해(취소 시도함) 포지션 상태를 그대로 유지한 경우.
            raise HTTPException(
                status_code=502,
                detail="매도 주문을 제출했지만 체결이 확인되지 않았습니다(취소 시도함) — "
                "포지션은 유지됩니다, 잠시 후 다시 시도하세요.",
            )

        await session.refresh(state)
        return _serialize_state(state, current_price=None)


@router.get("/log")
async def get_auto_trade_log(
    limit: int = Query(30, ge=1, le=200, description="페이지당 행 수"),
    offset: int = Query(0, ge=0, description="건너뛸 행 수(페이지네이션)"),
    date: dt.date | None = Query(None, description="KST 기준 이 날짜 하루만(생략 시 전체 기간)"),
    trades_only: bool = Query(
        True, description="실제 체결된 거래만(기본) — 차단/오류/체결미확인/트레일전환 등은 제외"
    ),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """매매일지(`AutoTradeLog`) — ts 내림차순(최신이 먼저). 킬스위치가 꺼져
    있거나 idle 상태에서 진입 조건 미충족인 폴링은 애초에 기록되지 않는다
    (`collectors/auto_trader.py` 모듈 docstring "노이즈 방지" 원칙).

    2026-08-18(PLAN.md §5.70) — 예전엔 `limit`(최대 1000, 기본 200)만 있어
    프런트가 항상 최대 200행을 한 화면에 그대로 뿌렸다("너무 방대하다"는
    지적 + 공휴일 주문 재시도 노이즈가 겹쳐 실제로 오작동처럼 보인 사고,
    `collectors/auto_trader.py`의 "오늘 데이터 아니면 스킵" 가드로 노이즈
    자체는 근본 수정했지만 페이지네이션/날짜 필터는 별개로 필요) — `offset`/
    `date`를 추가하고 기본 `limit`을 30으로 낮췄다. `date`가 있으면 KST
    기준 그 날짜의 00:00~다음날 00:00 범위로 좁힌다(``ts``는 UTC로 저장되지만
    비교는 KST 자정 경계로 해야 사용자가 보는 날짜와 일치한다).

    2026-08-21(PLAN.md §5.73, 사용자 지적 — "정보가 장황하기만 하지 유의미한
    정보를 제공한다고 생각하지는 않아") — `trades_only`(기본 True) 추가.
    §5.71의 반복 억제(같은 이벤트가 5분 이내 재발생하면 접기)로도 `entry_
    blocked_risk`류가 여전히 하루 수십 건씩 쌓였다(리스크 경보가 45분~1시간
    간격으로 계속 재확인되면 매번 새 사건으로 기록되므로) — 껐다 켜는 게
    아니라 아예 "실제로 주문이 체결된 이벤트"(`collectors/auto_trader.
    TRADE_EVENT_TYPES`)만 기본으로 보여주고, 차단/오류/체결미확인/트레일
    전환은 `trades_only=false`로 명시해야만 보이게 한다.

    Returns ``{"rows": [{"id", "ts", "event_type", "code", "price", "reason",
    "signal_snapshot", "order_response"}, ...], "total": int}`` — ``total``은
    같은 필터(``date``/``trades_only``) 기준 전체 건수(페이지네이션 UI가
    "다음" 버튼 활성화 여부/총 건수를 계산하는 데 씀).
    """
    filters = []
    if date is not None:
        start_utc = dt.datetime.combine(date, dt.time.min, tzinfo=KST).astimezone(dt.timezone.utc)
        end_utc = start_utc + dt.timedelta(days=1)
        filters.append(AutoTradeLog.ts >= start_utc)
        filters.append(AutoTradeLog.ts < end_utc)
    if trades_only:
        filters.append(AutoTradeLog.event_type.in_(TRADE_EVENT_TYPES))

    total = (
        await session.execute(select(func.count()).select_from(AutoTradeLog).where(*filters))
    ).scalar_one()

    stmt = (
        select(AutoTradeLog)
        .where(*filters)
        .order_by(AutoTradeLog.ts.desc())
        .offset(offset)
        .limit(limit)
    )
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
        ],
        "total": total,
    }
