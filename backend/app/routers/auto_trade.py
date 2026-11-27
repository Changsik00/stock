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
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients.kiwoom import MAX_ORDER_NOTIONAL_KRW, KiwoomClient
from ..collectors import auto_trader as auto_trader_mod
from ..collectors import intraday_snapshot
from ..collectors.auto_trader import (
    AUTO_TRADE_TOTAL_BUDGET_KRW,
    STATE_ID,
    TARGET_CODE,
    TARGET_QTY,
    TRADE_EVENT_TYPES,
    _best_fill_price,
    _cancel_unfilled_order_silently,
    _confirm_holdings_qty,
    _settle_buy_fill,
    _execute_sell,
    _log,
    _position_lock,
)
from ..db import get_session
from ..market_hours import KST
from ..models import AutoTradeLog, AutoTradeState, ExitPreset, StockIntradayBar, StockOhlcv
from ..quant.auto_trade_backtest import run_backtest
from ..quant.auto_trade_intraday_backtest import run_intraday_backtest
from ..quant.auto_trade_performance import (
    ENTRY_EVENT_TYPES,
    EXIT_EVENT_TYPES,
    summarize_by_entry_source,
)
from ..quant import auto_trade_rules as _engine_rules
from ..quant import exit_rules as exit_rules_mod
from ..quant.auto_trade_rules import TARGET_NAME, check_entry_budget, compute_order_qty, foreign_flow_sign
from ..quant.etf_pair_metrics import PAIR_ETF_NAMES, PAIR_ETFS
from .stocks import _warm_stock_intraday

router = APIRouter(prefix="/api/auto-trade", tags=["auto-trade"])


class AutoTradeToggle(BaseModel):
    enabled: bool


class ExitRuleIn(BaseModel):
    """수동 매수 청산 조건 1개(Phase 5.118). 검증은 `quant/exit_rules.validate_rules`."""

    kind: str
    value: float
    activate_pct: float | None = None


class ManualBuyBody(BaseModel):
    """`rules`를 생략(None)하면 엔진 고정 규칙으로 관리(기존 동작). 목록을 주면(빈 목록 포함)
    이 포지션은 수동 진입으로 표시되고 사용자 조건 + 장마감·외인반전으로 청산된다."""

    rules: list[ExitRuleIn] | None = None
    # 주의(warnings)가 있으면 409로 돌려주고, 사용자가 확인한 뒤 true로 재요청해야 진행한다.
    acknowledge_warnings: bool = False


class ManualRulesBody(BaseModel):
    rules: list[ExitRuleIn]
    acknowledge_warnings: bool = False


class AnalyzeBody(BaseModel):
    rules: list[ExitRuleIn]
    entry_price: float | None = None
    peak: float | None = None


class PresetIn(BaseModel):
    name: str
    rules: list[ExitRuleIn]


def _rules_payload(rules: list[ExitRuleIn] | None) -> list[dict]:
    return [r.model_dump(exclude_none=True) for r in (rules or [])]


async def _recent_range_stats(session: AsyncSession, code: str, days: int = 20) -> dict | None:
    """최근 일봉으로 일중 변동 통계(평균 고저폭 %, 최대 상승폭 %)를 구한다. 5일 미만이면 None."""
    try:
        rows = (
            await session.execute(
                select(StockOhlcv.open, StockOhlcv.high, StockOhlcv.low)
                .where(StockOhlcv.code == code)
                .order_by(StockOhlcv.date.desc())
                .limit(days)
            )
        ).all()
    except Exception:  # noqa: BLE001 - 통계는 보조 정보라 실패해도 분석은 계속한다
        return None
    ranges, ups = [], []
    for o, h, lo in rows:
        if o and h and lo and o > 0:
            ranges.append((h - lo) / o * 100)
            ups.append((h / o - 1) * 100)
    if len(ranges) < 5:
        return None
    return {"avg_range_pct": sum(ranges) / len(ranges), "max_up_pct": max(ups), "days": len(ranges)}


async def _reference_price(code: str) -> float | None:
    """/state의 reference_price와 같은 소스 — 최근 1분봉 종가."""
    try:
        intraday = await _warm_stock_intraday(code, 1)
    except Exception:  # noqa: BLE001
        return None
    bars = intraday.get("bars") if intraday else None
    return bars[-1]["close"] if bars else None


def _gate_analysis(analysis: dict, acknowledge: bool) -> list[dict]:
    """errors -> 400(메시지 목록), 미확인 warnings -> 409(warnings 반환). 통과하면 정규화 조건."""
    if analysis["errors"]:
        raise HTTPException(
            status_code=400,
            detail={"message": " / ".join(analysis["errors"]), "errors": analysis["errors"]},
        )
    if analysis["warnings"] and not acknowledge:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "확인이 필요한 주의 사항이 있습니다.",
                "needs_acknowledge": True,
                "warnings": analysis["warnings"],
                "infos": analysis["infos"],
            },
        )
    return analysis["rules"]


def _load_manual_blob(row: AutoTradeState) -> dict | None:
    """수동 진입이면 저장된 {"rules","original","modified_at"}를, 아니면(또는 깨졌으면) None."""
    if row.entry_source != "manual":
        return None
    try:
        data = json.loads(row.manual_exit_rules or "")
        if isinstance(data.get("rules"), list):
            return data
    except Exception:  # noqa: BLE001 - 깨진 JSON은 None(엔진 고정 규칙 폴백, collectors와 동일)
        pass
    return None


