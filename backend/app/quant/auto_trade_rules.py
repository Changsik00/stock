"""자동매매 상태 전이 순수 판정 함수 (PLAN.md §5.54).

이 모듈은 DB/네트워크/`KiwoomClient`를 전혀 모른다 — 신호값(dict)과 현재
상태(status/entry_price/peak_price)만 받아 "다음에 무엇을 해야 하는지"만
판정한다(단위테스트 대상, `tests/test_auto_trade_rules.py`). 실제 주문 실행
(`place_buy_order`/`place_sell_order` 호출, `AutoTradeState`/`AutoTradeLog`
갱신)은 `collectors/auto_trader.py`가 이 모듈의 판정 결과를 받아서 한다 —
`quant/signals.py` 모듈 docstring이 이미 확립한 "계산부/조립부 분리" 패턴과
동일하다.

**원칙(§5 전체 원칙 그대로): 이 엔진 자체는 사용자가 이미 확정한 전략 규칙을
기계적으로 실행할 뿐이다.** 아래 상수/함수 어디에도 "이 조합이 좋다/나쁘다"
같은 새로운 판단은 없다 — PLAN.md §5.54에서 사용자가 직접 확정한 숫자
(진입/트레일전환/손절 퍼센트)를 그대로 상수로 옮겼을 뿐이다.

## 전략 규칙 (PLAN.md §5.54, 사용자 확정)

- **진입**: `ma_cross.state == "golden"` AND `volume_spike.is_spike == True`.
- **손절**: 상태(holding/trailing) 무관, 현재가가 진입가 대비 -1.5% 이하면
  즉시 매도 — 다른 조건보다 항상 먼저 확인한다(최우선 안전장치).
- **트레일 전환**: "holding" 상태에서 현재가가 진입가 대비 +1% 이상이면
  "trailing"으로 전환, `peak_price`를 현재가로 초기화.
  전환 전(순수 "holding")에는 손절 조건만 감시한다 — 그 밖의 이유로는
  매도하지 않는다.
- **트레일 청산**: "trailing" 상태에서 `ma_cross.state == "dead"`(추세 반전
  확인) **AND** 현재가가 진입가 대비 +0.5% 이하로 되돌아왔으면 매도.
  그 전까지는 신고가(`peak_price`)를 계속 갱신하며 보유한다.
"""

from __future__ import annotations

from typing import Any, Literal

# -- 전략 상수 (PLAN.md §5.54, 사용자 확정값 — 바꾸지 말 것) ------------------------

TARGET_CODE = "0167A0"  # SOL AI반도체TOP2플러스 — 자동매매 대상 종목 고정
TARGET_QTY = 1  # 1주 고정

STOP_LOSS_PCT = -1.5  # 진입가 대비 -1.5% 이하 -> 즉시 손절 (상태 무관)
TRAIL_ACTIVATE_PCT = 1.0  # 진입가 대비 +1% 이상 -> holding에서 trailing으로 전환
TRAIL_EXIT_FLOOR_PCT = 0.5  # trailing 중 dead cross + 진입가 대비 +0.5% 이하 -> 청산

Status = Literal["idle", "holding", "trailing"]


def _pct_change(entry_price: float, current_price: float) -> float:
    """진입가 대비 현재가의 등락률(%)."""
    return (current_price - entry_price) / entry_price * 100


def evaluate_entry(ma_cross_state: str | None, volume_is_spike: bool | None) -> bool:
    """진입 조건: ma_cross golden AND volume_spike. 어느 한쪽이라도 아니면(계산
    불가로 None인 경우 포함) False — 데이터 부족을 "진입 신호 있음"으로 잘못
    해석하지 않기 위해 명시적으로 `is golden`/`is True`만 참으로 취급한다."""
    return ma_cross_state == "golden" and volume_is_spike is True


def evaluate_stop_loss(entry_price: float, current_price: float) -> bool:
    """손절 조건: 현재가가 진입가 대비 -1.5% 이하. 상태(holding/trailing)와
    무관하게 항상 먼저 확인해야 하는 최우선 조건이다."""
    return _pct_change(entry_price, current_price) <= STOP_LOSS_PCT


def evaluate_trail_activate(entry_price: float, current_price: float) -> bool:
    """트레일 전환 조건: 현재가가 진입가 대비 +1% 이상 (holding -> trailing)."""
    return _pct_change(entry_price, current_price) >= TRAIL_ACTIVATE_PCT


def evaluate_trail_exit(ma_cross_state: str | None, entry_price: float, current_price: float) -> bool:
    """트레일 청산 조건: 추세 반전(dead cross) 확인 AND 현재가가 진입가 대비
    +0.5% 이하로 되돌아옴. 둘 다 충족해야 한다 — dead cross만으로는(플로어
    도달 전) 아직 매도하지 않는다(신고가 갱신 중일 수도 있으므로)."""
    return ma_cross_state == "dead" and _pct_change(entry_price, current_price) <= TRAIL_EXIT_FLOOR_PCT


def next_peak_price(peak_price: float, current_price: float) -> float:
    """trailing 중 신고가 갱신 — 현재가가 기존 peak보다 높으면 그 값, 아니면
    기존 peak을 그대로 유지."""
    return max(peak_price, current_price)


