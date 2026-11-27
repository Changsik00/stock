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
- **트레일 청산**: "trailing" 상태에서 현재가가 신고가(`peak_price`) 대비
  `TRAIL_GIVEBACK_PCT`(0.5%) 이상 반납하면 매도. 그 전까지는 신고가를 계속
  갱신하며 보유한다.

  **2026-09-09 교체(PLAN.md §5.83, ratchet 방식으로 전환)** — 원래는
  "`ma_cross.state == "dead"`(추세 반전 확인) AND 진입가 대비 +0.5% 이하로
  되돌아옴" 둘 다 요구했다(`quant/auto_trade_backtest.py` 모듈 docstring이
  `trail_mode="ratchet"`이라는 이름으로 붙여둔 비교 실험용 대안이 바로 이
  전환 후 규칙). 실거래 5주(26건) 감사 로그를 보니 이 원래 조건이 사실상
  작동하지 않았다 — dead cross는 교차가 일어난 바로 그 1분봉에서만 참인
  순간 이벤트라(`quant/signals.py::moving_average_cross` docstring), 진입
  골든크로스가 겪었던 것과 같은 폴링 타이밍 미스(§5.54-6 "2026-08-05 실측
  버그 수정" 절 참고, 그쪽은 lookback으로 고쳤지만 트레일 청산 쪽은 "이번
  수정 범위 밖"으로 남겨뒀었다)를 그대로 겪어, 트레일 진입 후 반전되면
  거의 항상 이 조건을 못 맞고 그대로 `STOP_LOSS_PCT`(-1.5%)까지 밀려버렸다
  (실거래 손절 15건이 전체 손실의 거의 전부, 트레일 청산은 5주간 딱 1번).
  같은 실 1분봉 데이터로 세 방식(원래 방식/`ratchet`/`daily_reset`)을
  비교한 백테스트에서도 원래 방식만 뚜렷하게 나빴다(누적수익률 -10.97% vs
  -1.43% vs +4.61%, `quant/auto_trade_intraday_backtest.py`) — 사용자
  확인 후 `ratchet`으로 교체를 확정했다. 새 조건은 진입가가 아니라
  **고점** 기준이고 dead cross 확인도 요구하지 않는다(순수 가격 임계값이라
  "순간 이벤트를 놓친다"는 문제 자체가 없다).

## 안전 규칙 추가 (PLAN.md §5.55, 2026-08-06 — 실제 손실 사고 이후)

2026-08-05 오버나잇 갭하락으로 의도한 -1.5%가 아니라 -4.87%에 손절되는 실제
손실 사고가 발생했다(경위는 PLAN.md §5.55 본문 참고). 사용자가 지적한 4가지를
그대로 규칙화했다 — 여기에도 새 판단은 없다, 이미 있는 지표(regime/risk_alert/
foreign-position)를 그대로 매핑한 것뿐이다:

- **§5.55-1 진입 시간 필터**: 개장 후 10분(09:00~09:10)·마감 전 10분
  (15:10~15:30, Phase 5.134에서 15:20->15:10) KST엔 **신규 진입만** 금지(`is_entry_blocked_by_time`). 손절/
  트레일청산/강제청산은 이 함수를 절대 거치지 않으므로 항상 그대로 작동한다.
- **§5.55-2 장마감 전 조건부 오버나잇 청산**(이번 사고를 직접 막았을 최우선
  규칙): 15:10 KST 이후 보유 중이면 `evaluate_eod_forced_exit`이 리스크 경보/
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
# Phase 5.122(2026-11-26) — 더 이상 "진입 수량"이 아니다(수량은 compute_order_qty로 주문 시점에
# 산출). entry_qty가 비어 있는 비정상 보유 상태의 매도 수량 폴백·하위 호환 import용으로만 남긴다.
TARGET_QTY = 1
TARGET_NAME = "SOL AI반도체TOP2플러스"  # TARGET_CODE의 표시용 이름

STOP_LOSS_PCT = -1.5  # 진입가 대비 -1.5% 이하 -> 즉시 손절 (상태 무관)
TRAIL_ACTIVATE_PCT = 1.0  # 진입가 대비 +1% 이상 -> holding에서 trailing으로 전환
TRAIL_GIVEBACK_PCT = 0.5  # PLAN.md §5.83(2026-09-09, ratchet 전환) — 신고가(peak_price) 대비 -0.5% 이하 반납 -> 청산

# -- 안전 규칙 상수 (PLAN.md §5.55, 사용자 확정값 — 바꾸지 말 것) --------------------

# §5.55-3: 리스크 경보(서킷브레이커/사이드카/거래량급증/수급가속도, §5.36) 활성
# 중에 보유 포지션에 적용하는 임시 손절선 — 평상시 STOP_LOSS_PCT(-1.5%)보다
# 타이트하다. STOP_LOSS_PCT 자체는 건드리지 않는다(리스크 경보가 없을 때는
# 그대로 -1.5%).
STOP_LOSS_PCT_RISK_ALERT = -0.8

# §5.55-1: 개장 직후/마감 직전 변동성이 큰 구간 — 신규 진입만 금지(청산은 예외 없음).
ENTRY_BLOCK_OPEN_START = dt.time(9, 0)
ENTRY_BLOCK_OPEN_END = dt.time(9, 10)
ENTRY_BLOCK_CLOSE_START = dt.time(15, 10)  # Phase 5.134: 15:20 -> 15:10(강제청산 시작과 동일)
ENTRY_BLOCK_CLOSE_END = dt.time(15, 30)

# §5.55-2: 장마감 전 조건부 오버나잇 청산 판정 시간대 — ENTRY_BLOCK_CLOSE_*와
# 같은 15:10~15:30 구간이지만 의미가 다르므로(진입 금지 vs 강제청산 판정) 별도
# 이름의 상수로 둔다.
EOD_FORCED_EXIT_START = dt.time(15, 10)  # Phase 5.134: 15:20 -> 15:10(동시호가 전 정규장에서 체결되도록)
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


def evaluate_trail_exit(peak_price: float, current_price: float) -> bool:
    """트레일 청산 조건(PLAN.md §5.83, ratchet 방식) — 현재가가 신고가
    (`peak_price`) 대비 `TRAIL_GIVEBACK_PCT`(0.5%) 이상 반납하면 청산.
    dead cross 확인은 요구하지 않는다 — 모듈 docstring "2026-09-09 교체"
    절 참고(dead cross는 순간 이벤트라 폴링과 맞아떨어지기 어려웠다)."""
    return current_price <= peak_price * (1 - TRAIL_GIVEBACK_PCT / 100)


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

    **2026-09-09(PLAN.md §5.83)** — 트레일 청산이 ratchet 방식(고점 대비
    반납폭)으로 바뀌면서 더 이상 ma_cross가 필요 없어 `ma_cross_state`
    매개변수를 없앴다(모듈 상단 docstring "트레일 청산" 절 참고) — 호출부
    (`collectors/auto_trader.py::_handle_position`)도 함께 갱신했다.

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

    if evaluate_trail_exit(new_peak, current_price):
        giveback_pct = round((new_peak - current_price) / new_peak * 100, 4)
        return {
            "action": "exit_trail",
            "reason": (
                f"트레일 청산 조건 충족: 현재가 {current_price}가 고점 {new_peak} 대비 "
                f"-{giveback_pct:.2f}% 반납(>= {TRAIL_GIVEBACK_PCT}%) (진입가 대비 {pct:+.2f}%)"
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
            f"trailing 유지: 청산 조건 미충족 (현재가 {current_price} vs 고점 {new_peak}, "
            f"진입가 대비 {pct:+.2f}%)"
        ),
    }


def compute_order_qty(
    price: float | None,
    max_order_notional_krw: float,
    total_budget_krw: float,
    orderable_cash_krw: float | None,
) -> int:
    """Phase 5.122 — 매수 수량 = floor(min(주문 1건 캡, 누적 예산, 계좌 주문가능금액) / 지정가).

    price<=0/None, 어느 한도라도 <=0, 주문가능금액 None(조회 실패) -> 0(주문 보류).
    순수 함수 — 호출부가 0을 받으면 주문 없이 budget_blocked로 기록한다."""
    if price is None or price <= 0:
        return 0
    if orderable_cash_krw is None:
        return 0
    limits = (max_order_notional_krw, total_budget_krw, orderable_cash_krw)
    if any(x is None or x <= 0 for x in limits):
        return 0
    return int(min(limits) // price)


def classify_buy_fill(ordered_qty: int, actual_qty: int | None) -> str:
    """Phase 5.122 — 매수 주문 후 실보유수량으로 체결 결과를 분류한다(순수).

    "full"(전량) / "partial"(0<실보유<주문) / "unconfirmed"(0주·조회불가·주문수량 초과 등
    판정 불가 — 기존 동작: 취소 후 idle 유지)."""
    if actual_qty is None:
        return "unconfirmed"
    if actual_qty == ordered_qty:
        return "full"
    if 0 < actual_qty < ordered_qty:
        return "partial"
    return "unconfirmed"


def classify_sell_fill(held_qty: int, remaining_qty: int | None) -> str:
    """Phase 5.122 — 매도 주문 후 남은 실보유수량으로 체결 결과를 분류한다(순수).

    "full"(0주 남음 -> idle) / "partial"(0<잔량<보유 -> 잔량으로 holding 유지) /
    "unconfirmed"(체결 없음·조회불가 — 기존 동작: 취소 후 포지션 유지)."""
    if remaining_qty is None:
        return "unconfirmed"
    if remaining_qty == 0:
        return "full"
    if 0 < remaining_qty < held_qty:
        return "partial"
    return "unconfirmed"


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
    """§5.55-1: 개장 후 10분(09:00~09:10) 또는 마감 전 20분(15:10~15:30, KST; Phase 5.134)
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
    15:10~15:30 KST에 보유 중(holding/trailing)이면 오버나잇 허용 여부를
    판정한다.

    판정 순서:
    1. 이 시간대가 아니거나 보유 중이 아니면(status가 holding/trailing이
       아니면) 판정 대상이 아니다 — `should_exit`: False.
    2. 리스크 경보가 하나라도 활성 상태(`risk_alert_active`)면 **무조건 청산**.
    3. 그 외에는 다음 3개를 **전부** 충족해야 오버나잇 보유를 허용한다 — 하나라도
       실패하면 무조건 청산:
       a. `unrealized_pnl_positive`(현재가가 진입가보다 높음).
       b. `status == "trailing"`(장중 한 번이라도 +1% 이상 올라 트레일 모드에
          진입한 이력 — 그냥 "holding"인 채로 15:10을 맞으면 그 자체가 "이
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
            "reason": f"15:10~15:30 KST 시간대 아님(now={now_kst}) — 오버나잇 강제청산 판정 대상 아님",
        }
    if status not in ("holding", "trailing"):
        return {
            "should_exit": False,
            "reason": f"보유 중 아님(status={status!r}) — 오버나잇 강제청산 판정 대상 아님",
        }

    if risk_alert_active:
        return {
            "should_exit": True,
            "reason": "15:10 이후 첫 폴링: 리스크 경보 활성 중 -> 오버나잇 무조건 청산",
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
                "15:10 이후 오버나잇 조건 미충족(" + ", ".join(failed_conditions) + ") -> 강제 청산"
            ),
        }

    return {
        "should_exit": False,
        "reason": (
            "15:10 이후 오버나잇 조건 3개(평가손익>0, status=trailing, 코스닥 외국인 "
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


# ---------------------------------------------------------------------------
# Phase 5.134 — KRX 동시호가 구간(장 시작 08:30~09:00, 장 마감 15:20~15:30)
# ---------------------------------------------------------------------------
# 동시호가에는 주문이 접수만 되고 09:00/15:30에 단일가로 한 번에 체결된다 — 주문 직후
# 체결 확인·취소를 하면 체결 시점에 주문이 남지 않는다(auto_trader 모듈 docstring 참고).
AUCTION_OPEN_START = dt.time(8, 30)
AUCTION_OPEN_END = dt.time(9, 0)  # 배타
AUCTION_CLOSE_START = dt.time(15, 20)
AUCTION_CLOSE_END = dt.time(15, 30)  # 배타

# 동시호가 매도 지정가 = 매수1호가 x (1 - 2%) — 단일가 체결이라 실제 체결가는 단일가.
AUCTION_SELL_DISCOUNT_PCT = 2


def is_auction_window(now_kst: dt.time) -> bool:
    """KRX 동시호가 구간이면 True — 08:30<=t<09:00 또는 15:20<=t<15:30(끝은 배타)."""
    return (AUCTION_OPEN_START <= now_kst < AUCTION_OPEN_END) or (
        AUCTION_CLOSE_START <= now_kst < AUCTION_CLOSE_END
    )


# 단일가 체결(09:00/15:30) 직후 잔고(kt00018) 반영까지의 유예 — 이 시각 전엔 대기 매도를 정산하지 않는다.
# 반영 전에 정산하면 실제로 팔렸는데 '미체결'로 오판해 취소·포지션 유지(상태-현실 불일치)가 된다.
AUCTION_OPEN_SETTLE_AT = dt.time(9, 1)
AUCTION_CLOSE_SETTLE_AT = dt.time(15, 31)


def is_auction_settle_wait(now_kst: dt.time) -> bool:
    """동시호가 대기 매도를 아직 정산하면 안 되는 구간이면 True — 동시호가 구간 + 체결 후 1분 유예
    (08:30<=t<09:01 또는 15:20<=t<15:31)."""
    return (AUCTION_OPEN_START <= now_kst < AUCTION_OPEN_SETTLE_AT) or (
        AUCTION_CLOSE_START <= now_kst < AUCTION_CLOSE_SETTLE_AT
    )


def krx_tick_size(price: float, etf: bool = True) -> int:
    """KRX 호가 단위(원). ETF/ETN은 가격과 무관하게 5원, 주식은 2023년 개편 가격대별 단위.
    이 엔진은 ETF(0167A0)만 거래하므로 기본값은 ETF."""
    if etf:
        return 5
    for upper, tick in ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500)):
        if price < upper:
            return tick
    return 1_000


def auction_sell_price(best_bid: float, etf: bool = True) -> int:
    """동시호가 매도 주문가 — 매수1호가의 98%를 호가 단위로 **내림**한 정수 가격(정수 연산으로
    부동소수 오차 없음). 체결 우선(낮은 가격)이 목적이라 항상 내림한다."""
    discounted = int(best_bid) * (100 - AUCTION_SELL_DISCOUNT_PCT) // 100
    tick = krx_tick_size(discounted, etf)
    return discounted // tick * tick