def _decorate_rules(rules: list[dict], entry_price: float | None, peak: float | None) -> list[dict]:
    """조건별 라벨·발동가(원)·트레일 활성 여부를 붙인다(표시용)."""
    out = []
    for r in rules:
        item = dict(r)
        item["label"] = exit_rules_mod.describe_rule(r)
        item["kind_label"] = exit_rules_mod.KIND_LABEL[r["kind"]]
        if entry_price:
            item["trigger_price"] = exit_rules_mod.trigger_price(r, entry_price, peak)
            if r["kind"] == "trail_pct":
                item["active"] = exit_rules_mod.trail_active(r, entry_price, peak)
                if r.get("activate_pct") is not None:
                    item["activate_price"] = exit_rules_mod.trigger_price(
                        {"kind": "take_profit_pct", "value": r["activate_pct"]}, entry_price, None
                    )
        out.append(item)
    return out


def _engine_rule_constants() -> dict:
    """엔진 고정 규칙 값(표시용) — auto_trade_rules 상수를 그대로 노출한다(읽기 전용)."""
    return {
        "stop_loss_pct": abs(_engine_rules.STOP_LOSS_PCT),
        "stop_loss_pct_risk_alert": abs(_engine_rules.STOP_LOSS_PCT_RISK_ALERT),
        "trail_activate_pct": _engine_rules.TRAIL_ACTIVATE_PCT,
        "trail_giveback_pct": _engine_rules.TRAIL_GIVEBACK_PCT,
        "eod_forced_exit_start": _engine_rules.EOD_FORCED_EXIT_START.strftime("%H:%M"),
        "eod_forced_exit_end": _engine_rules.EOD_FORCED_EXIT_END.strftime("%H:%M"),
        # Phase 5.121 — 신규 진입 제한 시간대(표시용, 판정은 auto_trade_rules가 전담)
        "entry_block_windows": [
            [_engine_rules.ENTRY_BLOCK_OPEN_START.strftime("%H:%M"), _engine_rules.ENTRY_BLOCK_OPEN_END.strftime("%H:%M")],
            [_engine_rules.ENTRY_BLOCK_CLOSE_START.strftime("%H:%M"), _engine_rules.ENTRY_BLOCK_CLOSE_END.strftime("%H:%M")],
        ],
    }


def _entry_source_label(row: AutoTradeState) -> str | None:
    if row.status not in ("holding", "trailing"):
        return None
    return "manual" if _load_manual_blob(row) is not None else "auto"


def _serialize_manual(row: AutoTradeState) -> dict | None:
    blob = _load_manual_blob(row)
    if blob is None or row.status not in ("holding", "trailing"):
        return None
    entry = float(row.entry_price) if row.entry_price is not None else None
    peak = float(row.peak_price) if row.peak_price is not None else None
    original = blob.get("original") or blob["rules"]
    return {
        "rules": _decorate_rules(blob["rules"], entry, peak),
        "original": _decorate_rules(original, entry, None),
        "modified_at": blob.get("modified_at"),
        "has_stop": exit_rules_mod.has_stop(blob["rules"]),
        "summary": exit_rules_mod.describe_rules(blob["rules"]),
    }


def derive_mode(enabled: bool, status: str | None, entry_source: str | None) -> str:
    """Phase 5.121 — 화면 모드. 'stopped'|'watching'|'auto_holding'|'manual_holding'.
    보유 중이면 킬스위치와 무관하게 보유 모드(포지션은 그대로 존재)."""
    if status in ("holding", "trailing"):
        return "manual_holding" if entry_source == "manual" else "auto_holding"
    return "watching" if enabled else "stopped"


def build_entry_reason(log_row) -> dict | None:
    """진입 로그 행(ts·event_type·reason·signal_snapshot 속성) -> 화면용 dict. 없으면 None."""
    if log_row is None:
        return None
    signal = None
    raw = getattr(log_row, "signal_snapshot", None)
    if raw:
        try:
            signal = raw if isinstance(raw, dict) else json.loads(raw)
        except Exception:  # noqa: BLE001 - 깨진 스냅샷은 null로
            signal = None
    ts = getattr(log_row, "ts", None)
    return {
        "ts": ts.isoformat() if ts else None,
        "event_type": log_row.event_type,
        "reason": log_row.reason,
        "signal": signal,
    }


def build_exit_levels(
    entry_price: float | None,
    peak: float | None,
    entry_source: str | None,
    manual_rules: list[dict] | None,
) -> list[dict]:
    """보유 포지션의 발동가 목록 [{key,label,price,pct}] (표시용, 엔진 상수·exit_rules 재사용).
    자동: 손절(평상/리스크 경보)·트레일 전환가. 수동: 사용자 조건별 발동가."""
    if not entry_price or entry_price <= 0:
        return []

    def level(key: str, label: str, rule: dict) -> dict | None:
        price = exit_rules_mod.trigger_price(rule, entry_price, peak)
        if price is None:
            return None
        return {"key": key, "label": label, "price": price, "pct": round((price / entry_price - 1) * 100, 2)}

    if entry_source == "manual":
        out = []
        for i, r in enumerate(manual_rules or []):
            item = level(f"manual_{i}_{r['kind']}", exit_rules_mod.describe_rule(r), r)
            if item:
                out.append(item)
        return out
    specs = [
        ("stop_loss", "손절", {"kind": "stop_loss_pct", "value": abs(_engine_rules.STOP_LOSS_PCT)}),
        (
            "stop_loss_risk_alert",
            "손절(리스크 경보 시)",
            {"kind": "stop_loss_pct", "value": abs(_engine_rules.STOP_LOSS_PCT_RISK_ALERT)},
        ),
        ("trail_activate", "트레일 전환", {"kind": "take_profit_pct", "value": _engine_rules.TRAIL_ACTIVATE_PCT}),
    ]
    return [x for x in (level(k, lb, r) for k, lb, r in specs) if x]