def decide_idle_action(ma_cross_state: str | None, volume_is_spike: bool | None) -> dict[str, Any]:
    """status == "idle"일 때의 판정. Returns
    ``{"action": "enter"|"none", "reason": str}``."""
    if evaluate_entry(ma_cross_state, volume_is_spike):
        return {
            "action": "enter",
            "reason": (
                f"진입 조건 충족: ma_cross.state={ma_cross_state!r} == 'golden' AND "
                f"volume_spike.is_spike={volume_is_spike!r} == True"
            ),
        }
    return {
        "action": "none",
        "reason": (
            f"진입 조건 미충족: ma_cross.state={ma_cross_state!r}, "
            f"volume_spike.is_spike={volume_is_spike!r}"
        ),
    }


def decide_position_action(
    status: Status,
    entry_price: float,
    peak_price: float | None,
    ma_cross_state: str | None,
    current_price: float,
) -> dict[str, Any]:
    """status in ("holding", "trailing")일 때의 판정.

    검사 순서(§5.54 규칙 그대로): 손절 조건을 항상 먼저 확인한다(상태 무관,
    최우선 안전장치) — 손절 조건이 충족되면 holding/trailing 여부와 무관하게
    그 즉시 "stop_loss"를 반환한다. 손절이 아니면 상태별로 갈린다:

    - "holding": 트레일 전환 조건 확인 -> 충족 시 "trail_activate"
      (`new_peak_price`=현재가), 아니면 "none".
    - "trailing": 신고가 갱신 + 트레일 청산 조건 확인 -> 청산 조건 충족 시
      "exit_trail"(갱신된 peak 포함), 아니면 peak이 갱신됐으면 "peak_update",
      변화 없으면 "none".

    Returns ``{"action": "stop_loss"|"trail_activate"|"exit_trail"|
    "peak_update"|"none", "reason": str, "new_peak_price": float (해당 시)}``.
    """
    pct = round(_pct_change(entry_price, current_price), 4)

    if evaluate_stop_loss(entry_price, current_price):
        return {
            "action": "stop_loss",
            "reason": (
                f"손절 조건 충족: 현재가 {current_price} vs 진입가 {entry_price} "
                f"({pct:+.2f}%) <= {STOP_LOSS_PCT}% (status={status})"
            ),
        }

    if status == "holding":
        if evaluate_trail_activate(entry_price, current_price):
            return {
                "action": "trail_activate",
                "reason": (
                    f"트레일 전환 조건 충족: 현재가 {current_price} vs 진입가 {entry_price} "
                    f"({pct:+.2f}%) >= {TRAIL_ACTIVATE_PCT}%"
                ),
                "new_peak_price": current_price,
            }
        return {
            "action": "none",
            "reason": (
                f"holding 유지: 손절/트레일전환 조건 모두 미충족 (현재가 {current_price} "
                f"vs 진입가 {entry_price}, {pct:+.2f}%)"
            ),
        }

    # status == "trailing"
    prev_peak = peak_price if peak_price is not None else entry_price
    new_peak = next_peak_price(prev_peak, current_price)

    if evaluate_trail_exit(ma_cross_state, entry_price, current_price):
        return {
            "action": "exit_trail",
            "reason": (
                f"트레일 청산 조건 충족: ma_cross.state={ma_cross_state!r} == 'dead' AND "
                f"현재가 {current_price} vs 진입가 {entry_price} ({pct:+.2f}%) <= "
                f"{TRAIL_EXIT_FLOOR_PCT}% (peak={new_peak})"
            ),
            "new_peak_price": new_peak,
        }

    if new_peak != prev_peak:
        return {
            "action": "peak_update",
            "reason": f"신고가 갱신: {prev_peak} -> {new_peak}",
            "new_peak_price": new_peak,
        }

    return {
        "action": "none",
        "reason": (
            f"trailing 유지: 청산 조건 미충족 (ma_cross.state={ma_cross_state!r}, "
            f"현재가 {current_price} vs 진입가 {entry_price}, {pct:+.2f}%, peak={new_peak})"
        ),
    }


def check_entry_budget(
    status: Status,
    notional: float,
    total_budget_krw: float,
    max_order_notional_krw: float,
) -> bool:
    """매수 진입 전 최종 예산 가드 — 다음을 **전부** 통과해야 True(매수 진행
    가능):

    1. ``status == "idle"``(이미 보유 중이면 절대 추가 매수하지 않음 — 이
       전략은 한 번에 포지션 하나만 허용).
    2. ``notional <= total_budget_krw``(`AUTO_TRADE_TOTAL_BUDGET_KRW`, 이
       자동매매 기능 전체의 누적 예산 — 사용자가 실제 입금한 금액).
    3. ``notional <= max_order_notional_krw``(`MAX_ORDER_NOTIONAL_KRW`,
       `clients/kiwoom.py`의 기존 주문 1건 캡 — `place_buy_order` 내부에서도
       다시 확인되지만, 여기서도 방어적으로 먼저 확인해 조건을 만족하지
       못하면 아예 주문 시도조차 하지 않는다).

    이 전략은 상태 기계상 "idle이 아니면 애초에 이 함수가 호출되지 않는다"
    (호출부가 idle일 때만 진입을 시도하므로)로 실질적으로 충분하지만,
    PLAN.md §5.54가 "방어적으로 명시적 체크를 둔다"고 요구해 상태 조건도
    이 함수 안에 포함한다."""
    if status != "idle":
        return False
    if notional > total_budget_krw:
        return False
    if notional > max_order_notional_krw:
        return False
    return True
