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

## 안전 규칙 추가 (PLAN.md §5.55, 2026-08-06 — 실제 손실 사고 이후)

2026-08-05 오버나잇 갭하락으로 의도한 -1.5%가 아니라 -4.87%에 손절되는 실제
손실 사고가 발생했다(경위는 PLAN.md §5.55 본문 참고). 사용자가 지적한 4가지를
그대로 규칙화했다 — 여기에도 새 판단은 없다, 이미 있는 지표(regime/risk_alert/
foreign-position)를 그대로 매핑한 것뿐이다:

- **§5.55-1 진입 시간 필터**: 개장 후 10분(09:00~09:10)·마감 전 10분
  (15:20~15:30) KST엔 **신규 진입만** 금지(`is_entry_blocked_by_time`). 손절/
  트레일청산/강제청산은 이 함수를 절대 거치지 않으므로 항상 그대로 작동한다.
- **§5.55-2 장마감 전 조건부 오버나잇 청산**(이번 사고를 직접 막았을 최우선
  규칙): 15:20 KST 이후 보유 중이면 `evaluate_eod_forced_exit`이 리스크 경보/
  평가손익/trailing 여부/코스닥 외국인 매도 스트릭 4개를 확인해 하나라도
  실패하면 무조건 청산 판정을 반환한다.
- **§5.55-3 리스크 경보 연동**: 활성 중엔 신규 진입 금지(호출부 책임) + 보유
  중 손절선을 `STOP_LOSS_PCT`(-1.5%)에서 `STOP_LOSS_PCT_RISK_ALERT`(-0.8%)로
  임시 축소.
- **§5.55-4 수급 방향 전환 조기청산**: 진입 시점 외인 현물 누적 순매수 부호
  (`foreign_flow_sign`)를 기록해 두고, 이후 반전되면(`evaluate_foreign_flow_
  reversal_exit`) 가격 조건과 무관하게 조기 청산 판정을 반환한다.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

# -- 전략 상수 (PLAN.md §5.54, 사용자 확정값 — 바꾸지 말 것) ------------------------

TARGET_CODE = "0167A0"  # SOL AI반도체TOP2플러스 — 자동매매 대상 종목 고정
TARGET_QTY = 1  # 1주 고정

STOP_LOSS_PCT = -1.5  # 진입가 대비 -1.5% 이하 -> 즉시 손절 (상태 무관)
TRAIL_ACTIVATE_PCT = 1.0  # 진입가 대비 +1% 이상 -> holding에서 trailing으로 전환
TRAIL_EXIT_FLOOR_PCT = 0.5  # trailing 중 dead cross + 진입가 대비 +0.5% 이하 -> 청산

# -- 안전 규칙 상수 (PLAN.md §5.55, 사용자 확정값 — 바꾸지 말 것) --------------------

# §5.55-3: 리스크 경보(서킷브레이커/사이드카/거래량급증/수급가속도, §5.36) 활성
# 중에 보유 포지션에 적용하는 임시 손절선 — 평상시 STOP_LOSS_PCT(-1.5%)보다
# 타이트하다. STOP_LOSS_PCT 자체는 건드리지 않는다(리스크 경보가 없을 때는
# 그대로 -1.5%).
STOP_LOSS_PCT_RISK_ALERT = -0.8

# §5.55-1: 개장 직후/마감 직전 변동성이 큰 구간 — 신규 진입만 금지(청산은 예외 없음).
ENTRY_BLOCK_OPEN_START = dt.time(9, 0)
ENTRY_BLOCK_OPEN_END = dt.time(9, 10)
ENTRY_BLOCK_CLOSE_START = dt.time(15, 20)
ENTRY_BLOCK_CLOSE_END = dt.time(15, 30)

# §5.55-2: 장마감 전 조건부 오버나잇 청산 판정 시간대 — ENTRY_BLOCK_CLOSE_*와
# 같은 15:20~15:30 구간이지만 의미가 다르므로(진입 금지 vs 강제청산 판정) 별도
# 이름의 상수로 둔다.
EOD_FORCED_EXIT_START = dt.time(15, 20)
EOD_FORCED_EXIT_END = dt.time(15, 30)

Status = Literal["idle", "holding", "trailing"]


def _pct_change(entry_price: float, current_price: float) -> float:
    """진입가 대비 현재가의 등락률(%)."""
    return (current_price - entry_price) / entry_price * 100