def _serialize_state(
    row: AutoTradeState,
    current_price: float | None,
    reference_price: float | None = None,
    entry_reason: dict | None = None,
) -> dict:
    unrealized_pnl = unrealized_pnl_pct = None
    if current_price is not None and row.entry_price is not None and row.entry_qty:
        entry_price = float(row.entry_price)
        qty = row.entry_qty
        unrealized_pnl = round((current_price - entry_price) * qty, 4)
        unrealized_pnl_pct = round((current_price - entry_price) / entry_price * 100, 4) if entry_price else None

    entry_source = _entry_source_label(row)
    manual = _serialize_manual(row)
    peak_f = float(row.peak_price) if row.peak_price is not None else None
    entry_f = float(row.entry_price) if row.entry_price is not None else None
    held = row.status in ("holding", "trailing")
    return {
        # Phase 5.121 — 조회 전용 노출(판정·킬스위치와 무관)
        "mode": derive_mode(row.enabled, row.status, entry_source),
        "entry_reason": entry_reason if held else None,
        "exit_levels": build_exit_levels(entry_f, peak_f, entry_source, manual["rules"] if manual else None)
        if held
        else [],
        "last_eval": auto_trader_mod.LAST_EVAL,
        "enabled": row.enabled,
        "status": row.status,
        "code": row.code,
        "entry_price": float(row.entry_price) if row.entry_price is not None else None,
        "entry_qty": row.entry_qty,
        "entry_at": row.entry_at.isoformat() if row.entry_at else None,
        "entry_order_no": row.entry_order_no,
        # Phase 5.134 — 동시호가에 낸 매도 주문(체결 대기). 없으면 None.
        "pending_sell": {
            "order_no": row.pending_sell_order_no,
            "qty": row.pending_sell_qty,
            "event": row.pending_sell_event,
            "price": float(row.pending_sell_price) if row.pending_sell_price is not None else None,
            "at": row.pending_sell_at.isoformat(),
        }
        if getattr(row, "pending_sell_at", None) is not None
        else None,
        "peak_price": float(row.peak_price) if row.peak_price is not None else None,
        # PLAN.md §5.55-4(2026-08-06) — 진입 시점 외인 현물 누적 순매수 부호
        # ("positive"|"negative"|None). 이후 반전되면 조기 청산되는 근거값이라
        # 대시보드에서도 참고할 수 있게 그대로 노출한다.
        "entry_foreign_flow_sign": row.entry_foreign_flow_sign,
        "current_price": current_price,
        # Phase 5.118 — 대기 중 수동 매수 조건 미리보기용 기준가(최근 1분봉 종가, 주문가 아님).
        "reference_price": reference_price,
        "unrealized_pnl": unrealized_pnl,
        "unrealized_pnl_pct": unrealized_pnl_pct,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        # 엔진이 실제로 매매하는 대상(엔진 상수). DB 행 code와 다르면 db_code로
        # 그대로 노출해 불일치를 숨기지 않는다.
        "target": {
            "code": TARGET_CODE,
            "name": TARGET_NAME,
            # Phase 5.122 — 고정 수량 폐지: 보유 중이면 실제 수량, 대기 중이면 None(주문 시점에 산출).
            "qty": row.entry_qty if held else None,
            "max_order_notional": MAX_ORDER_NOTIONAL_KRW,
            "total_budget": AUTO_TRADE_TOTAL_BUDGET_KRW,
            "db_code": row.code,
        },
        # Phase 5.118 — 진입 방식('manual'|'auto'|None=포지션 없음)과 수동 진입의 사용자 청산 조건.
        "entry_source": entry_source,
        "manual_exit_rules": manual,
        "engine_rules": _engine_rule_constants(),
        # 관찰만 하는(주문 안 나감) 쌍 ETF.
        "observing": [
            {"code": c, "name": PAIR_ETF_NAMES.get(c, c), "multiple": m}
            for c, m in PAIR_ETFS.items()
        ],
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
    reference_price = None
    try:
        intraday = await _warm_stock_intraday(row.code, 1)
    except Exception:  # noqa: BLE001 - 상태 조회 자체가 크래시하지 않도록
        intraday = None
    bars = intraday.get("bars") if intraday else None
    last_close = bars[-1]["close"] if bars else None
    if row.status in ("holding", "trailing"):
        current_price = last_close
    else:
        reference_price = last_close  # 대기 중엔 수동 매수 조건 미리보기 기준가로만 쓴다

    entry_reason = None
    if row.status in ("holding", "trailing"):
        q = select(AutoTradeLog).where(AutoTradeLog.event_type.in_(("entry", "manual_entry")))
        if row.entry_at is not None:
            q = q.where(AutoTradeLog.ts >= row.entry_at - dt.timedelta(minutes=5))
        log_row = (await session.execute(q.order_by(AutoTradeLog.ts.desc()).limit(1))).scalars().first()
        entry_reason = build_entry_reason(log_row)

    return _serialize_state(row, current_price, reference_price, entry_reason)


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
async def manual_buy_auto_trade(
    body: ManualBuyBody | None = None, session: AsyncSession = Depends(get_session)
) -> dict:
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

    **Phase 5.118 — 사용자 청산 조건**: body의 `rules`(익절/손절/고점 대비 하락/목표가/손절가)를
    주면 이 포지션은 `entry_source='manual'`로 표시되고, 엔진이 그 조건 + 장마감 강제청산·외인
    수급 반전으로 실제 매도 주문을 낸다(기본 손절 -1.5%/경보 -0.8%는 적용하지 않음). 검증(주문
    전): `exit_rules.analyze_rules`의 errors(범위·중복·모순·즉시발동 등)면 400, warnings가 있는데
    `acknowledge_warnings`가 아니면 409(warnings 반환, 주문 안 나감). `rules`를
    생략하면 기존처럼 엔진 고정 규칙으로 관리된다. 빈 목록은 허용(손절 조건 없음 — UI가 경고).

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

        # Phase 5.118 — 사용자 청산 조건 검증(주문 전). None이면 엔진 고정 규칙(기존 동작).
        manual_rules: list[dict] | None = None
        if body is not None and body.rules is not None:
            stats = await _recent_range_stats(session, state.code or TARGET_CODE)
            analysis = exit_rules_mod.analyze_rules(_rules_payload(body.rules), current_price, stats=stats)
            manual_rules = _gate_analysis(analysis, body.acknowledge_warnings)

        # Phase 5.122 — 수량 = floor(min(주문캡, 누적예산, 주문가능금액) / 매도1호가), 주문 시점 산출.
        order_qty = 0
        order_price = None
        order_response: dict = {}
        orderable_cash: int | None = None
        block_note = ""
        fill_kind, filled_qty = "unconfirmed", 0
        try:
            async with KiwoomClient() as client:
                order_price = await _best_fill_price(client, code, "buy")
                if order_price is None:
                    raise RuntimeError("매도호가를 확인할 수 없어 주문가를 정할 수 없음")
                try:
                    orderable_cash = await client.get_orderable_cash()
                except Exception as e:  # noqa: BLE001 - 주문가능금액을 모르면 보류(보수적)
                    block_note = f"주문가능금액 조회 실패({e})"
                order_qty = compute_order_qty(
                    order_price, MAX_ORDER_NOTIONAL_KRW, AUTO_TRADE_TOTAL_BUDGET_KRW, orderable_cash
                )
                if order_qty >= 1 and not check_entry_budget(
                    state.status, order_qty * order_price, AUTO_TRADE_TOTAL_BUDGET_KRW, MAX_ORDER_NOTIONAL_KRW
                ):
                    block_note = f"예산 가드 실패(status={state.status})"
                    order_qty = 0
                if order_qty >= 1:
                    order_response = await client.place_buy_order(code, order_qty, int(order_price))
                    order_no = str(order_response.get("ord_no")) if order_response.get("ord_no") else None
                    confirmed = await _confirm_holdings_qty(client, code, order_qty)
                    fill_kind, filled_qty = await _settle_buy_fill(client, code, order_no, order_qty, confirmed)
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

        if order_qty < 1:
            detail = (
                f"주문 수량 0주 — 지정가 {order_price}원, 한도(주문캡 {MAX_ORDER_NOTIONAL_KRW}, "
                f"누적예산 {AUTO_TRADE_TOTAL_BUDGET_KRW}), 주문가능금액 {orderable_cash}원"
                + (f" / {block_note}" if block_note else "")
            )
            await _log(
                session,
                event_type="error",
                code=code,
                price=order_price,
                reason=f"수동 매수 보류(budget_blocked): {detail}",
                signal_snapshot={"current_price": current_price, "source": "manual"},
            )
            raise HTTPException(status_code=400, detail=f"예산 가드 실패: {detail}")

        if fill_kind == "unconfirmed":
            # PLAN.md §5.78(§5.71 후속) — `_handle_idle`과 동일한 이유(collectors/
            # auto_trader.py 모듈 상단 "미체결 확인" 절 참고). 주문은 접수됐지만
            # 실보유수량으로 체결이 확인되지 않음(취소 시도함) — status를
            # holding으로 넘기지 않는다.
            await _log(
                session,
                event_type="buy_unconfirmed",
                code=code,
                price=order_price,
                reason=(
                    f"사용자가 대시보드에서 수동으로 매수 주문을 실행함(지정가 {order_price}원, "
                    f"{order_qty}주)했으나 체결 미확인(실보유수량 불일치, 취소 시도)"
                ),
                signal_snapshot={"current_price": current_price, "source": "manual"},
                order_response=order_response,
            )
            raise HTTPException(
                status_code=502,
                detail="매수 주문을 제출했지만 체결이 확인되지 않았습니다(취소 시도함) — 잠시 후 다시 시도하세요.",
            )

        partial_note = f" [부분 체결 {filled_qty}/{order_qty}주, 잔량 취소]" if fill_kind == "partial" else ""

        now = dt.datetime.now(dt.timezone.utc)
        state.status = "holding"
        state.entry_price = order_price
        state.entry_qty = filled_qty
        state.entry_at = now
        state.entry_order_no = str(order_response.get("ord_no")) if order_response.get("ord_no") else None
        state.peak_price = None
        if manual_rules is not None:
            state.entry_source = "manual"
            state.manual_exit_rules = json.dumps(
                {"rules": manual_rules, "original": manual_rules, "modified_at": None}, ensure_ascii=False
            )
        else:
            state.entry_source = None
            state.manual_exit_rules = None

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
                f"{filled_qty}주){partial_note}, 진입 시점 외인 현물 수급 부호={state.entry_foreign_flow_sign!r}"
                + (
                    f" / 청산 조건: {exit_rules_mod.describe_rules(manual_rules)}"
                    + ("" if exit_rules_mod.has_stop(manual_rules) else " (손절 조건 없음)")
                    + " + 장마감 강제청산·외인 수급 반전"
                    if manual_rules is not None
                    else " / 청산: 엔진 고정 규칙"
                )
            ),
            signal_snapshot={
                "current_price": current_price,
                "source": "manual",
                "qty": filled_qty,
                **({"exit_rules": manual_rules} if manual_rules is not None else {}),
            },
            order_response=order_response,
        )

        return _serialize_state(state, current_price=order_price)


