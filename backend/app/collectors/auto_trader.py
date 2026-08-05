"""자동매매 실행 엔진 — SOL AI반도체TOP2플러스(0167A0) 트레일링 스탑 (PLAN.md §5.54).

**이 프로젝트 최초의 완전자동매매 실행 엔진이다 — 실제 증권 계좌(실전, 모의
아님)에 연결돼 있고, `place_buy_order`/`place_sell_order`(`clients/kiwoom.py`,
kt10000/kt10001, 2026-08-04 실호출 확정)를 실제로 호출해 진짜 돈을 움직인다.**
이 모듈은 새 판단 로직을 만들지 않는다 — 사용자가 PLAN.md §5.54에서 직접
확정한 진입/청산/손절 규칙을 기계적으로 실행할 뿐이다(house rule §5).

## 절대 원칙(안전장치 — 바꾸지 말 것)

1. **킬스위치 기본 OFF** — `AutoTradeState.enabled`가 False면 `run_auto_trade`는
   맨 첫 줄에서 즉시 반환한다. 신호 평가조차 하지 않는다(로그도 남기지 않음 —
   매 폴링마다 "disabled"를 기록하면 노이즈만 커진다).
2. **누적 예산 가드** — `quant/auto_trade_rules.check_entry_budget`이
   `AUTO_TRADE_TOTAL_BUDGET_KRW`(기본 25,000원, 사용자가 실제 입금한 금액)와
   기존 `MAX_ORDER_NOTIONAL_KRW`(`clients/kiwoom.py`, 5만원, 주문 1건 캡)
   **둘 다** 통과해야만 매수를 시도한다. 상태 기계가 한 번에 포지션 하나만
   허용하므로(단일 종목, 단일 수량 1주) 실질적으로는 "이미 보유 중이면 추가
   매수 안 함"과 같지만, 방어적으로 명시 체크를 둔다.
3. **모든 실행/판단을 감사 로그(`AutoTradeLog`)에 남긴다** — 그 순간의
   신호값(ma_cross state, volume_spike, 가격, 진입가 대비 %)을 사람이 읽을 수
   있는 문장으로 남긴다. idle 상태에서 진입 조건 미충족, trailing 중 신고가만
   조용히 갱신되는 경우는 의도적으로 로그하지 않는다 — `collectors/
   scalp_tracker.py`/`positioning_snapshot.py`와 동일한 "의미 있는 사건만
   기록" 태도(노이즈 방지).
4. **house rule(§5)** — 이 엔진 자체는 사용자가 이미 확정한 규칙을 기계적으로
   실행할 뿐이다. 로그 문구도 "이 조합이 좋다/나쁘다" 같은 새 판단을 만들지
   않는다 — `quant/auto_trade_rules.py`가 만드는 reason 문자열(신호값과 임계값을
   그대로 서술)을 그대로 저장한다.
5. **주문 실패는 재시도 루프에 빠지지 않는다** — 매수/매도 시도는 각각 한 번의
   `try/except`로 감싼다. 실패하면 `AutoTradeState`를 갱신하지 않고(잘못된
   상태로 남기지 않음) 로그만 남기고 반환한다 — 조건이 다음 폴링에도 여전히
   맞으면 자연히 재시도되지만, 이 함수 자체는 절대 같은 폴링 안에서 반복
   시도하지 않는다.

## 진입 신호 감지 — "지금 이 순간"이 아니라 "최근 몇 봉 이내"(2026-08-05 수정)

골든크로스(`quant/signals.py::moving_average_cross`)는 교차가 일어난 바로 그
1분봉에서만 "golden"이고, 다음 봉부터는 이미 "none"으로 돌아가는 순간 이벤트다.
60초 폴링과 1분봉 경계가 정확히 맞아떨어지지 않으면 이 순간을 그냥 지나칠 수
있다 — 실제로 2026-08-05 10:33 KST에 골든크로스+거래량 스파이크가 동시에
떴는데도(사후 재계산 확인) 폴링이 이 정확한 순간을 못 맞춰 진입이 일어나지
않은 사례가 있었다(사용자 지적으로 발견). `_golden_cross_in_lookback`/
`_volume_spike_in_lookback`(아래)이 "지금 이 순간"뿐 아니라
`ENTRY_SIGNAL_LOOKBACK_BARS`(3봉, 약 3분) 이내에 신호가 있었는지도 함께
본다 — 조건 자체(golden AND spike)를 느슨하게 하는 게 아니라 감지 시점만
넓혀 이 폴링 타이밍 미스를 없앤다. 청산(dead cross) 판정은 이번 수정 범위
밖이다(최신 봉 기준 그대로) — 진입에서 실제로 관측된 문제만 고쳤다.

## 스케줄링 배선

`collectors/live_refresh.py`의 60초 잡에서 `positioning_snapshot.
track_positioning_snapshot` 호출 바로 옆에서 호출된다(같은 try/except 패턴).
`enabled` 기본값이 False이므로 배포 직후에는 이 호출이 매 폴링마다 즉시
반환되기만 하고 아무 부작용이 없다 — 안전하다.

## 가격 정책 — "현재가"(판정용) vs "주문가"(체결용)

상태 기계 판정(진입가 대비 %, 손절/트레일 조건)에 쓰는 **현재가**는 신호
계산에 쓴 것과 같은 소스(§5.3 `GET /{code}/signals`가 쓰는 `_warm_stock_
intraday`의 마지막 분봉 종가)를 그대로 재사용한다 — 중복 조회 없음. 반면
**실제 주문가**(`place_buy_order`/`place_sell_order`에 넘기는 지정가)는 별도로
`stock_quote`(ka10004, PLAN.md §5.50-2)를 호출해 즉시 체결 가능한 호가(매수
주문 -> 매도 1호가, 매도 주문 -> 매수 1호가)를 쓴다 — 이건 새로운 매매
"판단"이 아니라 지정가 주문이 실제로 체결되도록 하는 기계적 실행
디테일이다(PLAN.md §5.54 지시 그대로).

## 미체결 확인은 이 엔진의 책임이 아니다

`place_buy_order`/`place_sell_order`가 성공(예외 없이 반환)하면 그 즉시 상태를
전이한다(주문 접수 = 상태 전이) — 체결 여부를 `get_unfilled_orders`로 재확인하는
루프는 두지 않는다(PLAN.md §5.54가 정의한 알고리즘 범위 밖). 즉시 체결 가능한
호가로 지정가를 걸어 정상 시장 상황에서는 사실상 즉시 체결되는 것을 전제로
한다 — 이 전제가 깨지는 경우(부분체결/미체결 잔류)에 대한 대응은 이번 범위
밖의 후속 작업이다.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..clients.kiwoom import MAX_ORDER_NOTIONAL_KRW, KiwoomClient, parse_quote_levels
from ..models import AutoTradeLog, AutoTradeState
from ..quant.auto_trade_rules import (
    STOP_LOSS_PCT,
    TARGET_CODE,
    TARGET_QTY,
    check_entry_budget,
    decide_idle_action,
    decide_position_action,
    evaluate_stop_loss,
)
from ..quant.signals import moving_average_cross, volume_spike
from ..routers.stocks import _warm_stock_intraday

logger = logging.getLogger(__name__)

# 이 자동매매 기능 전체의 누적 예산(원) — 사용자가 실제 입금한 금액(PLAN.md
# §5.54). clients/kiwoom.py의 MAX_ORDER_NOTIONAL_KRW(5만원, 주문 1건 캡)와는
# 별개로 추가 적용된다 — 둘 다 통과해야 매수 가능
# (quant/auto_trade_rules.check_entry_budget). 실제 매수는 항상 이 값과
# MAX_ORDER_NOTIONAL_KRW 중 더 작은 쪽이 사실상의 상한이 된다.
AUTO_TRADE_TOTAL_BUDGET_KRW = 25_000

STATE_ID = 1  # AutoTradeState 싱글턴 PK — routers/auto_trade.py와 동일한 상수 사용

# **2026-08-05 추가(PLAN.md §5.54-6)**: `run_auto_trade`(60초)와 `watch_stop_loss`
# (30초, 아래)가 둘 다 AutoTradeState(포지션)를 읽고 매도를 시도할 수 있다 —
# 이 락으로 두 잡이 동시에 "지금 보유 중"을 보고 동시에 매도를 시도해 중복
# 주문이 나가는 걸 막는다. 두 함수 모두 상태를 읽는 시점부터 이 락 안에서
# 실행돼야 한다(읽기부터 쓰기까지 원자적으로).
_position_lock = asyncio.Lock()

# **2026-08-05 실측 버그 수정**: `moving_average_cross`는 "교차가 일어난 바로 그
# 1분봉"에서만 "golden"이고, 다음 봉부터는 이미 "none"으로 돌아간다(순간 이벤트).
# 60초 폴링과 1분봉 경계가 정확히 안 맞아떨어지면(예: 봉이 아직 마감되기 전에
# 폴링하거나, 폴링 시점에 이미 다음 봉이 "최신"이 돼 있으면) 이 순간을 그냥
# 지나칠 수 있다 — 실제로 2026-08-05 10:33 KST에 골든크로스+거래량 스파이크가
# 동시에 떴는데도(사후 재계산으로 확인) 폴링이 이 정확한 순간을 못 맞춰 진입이
# 일어나지 않았다(사용자 지적, 로그로 재현·확인). 조건 자체(golden AND spike)를
# 느슨하게 하는 게 아니라, "지금 이 순간"만 보던 걸 "최근 몇 봉 안에 있었는지"로
# 감지 시점만 넓혀서 이 폴링 타이밍 미스를 없앤다.
ENTRY_SIGNAL_LOOKBACK_BARS = 3


def _golden_cross_in_lookback(bars: list[dict]) -> bool:
    """최근 `ENTRY_SIGNAL_LOOKBACK_BARS`개 봉 중 하나라도 그 시점 기준으로
    골든크로스였으면 True. `moving_average_cross`는 항상 "지금까지 주어진
    봉 리스트의 마지막 봉" 기준으로 판정하므로, 봉을 하나씩 줄여 가며(과거
    시점을 재현해) 반복 호출한다 — 새 지표가 아니라 기존 `moving_average_cross`를
    여러 시점에 다시 적용할 뿐이다."""
    if len(bars) < 21:  # moving_average_cross의 long_window(20)+1 최소 요구량
        return False
    start = max(21, len(bars) - ENTRY_SIGNAL_LOOKBACK_BARS + 1)
    return any(moving_average_cross(bars[:i])["state"] == "golden" for i in range(start, len(bars) + 1))


def _volume_spike_in_lookback(bars: list[dict]) -> bool:
    """최근 `ENTRY_SIGNAL_LOOKBACK_BARS`개 봉 중 하나라도 그 시점 기준으로
    거래량 스파이크였으면 True. `_golden_cross_in_lookback`과 동일한 방식
    (기존 `volume_spike`를 여러 시점에 재적용)."""
    if len(bars) < 2:
        return False
    start = max(1, len(bars) - ENTRY_SIGNAL_LOOKBACK_BARS + 1)
    return any(volume_spike(bars[:i])["is_spike"] for i in range(start, len(bars) + 1))


async def _get_state(session: AsyncSession) -> AutoTradeState | None:
    return await session.get(AutoTradeState, STATE_ID)


async def _log(
    session: AsyncSession,
    *,
    event_type: str,
    code: str,
    price: float | None,
    reason: str,
    signal_snapshot: dict | None = None,
    order_response: dict | None = None,
) -> None:
    session.add(
        AutoTradeLog(
            event_type=event_type,
            code=code,
            price=price,
            reason=reason[:1000],
            signal_snapshot=(
                json.dumps(signal_snapshot, ensure_ascii=False, default=str)[:2000]
                if signal_snapshot is not None
                else None
            ),
            order_response=(
                json.dumps(order_response, ensure_ascii=False, default=str)[:2000]
                if order_response is not None
                else None
            ),
        )
    )
    await session.commit()


async def _best_fill_price(client: KiwoomClient, code: str, side: str) -> float | None:
    """즉시 체결 가능한 지정가를 반환한다 — `side="buy"`는 매도 1호가
    (`asks[0]`), `side="sell"`은 매수 1호가(`bids[0]`). 호가 잔량이 전부 0(호가
    없음/데이터 없음)이면 None."""
    data = await client.stock_quote(code)
    levels = parse_quote_levels(data)
    book = levels["asks"] if side == "buy" else levels["bids"]
    for lvl in book:
        if lvl["price"] > 0:
            return lvl["price"]
    return None


async def run_auto_trade(session: AsyncSession) -> dict:
    """PLAN.md §5.54 상태 기계를 1회 평가·실행한다.

    Returns 요약 dict(로깅용, `scalp_tracker.track_scalp_picks`/
    `positioning_snapshot.track_positioning_snapshot`의 반환 관례 참고) —
    ``{"enabled": bool, "action": str, ...}``. ``action``은
    "none"(꺼짐/조건 미충족/신고가만 조용히 갱신)|"enter"|"trail_activate"|
    "exit_stop_loss"|"exit_trail"|"budget_blocked"|"buy_failed"|"sell_failed"|
    "error" 중 하나.
    """
    result: dict = {"enabled": False, "action": "none"}

    # PLAN.md §5.54-6 — `watch_stop_loss`(30초 잡)와 동일한 락을 잡는다. 상태를
    # 읽는 시점부터 잡아야 두 잡이 동시에 "지금 보유 중"을 보고 동시에 매도를
    # 시도하는 걸 막을 수 있다(모듈 상단 `_position_lock` 주석 참고).
    async with _position_lock:
        state = await _get_state(session)
        if state is None or not state.enabled:
            # 킬스위치 꺼짐(또는 시드 행이 없는 비정상 상태) — 신호 평가조차 하지
            # 않고 즉시 반환. 로그도 남기지 않는다(노이즈 방지, 모듈 docstring 참고).
            return result

        result["enabled"] = True
        code = state.code or TARGET_CODE

        try:
            intraday = await _warm_stock_intraday(code, 1)
        except Exception as e:  # noqa: BLE001 - 신호 조회 실패는 다음 폴링에서 재시도
            logger.warning("auto-trade: intraday 조회 실패, 이번 폴링 건너뜀: %s", e)
            result["action"] = "error"
            result["error"] = str(e)[:300]
            return result

        bars = intraday.get("bars") if intraday else None
        if not bars:
            result["action"] = "no_bars"
            return result

        current_price = bars[-1]["close"]
        ma_cross = moving_average_cross(bars)
        vspike = volume_spike(bars)
        # 진입 판정 전용 — "지금 이 순간"뿐 아니라 최근 몇 봉 안에 신호가 있었는지도
        # 함께 본다(모듈 상단 "2026-08-05 실측 버그 수정" 절 참고). 청산(dead cross)
        # 판정은 이번 수정 범위 밖이라 `ma_cross`(최신 봉 기준)를 그대로 쓴다.
        golden_recent = ma_cross["state"] == "golden" or _golden_cross_in_lookback(bars)
        spike_recent = bool(vspike["is_spike"]) or _volume_spike_in_lookback(bars)
        signal_snapshot = {
            "ma_cross": ma_cross,
            "volume_spike": vspike,
            "current_price": current_price,
            "golden_cross_recent": golden_recent,
            "volume_spike_recent": spike_recent,
        }

        if state.status == "idle":
            return await _handle_idle(
                session, state, code, current_price, golden_recent, spike_recent, signal_snapshot, result
            )

        return await _handle_position(session, state, code, current_price, ma_cross, signal_snapshot, result)


async def _handle_idle(
    session: AsyncSession,
    state: AutoTradeState,
    code: str,
    current_price: float,
    golden_recent: bool,
    spike_recent: bool,
    signal_snapshot: dict,
    result: dict,
) -> dict:
    # `evaluate_entry`/`decide_idle_action`은 문자열 ma_cross_state를 받아
    # `== "golden"`으로 비교한다(quant/auto_trade_rules.py) — 이미 "최근 봉
    # 이내 골든크로스 있었음"으로 판정된 값이므로, 그 인터페이스를 그대로
    # 재사용하려고 "golden"/"none" 문자열로 다시 인코딩한다(모듈 상단
    # "2026-08-05 실측 버그 수정" 절 참고, quant/auto_trade_rules.py는 수정하지
    # 않음 — 판정 로직 자체는 그대로, 입력값만 lookback 반영).
    decision = decide_idle_action("golden" if golden_recent else "none", spike_recent)
    result["action"] = decision["action"]
    if decision["action"] != "enter":
        # idle + 조건 미충족은 노이즈라 로그하지 않는다(모듈 docstring 원칙 3).
        return result

    notional = TARGET_QTY * current_price
    if not check_entry_budget(state.status, notional, AUTO_TRADE_TOTAL_BUDGET_KRW, MAX_ORDER_NOTIONAL_KRW):
        await _log(
            session,
            event_type="error",
            code=code,
            price=current_price,
            reason=(
                f"{decision['reason']} — 그러나 예산 가드 실패: notional={notional} "
                f"(qty={TARGET_QTY} x price={current_price}), "
                f"budget={AUTO_TRADE_TOTAL_BUDGET_KRW}, "
                f"max_order_notional={MAX_ORDER_NOTIONAL_KRW}, status={state.status}"
            ),
            signal_snapshot=signal_snapshot,
        )
        result["action"] = "budget_blocked"
        return result

    try:
        async with KiwoomClient() as client:
            order_price = await _best_fill_price(client, code, "buy")
            if order_price is None:
                raise RuntimeError("매도호가를 확인할 수 없어 주문가를 정할 수 없음")
            order_response = await client.place_buy_order(code, TARGET_QTY, int(order_price))
    except Exception as e:  # noqa: BLE001 - 주문 실패는 재시도 루프에 빠지지 않고 로그만
        logger.warning("auto-trade: 매수 주문 실패: %s", e)
        await _log(
            session,
            event_type="error",
            code=code,
            price=current_price,
            reason=f"매수 주문 시도 실패: {decision['reason']} / 오류: {e}",
            signal_snapshot=signal_snapshot,
        )
        result["action"] = "buy_failed"
        result["error"] = str(e)[:300]
        return result

    now = dt.datetime.now(dt.timezone.utc)
    state.status = "holding"
    state.entry_price = order_price
    state.entry_qty = TARGET_QTY
    state.entry_at = now
    state.entry_order_no = str(order_response.get("ord_no")) if order_response.get("ord_no") else None
    state.peak_price = None
    await session.commit()

    await _log(
        session,
        event_type="entry",
        code=code,
        price=order_price,
        reason=decision["reason"] + f" -> 매수 주문 제출(지정가 {order_price}원, {TARGET_QTY}주)",
        signal_snapshot=signal_snapshot,
        order_response=order_response,
    )
    result["action"] = "entry"
    return result


async def _handle_position(
    session: AsyncSession,
    state: AutoTradeState,
    code: str,
    current_price: float,
    ma_cross: dict,
    signal_snapshot: dict,
    result: dict,
) -> dict:
    decision = decide_position_action(
        state.status,
        float(state.entry_price),
        float(state.peak_price) if state.peak_price is not None else None,
        ma_cross["state"],
        current_price,
    )
    result["action"] = decision["action"]

    if decision["action"] == "none":
        return result

    if decision["action"] == "peak_update":
        # trailing 중 신고가만 조용히 갱신 — 로그하지 않는다(모듈 docstring 원칙 3).
        state.peak_price = decision["new_peak_price"]
        await session.commit()
        return result

    if decision["action"] == "trail_activate":
        state.status = "trailing"
        state.peak_price = decision["new_peak_price"]
        await session.commit()
        await _log(
            session,
            event_type="trail_activate",
            code=code,
            price=current_price,
            reason=decision["reason"],
            signal_snapshot=signal_snapshot,
        )
        return result

    # stop_loss 또는 exit_trail -> 매도
    event_type = "exit_stop_loss" if decision["action"] == "stop_loss" else "exit_trail"
    try:
        async with KiwoomClient() as client:
            order_price = await _best_fill_price(client, code, "sell")
            if order_price is None:
                raise RuntimeError("매수호가를 확인할 수 없어 주문가를 정할 수 없음")
            order_response = await client.place_sell_order(
                code, int(state.entry_qty or TARGET_QTY), int(order_price)
            )
    except Exception as e:  # noqa: BLE001 - 주문 실패는 재시도 루프에 빠지지 않고 로그만
        logger.warning("auto-trade: 매도 주문 실패: %s", e)
        await _log(
            session,
            event_type="error",
            code=code,
            price=current_price,
            reason=f"매도 주문 시도 실패({event_type}): {decision['reason']} / 오류: {e}",
            signal_snapshot=signal_snapshot,
        )
        result["action"] = "sell_failed"
        result["error"] = str(e)[:300]
        return result

    state.status = "idle"
    state.entry_price = None
    state.entry_qty = None
    state.entry_at = None
    state.entry_order_no = None
    state.peak_price = None
    await session.commit()

    await _log(
        session,
        event_type=event_type,
        code=code,
        price=order_price,
        reason=decision["reason"] + f" -> 매도 주문 제출(지정가 {order_price}원)",
        signal_snapshot=signal_snapshot,
        order_response=order_response,
    )
    return result


async def watch_stop_loss(session: AsyncSession) -> dict:
    """포지션 보유 중(holding/trailing) 손절만 감시하는 고빈도 보조 잡
    (PLAN.md §5.54-6, 2026-08-05 사용자 질의 — "매수하고 나서 모니터링 간격은
    어떻게 되니?"). `run_auto_trade`(60초, `collectors/live_refresh.py`의
    60초 잡)와 별개로 30초 간격의 전용 잡으로 등록된다(`start_live_refresh_
    scheduler` 참고).

    **왜 그냥 폴링만 더 자주 하면 안 되는가**: `run_auto_trade`의 손절 판정은
    1분봉(`ka10080`) 종가를 쓰는데, 이 데이터 자체가 60초 캐시로 묶여 있다
    (`routers/stocks.py::_intraday_ttl_seconds` — 1분봉은 1분에 한 번만
    갱신되므로 그보다 자주 조회해봤자 같은 봉을 다시 받을 뿐이다). 폴링
    주기만 30초로 당겨도 반응속도는 안 빨라진다 — 데이터 소스 자체를
    캐시 없는 실시간 호가(`stock_quote`, ka10004)로 바꿔야 실제로 더 빨리
    반응한다. 이 함수는 **딱 손절 판정에만** 실시간 매수 1호가(즉시 매도
    가능한 가격)를 쓴다.

    **진입/트레일전환/트레일청산은 건드리지 않는다** — 골든크로스/거래량
    스파이크/dead cross는 전부 1분봉 기반 기술적 지표라, 더 자주 봐도
    데이터가 그대로라 의미가 없다(위와 동일한 논리). 그건 계속
    `run_auto_trade`(60초)가 전담한다 — 이 함수는 상태가 "holding"/
    "trailing"이 아니면 즉시 반환한다.

    **동시성**: `run_auto_trade`와 동일한 `_position_lock`을 잡는다(모듈
    상단 주석 참고) — 두 잡이 동시에 같은 포지션에 대해 매도를 시도해
    중복 주문이 나가는 걸 막는다.

    Returns ``{"enabled": bool, "action": "none"|"exit_stop_loss"|
    "sell_failed"|"error"}``.
    """
    result: dict = {"enabled": False, "action": "none"}

    async with _position_lock:
        state = await _get_state(session)
        if state is None or not state.enabled:
            return result
        result["enabled"] = True

        if state.status not in ("holding", "trailing"):
            # 이 잡은 포지션 관리 전용 — 진입(idle) 스캔은 하지 않는다
            # (60초 run_auto_trade가 전담, 모듈 docstring 참고).
            return result

        entry_price = float(state.entry_price) if state.entry_price is not None else None
        if entry_price is None:
            # 정상 상태 기계라면 holding/trailing인데 entry_price가 없는 경우는
            # 없어야 한다 — 방어적으로 아무 것도 하지 않는다(잘못된 값으로
            # 손절 계산하지 않음).
            result["action"] = "error"
            return result

        code = state.code or TARGET_CODE

        try:
            async with KiwoomClient() as client:
                live_price = await _best_fill_price(client, code, "sell")
        except Exception as e:  # noqa: BLE001 - 호가 조회 실패는 다음 폴링에서 재시도
            logger.warning("auto-trade(fast watch): 호가 조회 실패, 이번 폴링 건너뜀: %s", e)
            result["action"] = "error"
            result["error"] = str(e)[:300]
            return result

        if live_price is None or not evaluate_stop_loss(entry_price, live_price):
            return result

        # 손절 조건 충족 — 방금 조회한 매수1호가(live_price)를 그대로 매도
        # 지정가로 쓴다(재조회 없음 — 그 사이 값이 바뀌어봤자 여전히 손절
        # 구간이므로 다시 조회할 필요가 없다).
        pct = round((live_price - entry_price) / entry_price * 100, 4) if entry_price else None
        signal_snapshot = {
            "live_price": live_price,
            "entry_price": entry_price,
            "pct": pct,
            "source": "realtime_quote_fast_watch",
        }

        try:
            async with KiwoomClient() as client:
                order_response = await client.place_sell_order(code, int(state.entry_qty or TARGET_QTY), int(live_price))
        except Exception as e:  # noqa: BLE001 - 주문 실패는 재시도 루프에 빠지지 않고 로그만
            logger.warning("auto-trade(fast watch): 매도 주문 실패: %s", e)
            await _log(
                session,
                event_type="error",
                code=code,
                price=live_price,
                reason=f"[실시간 호가 손절 감시] 매도 주문 시도 실패: {e}",
                signal_snapshot=signal_snapshot,
            )
            result["action"] = "sell_failed"
            result["error"] = str(e)[:300]
            return result

        state.status = "idle"
        state.entry_price = None
        state.entry_qty = None
        state.entry_at = None
        state.entry_order_no = None
        state.peak_price = None
        await session.commit()

        await _log(
            session,
            event_type="exit_stop_loss",
            code=code,
            price=live_price,
            reason=(
                f"[실시간 호가 손절 감시(30초)] 손절 조건 충족: 실시간가 {live_price} vs "
                f"진입가 {entry_price} ({pct:+.2f}%) <= {STOP_LOSS_PCT}% -> 매도 주문 제출"
            ),
            signal_snapshot=signal_snapshot,
            order_response=order_response,
        )
        result["action"] = "exit_stop_loss"
        return result