def evaluate_entry(ma_cross_state: str | None, volume_is_spike: bool | None) -> bool:
    """진입 조건: ma_cross golden AND volume_spike. 어느 한쪽이라도 아니면(계산
    불가로 None인 경우 포함) False — 데이터 부족을 "진입 신호 있음"으로 잘못
    해석하지 않기 위해 명시적으로 `is golden`/`is True`만 참으로 취급한다."""
    return ma_cross_state == "golden" and volume_is_spike is True


def evaluate_stop_loss(entry_price: float, current_price: float, threshold_pct: float = STOP_LOSS_PCT) -> bool:
    """손절 조건: 현재가가 진입가 대비 `threshold_pct`(기본 -1.5%, `STOP_LOSS_PCT`)
    이하. 상태(holding/trailing)와 무관하게 항상 먼저 확인해야 하는 최우선
    조건이다. `threshold_pct`를 명시적으로 넘기면(§5.55-3, 리스크 경보 활성 시
    `STOP_LOSS_PCT_RISK_ALERT`) 그 값을 대신 쓴다 — 기존 호출부는 인자를 그대로
    두 개만 넘기던 그대로 동작한다(하위호환 기본값)."""
    return _pct_change(entry_price, current_price) <= threshold_pct


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
    risk_alert_active: bool = False,
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

    `risk_alert_active`(§5.55-3, 기본 False — 기존 호출부와 하위호환)가 True면
    손절 임계값으로 `STOP_LOSS_PCT`(-1.5%) 대신 `STOP_LOSS_PCT_RISK_ALERT`
    (-0.8%)를 쓴다 — 그 밖의 판정(트레일 전환/청산)은 영향받지 않는다.

    Returns ``{"action": "stop_loss"|"trail_activate"|"exit_trail"|
    "peak_update"|"none", "reason": str, "new_peak_price": float (해당 시)}``.
    """
    pct = round(_pct_change(entry_price, current_price), 4)
    stop_loss_threshold = STOP_LOSS_PCT_RISK_ALERT if risk_alert_active else STOP_LOSS_PCT

    if evaluate_stop_loss(entry_price, current_price, stop_loss_threshold):
        return {
            "action": "stop_loss",
            "reason": (
                f"손절 조건 충족: 현재가 {current_price} vs 진입가 {entry_price} "
                f"({pct:+.2f}%) <= {stop_loss_threshold}% (status={status}"
                + (", 리스크 경보 활성 -> 임시 손절선 적용" if risk_alert_active else "")
                + ")"
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


# ---------------------------------------------------------------------------
# PLAN.md §5.55 — 안전 규칙 (2026-08-06, 실제 손실 사고 이후)
# ---------------------------------------------------------------------------


def is_entry_blocked_by_time(now_kst: dt.time) -> bool:
    """§5.55-1: 개장 후 10분(09:00~09:10) 또는 마감 전 10분(15:20~15:30, KST)
    이면 True — 이 시간대엔 **신규 진입만** 금지한다. 손절/트레일청산/강제청산은
    이 함수를 절대 거치지 않는다(호출부가 진입 판정 경로에서만 이 함수를
    부른다) — 항상 허용이라는 절대 원칙은 이 함수 존재 자체와 무관하게 유지된다.
    양쪽 경계 모두 포함(<=)한다."""
    return (ENTRY_BLOCK_OPEN_START <= now_kst <= ENTRY_BLOCK_OPEN_END) or (
        ENTRY_BLOCK_CLOSE_START <= now_kst <= ENTRY_BLOCK_CLOSE_END
    )


def evaluate_eod_forced_exit(
    now_kst: dt.time,
    status: str,
    unrealized_pnl_positive: bool,
    kosdaq_foreign_streak_ok: bool,
    risk_alert_active: bool,
) -> dict[str, Any]:
    """§5.55-2(최우선 규칙 — 2026-08-05 실제 손실 사고를 직접 막았을 규칙):
    15:20~15:30 KST에 보유 중(holding/trailing)이면 오버나잇 허용 여부를
    판정한다.

    판정 순서:
    1. 이 시간대가 아니거나 보유 중이 아니면(status가 holding/trailing이
       아니면) 판정 대상이 아니다 — `should_exit`: False.
    2. 리스크 경보가 하나라도 활성 상태(`risk_alert_active`)면 **무조건 청산**.
    3. 그 외에는 다음 3개를 **전부** 충족해야 오버나잇 보유를 허용한다 — 하나라도
       실패하면 무조건 청산:
       a. `unrealized_pnl_positive`(현재가가 진입가보다 높음).
       b. `status == "trailing"`(장중 한 번이라도 +1% 이상 올라 트레일 모드에
          진입한 이력 — 그냥 "holding"인 채로 15:20을 맞으면 그 자체가 "이
          진입이 안 먹혔다"는 신호이므로 오버나잇 자격이 없다).
       c. `kosdaq_foreign_streak_ok`(코스닥 외국인 `confirmed_streak >= 0` —
          연속 매도 중이 아님, 호출부가 `/api/markets/regime`에서 계산해
          넘긴다).

    호출부가 regime/리스크 경보 조회 자체에 실패했을 때 이 값들을 어떻게
    채울지는(예: "확인 못 함 = 조건 미충족으로 간주") 이 함수의 책임이
    아니다 — 순수 함수라 호출부가 넘긴 bool 그대로만 판정한다.

    Returns ``{"should_exit": bool, "reason": str}``."""
    if not (EOD_FORCED_EXIT_START <= now_kst <= EOD_FORCED_EXIT_END):
        return {
            "should_exit": False,
            "reason": f"15:20~15:30 KST 시간대 아님(now={now_kst}) — 오버나잇 강제청산 판정 대상 아님",
        }
    if status not in ("holding", "trailing"):
        return {
            "should_exit": False,
            "reason": f"보유 중 아님(status={status!r}) — 오버나잇 강제청산 판정 대상 아님",
        }

    if risk_alert_active:
        return {
            "should_exit": True,
            "reason": "15:20 이후 첫 폴링: 리스크 경보 활성 중 -> 오버나잇 무조건 청산",
        }

    failed_conditions: list[str] = []
    if not unrealized_pnl_positive:
        failed_conditions.append("평가손익<=0")
    if status != "trailing":
        failed_conditions.append(f"status={status!r}(trailing 아님 -> 장중 트레일 진입 이력 없음)")
    if not kosdaq_foreign_streak_ok:
        failed_conditions.append("코스닥 외국인 confirmed_streak<0(연속 매도 중)")

    if failed_conditions:
        return {
            "should_exit": True,
            "reason": (
                "15:20 이후 오버나잇 조건 미충족(" + ", ".join(failed_conditions) + ") -> 강제 청산"
            ),
        }

    return {
        "should_exit": False,
        "reason": (
            "15:20 이후 오버나잇 조건 3개(평가손익>0, status=trailing, 코스닥 외국인 "
            "confirmed_streak>=0) 모두 충족, 리스크 경보도 없음 -> 오버나잇 보유 허용"
        ),
    }


def foreign_flow_sign(net_value: float | None) -> str | None:
    """§5.55-4: 외인 현물 누적 순매수 부호를 `"positive"`/`"negative"`/`None`
    (0 또는 값 없음)으로 인코딩한다. `AutoTradeState.entry_foreign_flow_sign`에
    진입 시점 값을 그대로 저장하고, 이후 폴링마다 현재 값을 같은 함수로 인코딩해
    `evaluate_foreign_flow_reversal_exit`로 비교한다."""
    if net_value is None:
        return None
    if net_value > 0:
        return "positive"
    if net_value < 0:
        return "negative"
    return None


def evaluate_foreign_flow_reversal_exit(entry_sign: str | None, current_sign: str | None) -> dict[str, Any]:
    """§5.55-4: 보유 중, 진입 시점 대비 외인 현물 누적 순매수 부호가 반전되면
    (매수->매도 또는 매도->매수) 가격 조건과 무관하게 조기 청산 판정을
    반환한다. 둘 중 하나라도 `None`이면(진입 시점 기록이 없었거나, 오늘 아직
    수급 데이터가 없어 0/누락) 반전 여부를 판정할 수 없으므로 청산하지
    않는다 — 데이터 부족을 "반전 발생"으로 잘못 해석하지 않기 위해
    (`evaluate_entry`가 None을 "신호 있음"으로 취급하지 않는 것과 동일한 원칙).

    Returns ``{"should_exit": bool, "reason": str}``."""
    if entry_sign is None or current_sign is None:
        return {
            "should_exit": False,
            "reason": (
                f"외인 현물 수급 방향 판정 불가(entry_sign={entry_sign!r}, "
                f"current_sign={current_sign!r}) -> 반전 조기청산 검토 안 함"
            ),
        }
    if entry_sign != current_sign:
        return {
            "should_exit": True,
            "reason": f"외인 현물 수급 방향 반전 감지: 진입 시점 {entry_sign} -> 현재 {current_sign} -> 조기 청산",
        }
    return {
        "should_exit": False,
        "reason": f"외인 현물 수급 방향 유지(entry={entry_sign}, current={current_sign}) -> 조기청산 없음",
    }