@router.put("/manual-exit-rules")
async def update_manual_exit_rules(
    body: ManualRulesBody, session: AsyncSession = Depends(get_session)
) -> dict:
    """보유 중인 **수동 진입** 포지션의 청산 조건 수정(Phase 5.118). 주문은 내지 않는다.
    수동 진입이 아니거나(자동 진입/보유 아님) 하면 409 — 엔진 고정 규칙은 어떤 경로로도
    바꿀 수 없다. 원래 조건(`original`)은 보존하고 `modified_at`을 남기며 매매일지에
    `manual_rules_update` 로그를 쓴다. 검증은 매수 시와 동일(현재가 기준 목표가/손절가)."""
    async with _position_lock:
        state = await _get_or_create_state(session)
        blob = _load_manual_blob(state)
        if state.status not in ("holding", "trailing") or blob is None:
            raise HTTPException(
                status_code=409,
                detail="수동 매수로 진입해 보유 중인 포지션에서만 청산 조건을 수정할 수 있습니다.",
            )
        code = state.code or TARGET_CODE
        try:
            intraday = await _warm_stock_intraday(code, 1)
            bars = intraday.get("bars") if intraday else None
            current_price = bars[-1]["close"] if bars else None
        except Exception:  # noqa: BLE001 - 현재가를 모르면 가격 대소 비교만 건너뛴다
            current_price = None
        entry = float(state.entry_price) if state.entry_price is not None else None
        peak = float(state.peak_price) if state.peak_price is not None else None
        stats = await _recent_range_stats(session, code)
        analysis = exit_rules_mod.analyze_rules(
            _rules_payload(body.rules), current_price, entry_price=entry, peak=peak, stats=stats
        )
        rules = _gate_analysis(analysis, body.acknowledge_warnings)

        before = exit_rules_mod.describe_rules(blob["rules"])
        now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
        state.manual_exit_rules = json.dumps(
            {
                "rules": rules,
                "original": blob.get("original") or blob["rules"],
                "modified_at": now_iso,
            },
            ensure_ascii=False,
        )
        await session.commit()
        await session.refresh(state)
        await _log(
            session,
            event_type="manual_rules_update",
            code=code,
            price=current_price,
            reason=(
                f"수동 매수 청산 조건 수정: [{before}] -> [{exit_rules_mod.describe_rules(rules)}]"
                + ("" if exit_rules_mod.has_stop(rules) else " (손절 조건 없음)")
            ),
            signal_snapshot={"source": "manual", "before": blob["rules"], "after": rules},
        )
        return _serialize_state(state, current_price=current_price)


@router.post("/manual-exit-rules/analyze")
async def analyze_manual_exit_rules(body: AnalyzeBody, session: AsyncSession = Depends(get_session)) -> dict:
    """청산 조건 분석(errors/warnings/infos/scenarios) — 순수 분석, 주문·상태 변경 없음.
    기준가는 `/state`의 reference_price와 같은 소스(최근 1분봉 종가). 보유 중이면 body에
    entry_price/peak가 없을 때 현재 포지션의 값을 쓴다."""
    state = await _get_or_create_state(session)
    code = state.code or TARGET_CODE
    ref = await _reference_price(code)
    entry, peak = body.entry_price, body.peak
    if entry is None and state.status in ("holding", "trailing") and state.entry_price is not None:
        entry = float(state.entry_price)
        if peak is None and state.peak_price is not None:
            peak = float(state.peak_price)
    stats = await _recent_range_stats(session, code)
    res = exit_rules_mod.analyze_rules(_rules_payload(body.rules), ref, entry_price=entry, peak=peak, stats=stats)
    res["reference_price"] = ref
    res["entry_price"] = entry
    return res


@router.get("/exit-presets")
async def list_exit_presets(session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = (await session.execute(select(ExitPreset).order_by(ExitPreset.id))).scalars().all()
    return [{"id": r.id, "name": r.name, "rules": r.rules} for r in rows]


@router.post("/exit-presets")
async def add_exit_preset(body: PresetIn, session: AsyncSession = Depends(get_session)) -> dict:
    name = body.name.strip()[:60]
    if not name:
        raise HTTPException(status_code=400, detail="프리셋 이름을 입력하세요.")
    # 저장은 errors만 막는다(warnings는 사용 시점에 다시 확인). 기준가 없이 % 위주로 검사.
    rules, errs = exit_rules_mod.validate_rules(_rules_payload(body.rules), None)
    if errs:
        raise HTTPException(status_code=400, detail={"message": " / ".join(errs), "errors": errs})
    if not rules:
        raise HTTPException(status_code=400, detail="저장할 조건이 없습니다.")
    row = ExitPreset(name=name, rules=rules)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {"id": row.id, "name": row.name, "rules": row.rules}


@router.delete("/exit-presets/{preset_id}")
async def delete_exit_preset(preset_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    row = await session.get(ExitPreset, preset_id)
    if row is None:
        raise HTTPException(status_code=404, detail="프리셋을 찾을 수 없습니다.")
    await session.delete(row)
    await session.commit()
    return {"deleted": preset_id}


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

        # Phase 5.122 — result["action"] == "sell_partial"이면 일부만 체결돼 잔량이 남은 상태
        # (entry_qty 갱신·status 유지됨). 오류가 아니라 정상 응답으로 현재 상태를 돌려준다.
        await session.refresh(state)
        return _serialize_state(state, current_price=None)


@router.post("/sync")
async def sync_auto_trade_state(session: AsyncSession = Depends(get_session)) -> dict:
    """대시보드 "동기화" 버튼(PLAN.md §5.82) — DB `AutoTradeState`와 실계좌
    (`get_account_holdings_qty`, kt00018) 보유수량을 직접 비교해 어긋나면
    수동으로 정정한다. `_confirm_holdings_qty`에 재시도를 추가했어도(같은
    Phase) 렉이 그보다 길면 여전히 놓칠 수 있어(§5.82의 두 실사고 모두 정확한
    지연 시간이 불명) — 그 경우를 위한 사용자 직접 트리거 안전망이다.
    `_position_lock`을 잡아 자동 엔진과 동시에 상태를 건드리지 않는다.

    비교 기준: status가 "holding"/"trailing"이면 기대 수량은 `state.entry_qty`(Phase 5.122, 없으면 1),
    "idle"이면 0.

    - 일치하면 아무 것도 바꾸지 않고 ``{"corrected": false, ...}``.
    - 실보유가 0인데 DB가 보유 중으로 남아있으면(9/4 실사고와 동일 방향) —
      status를 idle로, 포지션 필드를 전부 초기화. 진입가를 알고 있으니 항상
      안전하게 자동 정정할 수 있다.
    - 실보유가 있는데 DB가 idle이면(8/27 실사고와 동일 방향, 반대로 위험한
      쪽) — 이 프로젝트가 이미 갖고 있는 기록(`AutoTradeLog`의 가장 최근
      `entry`/`manual_entry`/`buy_unconfirmed`)에서 진입가를 그대로 가져와
      백필한다. kt00018 응답 자체에서 평균단가를 새로 파싱하지 않는다 — 이
      TR의 개별 필드명은 이미 두 번 실측 오판(§5.78/§5.79)이 있었던 곳이라
      새 필드를 또 추측하지 않고, 이미 검증된 우리 로그값만 재사용한다.
      참고할 로그가 없으면(추정 근거 전무) 자동 정정을 포기하고 409로
      사람에게 직접 확인을 요청한다 — 틀린 진입가로 손절/트레일 기준을
      잘못 세우는 것보다 아무것도 안 하는 게 안전하다.

    Returns ``{"corrected": bool, "actual_qty": int, "detail": str | None,
    "state": {...GET /state와 동일 직렬화...}}``."""
    async with _position_lock:
        state = await _get_or_create_state(session)
        code = state.code or TARGET_CODE
        expected_qty = int(state.entry_qty or TARGET_QTY) if state.status in ("holding", "trailing") else 0

        try:
            async with KiwoomClient() as client:
                actual_qty = await client.get_account_holdings_qty(code)
        except Exception as e:  # noqa: BLE001 - 조회 실패는 명확한 에러로 응답, 상태는 건드리지 않음
            raise HTTPException(status_code=502, detail=f"실계좌 잔고 조회 실패: {e}") from e

        if actual_qty == expected_qty:
            return {
                "corrected": False,
                "actual_qty": actual_qty,
                "detail": None,
                "state": _serialize_state(state, current_price=None),
            }

        if actual_qty == 0:
            prev_status = state.status
            await _log(
                session,
                event_type="error",
                code=code,
                price=float(state.entry_price) if state.entry_price is not None else None,
                reason=(
                    f"수동 동기화(대시보드): DB status={prev_status!r}(진입가 "
                    f"{state.entry_price}, {state.entry_qty}주)였으나 kt00018 실계좌 조회 결과 "
                    "실보유 0주 -> idle로 정정"
                ),
                signal_snapshot={"actual_holdings_qty": actual_qty, "expected_qty": expected_qty, "source": "manual_sync"},
            )
            state.status = "idle"
            state.entry_price = None
            state.entry_qty = None
            state.entry_at = None
            state.entry_order_no = None
            state.peak_price = None
            state.entry_foreign_flow_sign = None
            state.entry_source = None
            state.manual_exit_rules = None
            await session.commit()
            await session.refresh(state)
            return {
                "corrected": True,
                "actual_qty": actual_qty,
                "detail": f"status={prev_status!r} -> idle로 정정(실보유 0주 확인)",
                "state": _serialize_state(state, current_price=None),
            }

        if state.status != "idle":
            # 이 전략은 단일 종목·단일 포지션만 다루므로 status가 이미 holding/trailing인데
            # 실보유수량이 0도 entry_qty(기대 수량, Phase 5.122)도 아닌 조합은
            # 정상 흐름에서 나올 수 없다 — 뭘 근거로 자동 정정할지 애매하니(예:
            # peak_price/entry_order_no를 지워도 되는지 불확실) 사람 확인으로 넘긴다.
            raise HTTPException(
                status_code=409,
                detail=(
                    f"실보유 {actual_qty}주가 DB status={state.status!r}의 기대 수량({expected_qty}주)과도, "
                    "0주와도 맞지 않는 조합입니다 — 자동 정정 로직이 다루지 않는 경우라 직접 확인이 "
                    "필요합니다."
                ),
            )

        # actual_qty > 0인데 DB는 idle — 실계좌에는 있는데 DB가 모르는 포지션.
        # 우리 로그에서 가장 최근 매수 시도가를 근거로만 백필한다(위 docstring
        # 참고, kt00018 새 필드 추측 안 함).
        recent_stmt = (
            select(AutoTradeLog)
            .where(AutoTradeLog.code == code, AutoTradeLog.event_type.in_(["entry", "manual_entry", "buy_unconfirmed"]))
            .order_by(AutoTradeLog.ts.desc())
            .limit(1)
        )
        recent = (await session.execute(recent_stmt)).scalars().first()
        if recent is None or recent.price is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"실계좌에 {code} {actual_qty}주가 있는데 DB엔 포지션 기록이 없고(status="
                    f"{state.status!r}), 진입가를 추정할 참고 로그도 없습니다 — 자동 정정을 "
                    "포기합니다. 직접 확인 후 수동으로 상태를 맞춰야 합니다."
                ),
            )

        prev_status = state.status
        await _log(
            session,
            event_type="error",
            code=code,
            price=float(recent.price),
            reason=(
                f"수동 동기화(대시보드): kt00018 실계좌 조회 결과 {code} {actual_qty}주 보유 확인됐으나 "
                f"DB status={prev_status!r}(포지션 없음)로 불일치 -> 가장 최근 매수 로그(id={recent.id}, "
                f"{recent.ts.isoformat()}, 지정가 {recent.price}원)를 근거로 status=holding으로 정정"
            ),
            signal_snapshot={
                "actual_holdings_qty": actual_qty,
                "source": "manual_sync",
                "backfilled_from_log_id": recent.id,
            },
        )
        state.status = "holding"
        state.entry_price = recent.price
        state.entry_qty = actual_qty
        state.entry_at = recent.ts
        state.entry_order_no = None
        state.peak_price = None
        state.entry_foreign_flow_sign = None
        # 백필 포지션은 사용자 조건을 알 수 없으므로 엔진 고정 규칙(안전 측)으로 관리한다.
        state.entry_source = None
        state.manual_exit_rules = None
        await session.commit()
        await session.refresh(state)
        return {
            "corrected": True,
            "actual_qty": actual_qty,
            "detail": (
                f"status={prev_status!r}에서 holding으로 정정 — 진입가는 로그 id={recent.id} 기준 "
                f"{recent.price}원으로 백필"
            ),
            "state": _serialize_state(state, current_price=None),
        }


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


@router.get("/backtest")
async def get_auto_trade_backtest(
    code: str = Query(TARGET_CODE, description="종목코드(기본 자동매매 대상)"),
    days: int = Query(365, ge=1, le=3650, description="최근 며칠치 일봉을 쓸지"),
    trail_mode: str = Query(
        "fixed_floor", description="'fixed_floor'(현재 실거래 규칙) | 'ratchet'(비교 실험용 대안)"
    ),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """§5.54 자동매매 핵심 규칙(진입: 골든크로스+거래량 스파이크, 손절 -1.5%,
    트레일전환 +1%, 트레일청산: 데드크로스 AND 진입가+0.5% 이하)의 일봉 근사
    백테스트(PLAN.md §5.74) — **완전 읽기 전용**, 주문/상태 변경 전혀 없다
    (`_position_lock`/`AutoTradeState`/`AutoTradeLog` 어느 것도 건드리지
    않는다). 계산 자체는 `quant/auto_trade_backtest.run_backtest`(순수 함수,
    자세한 설계·제약은 그 모듈 docstring 참고)에 위임한다.

    **1분봉이 아니라 일봉 근사다** — 실제 자동매매가 쓰는 1분봉 이력은 DB에
    저장된 적이 없어(§5.1 원칙) `stock_ohlcv`(일봉)로 대신한다. §5.55 안전
    규칙(진입시간필터/EOD강제청산/리스크경보 손절선 축소/수급반전조기청산)은
    장중·실시간 개념이라 과거 일봉만으로는 재현할 수 없어 포함하지 않는다.

    `trail_mode="ratchet"`는 실거래 규칙이 아니라 비교 실험용 대안(트레일
    청산 기준을 진입가 고정 플로어 대신 고점 대비 반납폭으로 바꿔본 것) —
    `quant/auto_trade_backtest.py` 모듈 docstring 참고.

    Returns ``{"code", "days", "bars_used", "trail_mode", "trades": [...],
    "summary": {"n","win_rate_pct","avg_win_pct","avg_loss_pct",
    "cumulative_return_pct","best_trade_pct","worst_trade_pct"}}``.
    """
    if trail_mode not in ("fixed_floor", "ratchet"):
        raise HTTPException(400, "trail_mode must be 'fixed_floor' or 'ratchet'")

    since = dt.date.today() - dt.timedelta(days=days)
    stmt = (
        select(StockOhlcv)
        .where(StockOhlcv.code == code, StockOhlcv.date >= since)
        .order_by(StockOhlcv.date)
    )
    rows = (await session.execute(stmt)).scalars().all()
    bars = [
        {"date": r.date.isoformat(), "close": r.close, "volume": r.volume}
        for r in rows
        if r.close is not None and r.volume is not None
    ]

    result = run_backtest(bars, trail_mode=trail_mode)
    return {
        "code": code,
        "days": days,
        "bars_used": len(bars),
        "trail_mode": trail_mode,
        **result,
    }


@router.get("/intraday-backtest")
async def get_auto_trade_intraday_backtest(
    code: str = Query(TARGET_CODE, description="종목코드(기본 자동매매 대상)"),
    days: int = Query(180, ge=1, le=180, description="최근 며칠치 1분봉을 쓸지"),
    trail_mode: str = Query(
        "fixed_floor",
        description="'fixed_floor'(현재 실거래 규칙) | 'ratchet'(비교 실험용) | 'daily_reset'(비교 실험용)",
    ),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """§5.54 자동매매 핵심 규칙의 **진짜 1분봉** 백테스트(PLAN.md §5.77,
    §5.74/§5.75 후속) — **완전 읽기 전용**, 주문/상태 변경 전혀 없다(`/backtest`
    와 동일 성격). `GET /backtest`는 1분봉 이력이 없어 일봉으로 근사했지만,
    이건 §5.75가 쌓기 시작한 `StockIntradayBar`를 그대로 쓴다. 계산 자체는
    `quant/auto_trade_intraday_backtest.run_intraday_backtest`(순수 함수,
    자세한 설계·`daily_reset` 모드 제약은 그 모듈 docstring 참고)에 위임한다.

    §5.55 안전 규칙(진입시간필터/EOD강제청산/리스크경보 손절선/수급반전
    조기청산)은 `/backtest`와 동일하게 이번에도 포함하지 않는다 — 트레일
    설계 비교가 다른 조기청산 경로로 뒤섞이는 걸 막기 위해 의도적으로 뺐다.

    **데이터가 아직 하루~이틀치뿐이다**(§5.75 수집기가 한동안 워커 재시작
    없이 스케줄에서 누락돼 있었음, PLAN.md §5.77 참고) — `bars_used`가
    작고 `trades`가 비어있을 수 있다(에러 아님, 1분봉이 쌓일수록 의미가
    커진다).

    Returns ``{"code", "days", "bars_used", "trail_mode", "trades": [...],
    "summary": {"n","win_rate_pct","avg_win_pct","avg_loss_pct",
    "cumulative_return_pct","best_trade_pct","worst_trade_pct"}}``.
    """
    if trail_mode not in ("fixed_floor", "ratchet", "daily_reset"):
        raise HTTPException(400, "trail_mode must be 'fixed_floor', 'ratchet' or 'daily_reset'")

    since = dt.date.today() - dt.timedelta(days=days)
    stmt = (
        select(StockIntradayBar)
        .where(StockIntradayBar.code == code, StockIntradayBar.date >= since)
        .order_by(StockIntradayBar.date, StockIntradayBar.time)
    )
    rows = (await session.execute(stmt)).scalars().all()
    bars = [
        {"date": r.date.isoformat(), "time": r.time, "close": r.close, "volume": r.volume}
        for r in rows
        if r.close is not None and r.volume is not None
    ]

    result = run_intraday_backtest(bars, trail_mode=trail_mode)
    return {
        "code": code,
        "days": days,
        "bars_used": len(bars),
        "trail_mode": trail_mode,
        **result,
    }


@router.get("/performance")
async def get_auto_trade_performance(session: AsyncSession = Depends(get_session)) -> dict:
    """진입 방식별(자동/수동) 성과 집계(PLAN.md §5.76) — 완전 읽기 전용,
    `AutoTradeLog`를 그대로 집계할 뿐 새 저장소·주문·상태 변경이 전혀 없다.
    사용자가 "내가 직접 한 게 수익률이 더 좋다면 자동매매 프로그램의 존폐
    유무가 중요하다"고 물어 만들었다 — 계산 자체는
    `quant/auto_trade_performance.summarize_by_entry_source`(순수 함수,
    pairing 원리·`KNOWN_BAD_LOG_IDS` 예외 설명은 그 모듈 docstring 참고)에
    위임한다. **이 엔드포인트도 어떤 판단 문구도 만들지 않는다** — 숫자만
    반환한다.

    Returns ``{"by_source": {"auto": {...}, "manual": {...}}, "trades": [...],
    "anomalies": [...], "open_position": {...} | None}``.
    """
    event_types = ENTRY_EVENT_TYPES | EXIT_EVENT_TYPES
    stmt = (
        select(AutoTradeLog.id, AutoTradeLog.ts, AutoTradeLog.event_type, AutoTradeLog.price)
        .where(AutoTradeLog.event_type.in_(event_types))
        .order_by(AutoTradeLog.ts)
    )
    rows = (await session.execute(stmt)).all()
    log_rows = [
        {"id": r.id, "ts": r.ts.isoformat(), "event_type": r.event_type, "price": float(r.price)}
        for r in rows
        if r.price is not None
    ]
    return summarize_by_entry_source(log_rows)
