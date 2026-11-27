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
   `AUTO_TRADE_TOTAL_BUDGET_KRW`(기본 50,000원 — Phase 5.122에서 25,000원에서 상향)와
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

**2026-09-09 후속(PLAN.md §5.83)** — 트레일 청산 쪽도 결국 똑같은 문제를
겪었다(dead cross가 순간 이벤트라 폴링과 못 맞아떨어짐, 실거래 5주 동안
트레일 청산이 딱 1번만 발동). 이번엔 lookback을 넓히는 대신 `quant/
auto_trade_rules.py::evaluate_trail_exit`를 dead cross 확인이 필요 없는
ratchet 방식(고점 대비 반납폭)으로 교체해 이 순간 이벤트 의존 자체를
없앴다 — 자세한 경위는 그 모듈 docstring "트레일 청산" 절 참고.

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

## 미체결 확인(PLAN.md §5.71, 2026-08-19 — 실사고 이후 추가)

**과거(2026-08-19 이전)엔 이 절이 "주문 접수 = 상태 전이, 미체결 확인은
이 엔진의 책임이 아니다"였다 — 그 전제가 실제로 깨졌다.** 8/14 15:20 KST
EOD 강제청산이 매도 지정가 주문을 제출해 접수(`return_code=0`)까지는
됐지만, 가격이 그 사이 움직여 장마감(15:30)까지 체결되지 않은 채 자동
취소됐다. 그런데 `_execute_sell`은 제출 성공만 보고 그 즉시 상태를
idle로 갱신해버려, 실제로는 그대로 보유 중이던 포지션(1주, 매입가
18,015원)에 5일간(8/14~8/19) 손절/트레일링 감시가 전혀 작동하지 않았다
— 사용자가 실계좌를 직접 조회(kt00004/kt00018)해 발견, 상태를 수동으로
`holding`으로 정정하자마자 30초 손절 감시가 즉시 작동해 -5.52% 구간에서
실제로 청산됐다(체결 확인 완료).

이후 `_confirm_holdings_qty`/`_cancel_unfilled_order_silently`(아래,
`_best_fill_price` 바로 뒤)를 매수/매도 주문 제출 직후마다 호출한다 —
확인이 실패하면(아래 §5.78 참고) `cancel_order`(kt10003)로 취소를 시도한
뒤 **상태 전이를 하지 않고**(idle<->holding 어느 방향으로도 넘어가지
않음) `*_unconfirmed` 이벤트로만 로그한다 — 다음 폴링이 최신 호가로
자연히 재시도한다.

**2026-08-26 교체(PLAN.md §5.78 — §5.71과 반대 방향의 재사고)**: 처음
버전(`_order_still_unfilled`)은 `get_unfilled_orders`(ka10075, **미체결**
주문 목록)에 방금 낸 주문번호가 없으면 체결된 것으로 **추론**했다. 그런데
주문은 체결돼서 그 목록에서 빠질 수도 있지만 거부/취소돼서 빠질 수도
있다 — 그 함수는 이 둘을 구분하지 못했다. 실제로 사용자가 수동 매도
직후(대금 미결제 추정) 자동 매수가 즉시 거부됐는데도 이 함수가 "체결됨"
으로 오판해 `AutoTradeState`가 `holding`으로 잘못 전이됐고, 이후 36분간
모든 매도 시도가 "매도가능수량 0"으로 실패하며 재시도만 반복했다(사용자가
키움 HTS로 실보유 0주를 직접 확인해 발견, 상태를 수동으로 idle로 정정).
지금 버전(`_confirm_holdings_qty`)은 "왜 목록에서 사라졌는가" 추론 대신
`get_account_holdings_qty`(kt00018, 신규)로 **실제 보유수량을 직접**
확인한다 — 자세한 설계는 그 함수 docstring 참고.

## 동시호가 구간 매도(PLAN.md §5.134, 2026-11-27 — 사용자 승인 실거래 규칙 변경)

KRX 동시호가(장 시작 08:30~09:00, 장 마감 15:20~15:30)에는 주문이 접수만 되고
09:00/15:30에 단일가로 한 번에 체결된다. 그런데 위 "미체결 확인"은 주문 후 수 초 안에
실보유 0을 못 보면 취소하고 다음 폴링(60초/30초)이 다시 주문·취소를 반복해, 마지막 취소가
체결 시점 직전이라 **체결 시점에 주문이 남지 않았다**(실측: sell_unconfirmed 206건 중
158건이 15:20~15:30, 43건이 09:00 이전; 8/19 이후 EOD 강제청산이 사실상 한 번도 체결 안 됨).

이 구간에서 매도가 결정되면(`_place_auction_sell`) 매수1호가 -2%(호가단위 내림, `auction_sell_price`)
지정가로 주문하고 **확인·취소 없이 주문을 유지**한다. 주문번호·수량·원래 이벤트·사유를
`AutoTradeState.pending_sell_*`에 저장하고 `sell_pending_auction`을 기록한다. 대기 중에는
`run_auto_trade`/`watch_stop_loss` 모두 새 매도를 내지 않고(조용히 반환), 구간이 끝난 뒤 첫
폴링에서 `_settle_pending_sell`이 실보유로 정산한다: 0주=원래 청산 이벤트로 기록·idle /
일부=잔량 갱신·잔여 취소(`sell_unconfirmed`) / 그대로=취소·`sell_unconfirmed`·포지션 유지 /
조회 불가=대기 유지(추측하지 않음). 정규장·15:10~15:20 구간은 기존 즉시 확인·취소 그대로다.

## 안전 규칙 추가(PLAN.md §5.55, 2026-08-06 — 2026-08-05 실제 손실 사고 이후)

2026-08-05 오버나잇 갭하락으로 의도한 -1.5%가 아니라 -4.87%에 손절된 실제
사고 이후 추가된 4개 안전 규칙. 판정 로직 자체(`quant/auto_trade_rules.py`
의 `is_entry_blocked_by_time`/`evaluate_eod_forced_exit`/
`evaluate_foreign_flow_reversal_exit`/`foreign_flow_sign`)는 순수 함수이고,
이 파일은 그 판정에 필요한 값을 조회해 넘기고 실행(주문/로그/상태 갱신)만
한다 — 기존 `decide_idle_action`/`decide_position_action` + 이 파일의 분업
그대로다.

- **§5.55-1 진입 시간 필터**: `_handle_idle`이 `decide_idle_action`이 "enter"를
  반환한 *뒤에* `is_entry_blocked_by_time`으로 한 번 더 거른다(`check_entry_
  budget`과 같은 위치 — 신호 자체는 순수 판정, 실행 가드는 호출부가 겹겹이
  확인). 손절/트레일청산/강제청산 경로는 이 함수를 절대 호출하지 않는다.
- **§5.55-2 장마감 전 조건부 오버나잇 청산**(최우선 규칙): `_handle_position`이
  손절/트레일청산으로 이어지지 않은 뒤(`_check_forced_exits`) 리스크 경보
  (`_warm_index_tiles_live` 재사용) + regime(`_warm_regime` 재사용, 코스닥
  외국인 `confirmed_streak`)를 조회해 `evaluate_eod_forced_exit`에 넘긴다.
  regime/리스크 조회가 상대적으로 무겁고 15:10~15:30 사이에만 의미가 있어
  30초 `watch_stop_loss`가 아니라 60초 `run_auto_trade`에서만 평가한다.
  **regime 조회 자체가 실패하면 안전 측(청산)으로 폴백**한다 — "확인 못 함"을
  "조건 충족"으로 착각하지 않기 위해서다(이 파일의 다른 실패 처리와 다른
  선택 — 아래 `_check_forced_exits` 주석 참고).
- **§5.55-3 리스크 경보 연동**: 매 폴링(`run_auto_trade`/`watch_stop_loss`
  둘 다) `_warm_index_tiles_live`의 `risk.alerts`를 조회해 `risk_alert_active`
  로 넘긴다 — 활성 중이면 (a) idle 상태에서 신규 진입 금지, (b) 보유 중이면
  `decide_position_action`/`evaluate_stop_loss`에 `STOP_LOSS_PCT_RISK_ALERT`
  (-0.8%)를 적용해 손절선을 타이트하게 조정. 이 조회는 리스크 배너 자체가
  "지금 당장의 위험" 보조 신호라 **조회 실패 시 위험 없음(False)으로 폴백**
  한다(fail-open) — 그래도 절대 손절(-1.5%)은 항상 살아있어 최소 안전망은
  유지된다.
- **§5.55-4 수급 방향 전환 조기청산**: 진입 체결 직후 `intraday_snapshot.
  get_foreign_position_series`(§5.50-6, `spot` 필드 — 코스피+코스닥 외국인
  현물 합산 누적)의 최신 값을 `foreign_flow_sign`으로 부호만 인코딩해
  `AutoTradeState.entry_foreign_flow_sign`에 기록한다. 이후 보유 중 매
  `run_auto_trade` 폴링마다(DB 조회뿐, 새 외부 호출 없음) 현재 부호를 다시
  구해 `evaluate_foreign_flow_reversal_exit`로 비교 — 반전되면 가격과 무관하게
  조기 청산한다.

네 규칙 모두 **매도(청산)는 절대 막지 않는다** — 시간 필터(§5.55-1)는 진입
경로에만 적용되고, 나머지 세 규칙은 전부 "매도를 앞당기는" 방향으로만
작동한다(청산 조건을 추가할 뿐, 기존 손절/트레일청산을 지연시키거나 막는
로직은 어디에도 없다).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..clients.kiwoom import MAX_ORDER_NOTIONAL_KRW, KiwoomClient, parse_quote_levels
from ..market_hours import KST
from ..models import AutoTradeLog, AutoTradeState
from ..quant.auto_trade_rules import (
    EOD_FORCED_EXIT_END,
    EOD_FORCED_EXIT_START,
    STOP_LOSS_PCT,
    STOP_LOSS_PCT_RISK_ALERT,
    TARGET_CODE,
    TARGET_QTY,
    auction_sell_price,
    check_entry_budget,
    classify_buy_fill,
    classify_sell_fill,
    compute_order_qty,
    decide_idle_action,
    decide_position_action,
    evaluate_eod_forced_exit,
    evaluate_foreign_flow_reversal_exit,
    evaluate_stop_loss,
    foreign_flow_sign,
    is_auction_settle_wait,
    is_auction_window,
    is_entry_blocked_by_time,
)
from ..quant.exit_rules import decide_manual_exit
from ..quant.nq_shadow import merge_nq_shadow
from ..quant.signals import moving_average_cross, volume_spike
from ..routers.markets import _warm_index_tiles_live, _warm_regime
from ..routers.stocks import _warm_stock_intraday
from . import intraday_snapshot

logger = logging.getLogger(__name__)

# 이 자동매매 기능 전체의 누적 예산(원) — 사용자가 실제 입금한 금액(PLAN.md
# §5.54). clients/kiwoom.py의 MAX_ORDER_NOTIONAL_KRW(5만원, 주문 1건 캡)와는
# 별개로 추가 적용된다 — 둘 다 통과해야 매수 가능
# (quant/auto_trade_rules.check_entry_budget). 실제 매수는 항상 이 값과
# MAX_ORDER_NOTIONAL_KRW 중 더 작은 쪽이 사실상의 상한이 된다.
AUTO_TRADE_TOTAL_BUDGET_KRW = 50_000  # Phase 5.122: 25,000 -> 50,000(1주 고정 폐지, 5만원 한도 내 최대 수량)

# 2026-08-21(PLAN.md §5.73, 사용자 지적 — "정보가 장황하기만 하지 유의미한
# 정보를 제공한다고 생각하지는 않아") — 매매일지(`GET /api/auto-trade/log`)
# 기본 보기에서 "실제로 주문이 체결된" 이벤트만 남기고 나머지(진입 시도가
# 조건에 막힘/실패/체결 미확인/트레일 전환 같은 상태 메모)는 접어 둔다.
# `trail_activate`는 상태 전이이긴 하지만 주문 자체가 안 나가므로 이
# 집합에서 제외한다("실제 거래"의 기준은 "체결된 주문이 있었는가"로 통일).
TRADE_EVENT_TYPES = frozenset(
    {
        "entry",
        "manual_entry",
        "exit_stop_loss",
        "exit_trail",
        "exit_eod_forced",
        "exit_flow_reversal",
        "exit_manual",
        "exit_manual_rule",
    }
)

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


# 2026-08-19(PLAN.md §5.71, 사용자 지적 — "매매일지 900건 이상, 의미 있는
# 정보가 맞을까?") — 완전히 같은 이벤트(event_type+reason 바이트 단위 동일)가
# 폴링(60초/30초)마다 반복 기록되던 노이즈를 억제한다(실측: entry_blocked_risk가
# 완전히 정적인 문구로 8/17 하루에만 324건). DB 쿼리가 아니라 in-process
# 모듈 전역 dict로 판정한다(_intraday_cache 등 기존 캐시와 동일한 패턴) —
# 이 프로젝트 테스트는 실 dev Postgres를 공유하므로 DB 쿼리 기반 dedup은
# 다른 테스트/실운영 행과 우연히 겹쳐 오탐 억제될 위험이 있다. 프로세스
# 재시작 시 자연히 초기화되고, 테스트 프로세스와 실배포 프로세스는 애초에
# 별개 프로세스라 절대 섞이지 않는다.
_LOG_DEDUP_WINDOW = dt.timedelta(minutes=5)
_last_log_by_code: dict[str, tuple[str, str, dt.datetime]] = {}


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
    truncated_reason = reason[:1000]
    now = dt.datetime.now(dt.timezone.utc)
    prev = _last_log_by_code.get(code)
    if (
        prev is not None
        and prev[0] == event_type
        and prev[1] == truncated_reason
        and (now - prev[2]) < _LOG_DEDUP_WINDOW
    ):
        # 직전 기록과 완전히 같은 사건이 5분 이내에 다시 왔다 — 새 행을 만들지
        # 않는다("한 번 나면 영원히 안 남"이 아니라 "짧은 간격 반복만 접는다" —
        # 5분이 지나거나 다른 이벤트가 끼어들면 다시 기록된다). entry/exit_*
        # 처럼 매번 체결가가 달라 reason이 사실상 겹칠 일이 없는 진짜 이벤트는
        # 전혀 영향받지 않는다.
        return
    _last_log_by_code[code] = (event_type, truncated_reason, now)

    # PLAN.md §5.91 — 청산/전환 이벤트에 NQ 섀도 값을 **추가 필드로만** 기록한다
    # (관찰 전용, 판정 입력 아님; 실패/값 없음은 null, 길이 초과 시 생략).
    if signal_snapshot is not None and (event_type.startswith("exit_") or event_type == "trail_activate"):
        try:
            from .nq_futures_sampler import get_nq_shadow

            signal_snapshot = merge_nq_shadow(
                signal_snapshot,
                await get_nq_shadow(session),
                lambda d: json.dumps(d, ensure_ascii=False, default=str),
            )
        except Exception as e:  # noqa: BLE001 - 관찰 필드 실패가 로그/판정을 막으면 안 된다
            logger.warning("nq_shadow 기록 생략: %s", e)

    session.add(
        AutoTradeLog(
            event_type=event_type,
            code=code,
            price=price,
            reason=truncated_reason,
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


# 2026-11-11: 1.0초 → 1.5초. 키움 공지(2026-11-09 "국내주식 계좌 TR 유량제어 정책 변경")로
# kt00018 포함 계좌 TR 19종이 2026-11-12 18시 이후 초당 5회 → 초당 1회로 줄어든다. 1.0초는
# 새 한도와 여유가 0이라 0.5초 여유를 둔다(최악 대기 2초 → 3초).
_CONFIRM_HOLDINGS_RETRY_DELAYS_SEC: tuple[float, ...] = (1.5, 1.5)
"""PLAN.md §5.82 — 첫 조회가 불일치할 때만 이 간격으로 추가 재조회한다(최대
2회, 총 대기 3초). 첫 조회가 바로 일치하면 이 재시도는 전혀 실행되지
않는다 — kt00018은 TR별 독립 토큰버킷(1req/s)이라 이 정도 추가 호출은
무시할 수준(모듈 docstring "미체결 확인" 절 참고)."""


async def _confirm_holdings_qty(client: KiwoomClient, code: str, expected_qty: int) -> bool:
    """주문 직후 실제 보유수량이 `expected_qty`와 일치하는지 **직접** 확인한다.

    2026-08-26(PLAN.md §5.78) — 이전 버전(`_order_still_unfilled`)은
    `get_unfilled_orders`(ka10075, **미체결** 주문 목록)에 방금 낸 주문번호가
    없으면 체결된 것으로 **추론**했다. 그런데 주문은 체결돼서 그 목록에서
    빠질 수도 있지만 거부/취소돼서 빠질 수도 있다 — 그 함수는 이 둘을
    구분하지 못했다. 실제로 사용자가 수동 매도 직후(대금 미결제 추정) 자동
    매수가 즉시 거부됐는데도 "체결됨"으로 오판해 `AutoTradeState`가
    `holding`으로 잘못 전이됐고, 이후 36분간 모든 매도 시도가 "매도가능수량
    0"으로 실패하며 재시도만 반복한 실사고가 있었다(§5.71과 반대 방향의
    상태-현실 불일치).

    이 버전은 "왜 목록에서 사라졌는가" 추론 대신 `get_account_holdings_qty`
    (kt00018)로 **실제 보유수량을 직접** 본다 — 이 전략은 항상 `TARGET_CODE`
    하나만 다루므로 기대값이 항상 명확하다(매수 직후엔 `TARGET_QTY`, 매도
    직후엔 0).

    **2026-09-07 재시도 추가(PLAN.md §5.82)** — 첫 조회가 불일치하면 즉시
    미확인으로 단정하지 않고 `_CONFIRM_HOLDINGS_RETRY_DELAYS_SEC` 간격으로
    최대 2회 더 조회한다. 실제로 주문이 체결됐는데 kt00018 잔고 스냅샷이
    그 찰나에 아직 반영되지 않아 "미확인"으로 오판된 사고가 매수(8/27)·
    매도(9/4) 양방향에서 각 1회씩 있었고, 둘 다 며칠 뒤 사용자가 실계좌를
    직접 봐서야 발견해 수동으로 정정했다 — 이 재시도는 그 좁은 타이밍
    창(수 초 이내 반영)만 메운다. 렉이 이보다 길면(과거 두 사고 모두 정확한
    지연 시간은 불명) 여전히 못 잡을 수 있어 `POST /api/auto-trade/sync`
    (routers/auto_trade.py)로 언제든 수동 재동기화할 수 있게 별도로 열어
    둔다.

    조회 자체가 실패하거나(네트워크 등) 응답 파싱이 불가능하면(`get_account_
    holdings_qty`가 `ValueError` — kt00018 개별 행 필드명이 아직 실측 미확정,
    그 메서드 docstring 참고) 그 시도는 불일치로 취급하고 재시도를 계속한다
    (재시도가 소진되면 최종적으로 판정 불가로 보수적으로 False를 반환) —
    "확인 안 되면 이전 상태 유지"가 항상 더 안전한 기본값이다(모듈 상단
    안전 원칙, 이전 버전과 동일한 태도)."""
    for attempt, delay in enumerate((0.0, *_CONFIRM_HOLDINGS_RETRY_DELAYS_SEC)):
        if delay:
            await asyncio.sleep(delay)
        try:
            actual_qty = await client.get_account_holdings_qty(code)
        except Exception:  # noqa: BLE001 - 조회 실패 -> 이번 시도는 불일치로 취급하고 재시도
            continue
        if actual_qty == expected_qty:
            return True
    return False


async def _read_holdings_qty(client: KiwoomClient, code: str) -> int | None:
    """실보유수량을 1회 읽는다. 조회 실패/파싱 불가는 None(Phase 5.122 부분 체결 판정용)."""
    try:
        return int(await client.get_account_holdings_qty(code))
    except Exception as e:  # noqa: BLE001 - 조회 실패는 판정 불가(None)로 취급
        logger.warning("auto-trade: 실보유수량 조회 실패: %s", e)
        return None


async def _read_holdings_qty_with_retry(client: KiwoomClient, code: str, expected_qty: int) -> int | None:
    """`_confirm_holdings_qty`와 같은 재시도 간격으로 읽되, expected와 일치하면 즉시 반환하고
    끝까지 불일치면 **마지막으로 성공적으로 읽은 실보유수량**(한 번도 못 읽으면 None)을 반환한다."""
    last: int | None = None
    for delay in (0.0, *_CONFIRM_HOLDINGS_RETRY_DELAYS_SEC):
        if delay:
            await asyncio.sleep(delay)
        try:
            last = int(await client.get_account_holdings_qty(code))
        except Exception:  # noqa: BLE001 - 이번 시도는 건너뛰고 재시도
            continue
        if last == expected_qty:
            return last
    return last


async def _settle_buy_fill(
    client: KiwoomClient, code: str, order_no: str | None, order_qty: int, confirmed: bool
) -> tuple[str, int]:
    """Phase 5.122 — 매수 주문 후 체결 결과 정산. `confirmed`는 `_confirm_holdings_qty`(전량 확인) 결과.

    반환 (kind, qty): ("full", 주문수량) / ("partial", 실보유) — 잔량은 취소하고 재조회로 확정 /
    ("unconfirmed", 0) — 전량 취소(기존 동작: idle 유지, 다음 폴링 재시도)."""
    if confirmed:
        return "full", order_qty
    actual = await _read_holdings_qty(client, code)
    kind = classify_buy_fill(order_qty, actual)
    if kind == "partial":
        await _cancel_unfilled_order_silently(client, code, order_no, order_qty - int(actual))
        again = await _read_holdings_qty(client, code)  # 취소 직전 추가 체결 가능성 -> 1회 재확인
        if again is not None and 0 < again <= order_qty:
            actual = again
        kind = classify_buy_fill(order_qty, actual)
        return kind, int(actual)
    await _cancel_unfilled_order_silently(client, code, order_no, order_qty)
    # 조회~취소 사이에 체결됐을 수 있다 — 취소 후 1회 재확인해, 실보유가 있으면 그 수량으로
    # 보유 전환한다(놓치면 실보유 주식이 손절 감시 밖에 남는다).
    again = await _read_holdings_qty(client, code)
    if again is not None and 0 < again <= order_qty:
        return classify_buy_fill(order_qty, again), int(again)
    return "unconfirmed", 0


async def _settle_sell_fill(
    client: KiwoomClient, code: str, order_no: str | None, held_qty: int, confirmed: bool
) -> tuple[str, int]:
    """Phase 5.122 — 매도 주문 후 체결 결과 정산. `confirmed`는 `_confirm_holdings_qty(…, 0)` 결과.

    반환 (kind, 잔량): ("full", 0) / ("partial", 잔량) — 미체결 잔량 취소 후 재조회 /
    ("unconfirmed", held_qty) — 전량 취소, 포지션 유지(기존 동작)."""
    if confirmed:
        return "full", 0
    remaining = await _read_holdings_qty(client, code)
    kind = classify_sell_fill(held_qty, remaining)
    if kind == "partial":
        await _cancel_unfilled_order_silently(client, code, order_no, int(remaining))
        again = await _read_holdings_qty(client, code)
        if again is not None and 0 <= again <= held_qty:
            remaining = again
        kind = classify_sell_fill(held_qty, remaining)
        return kind, int(remaining)
    await _cancel_unfilled_order_silently(client, code, order_no, held_qty)
    # 조회~취소 사이에 체결됐을 수 있다 — 취소 후 1회 재확인해 실제 잔량을 반영한다.
    again = await _read_holdings_qty(client, code)
    if again is not None and 0 <= again < held_qty:
        return classify_sell_fill(held_qty, again), int(again)
    return "unconfirmed", held_qty


async def _cancel_unfilled_order_silently(
    client: KiwoomClient, code: str, order_no: str | None, qty: int
) -> None:
    """미체결로 확인된 주문을 최선을 다해 취소한다(`cancel_order`, kt10003,
    이미 실호출 확정) — 실패해도 무시한다(다음 폴링이 새 호가로 다시 시도
    하면 되므로 치명적이지 않다). 자금/수량 예약을 풀어 다음 시도를 막지
    않게 하는 목적. `order_no`가 없으면 아무것도 하지 않는다."""
    if not order_no:
        return
    try:
        await client.cancel_order(code, order_no, qty)
    except Exception as e:  # noqa: BLE001 - 취소 실패는 무시(다음 폴링이 그래도 재시도)
        logger.warning("auto-trade: 미체결 주문 취소 시도 실패(무시하고 계속): %s", e)


def _clear_pending_sell(state: AutoTradeState) -> None:
    """동시호가 대기 매도 필드를 전부 비운다(정산이 끝난 뒤 호출)."""
    state.pending_sell_order_no = None
    state.pending_sell_qty = None
    state.pending_sell_event = None
    state.pending_sell_reason = None
    state.pending_sell_price = None
    state.pending_sell_at = None


def _reset_to_idle(state: AutoTradeState) -> None:
    """청산 완료 시 포지션 필드를 전부 초기화한다(기존 청산 경로와 동일한 필드 집합)."""
    state.status = "idle"
    state.entry_price = None
    state.entry_qty = None
    state.entry_at = None
    state.entry_order_no = None
    state.peak_price = None
    state.entry_foreign_flow_sign = None
    state.entry_source = None
    state.manual_exit_rules = None


async def _place_auction_sell(
    session: AsyncSession,
    state: AutoTradeState,
    code: str,
    ref_price: float | None,
    event_type: str,
    reason: str,
    signal_snapshot: dict,
    result: dict,
    bid: float | None = None,
) -> dict:
    """Phase 5.134 — 동시호가 구간 매도: 매수1호가 -2%(호가단위 내림) 지정가로 주문하고
    체결 확인·취소 없이 주문을 유지한다(`pending_sell_*` 저장 + `sell_pending_auction` 기록).
    `bid`가 없으면 여기서 조회하고, 동시호가 중 호가가 비어 있으면 `ref_price`(판정 현재가)를
    기준가로 쓴다. 주문 자체가 실패하면 기존 실패 처리(error 로그, sell_failed)와 동일하다."""
    try:
        async with KiwoomClient() as client:
            if bid is None:
                bid = await _best_fill_price(client, code, "sell")
            if bid is None and ref_price:
                bid = ref_price
            if bid is None:
                raise RuntimeError("매수호가를 확인할 수 없어 주문가를 정할 수 없음")
            order_price = auction_sell_price(bid)
            held_qty = int(state.entry_qty or TARGET_QTY)
            order_response = await client.place_sell_order(code, held_qty, order_price)
            order_no = str(order_response.get("ord_no")) if order_response.get("ord_no") else None
    except Exception as e:  # noqa: BLE001 - 주문 실패는 재시도 루프에 빠지지 않고 로그만
        logger.warning("auto-trade: 동시호가 매도 주문 실패(%s): %s", event_type, e)
        await _log(
            session,
            event_type="error",
            code=code,
            price=ref_price,
            reason=f"매도 주문 시도 실패({event_type}): {reason} / 오류: {e}",
            signal_snapshot=signal_snapshot,
        )
        result["action"] = "sell_failed"
        result["error"] = str(e)[:300]
        return result

    state.pending_sell_order_no = order_no
    state.pending_sell_qty = held_qty
    state.pending_sell_event = event_type
    state.pending_sell_reason = reason
    state.pending_sell_price = order_price
    state.pending_sell_at = dt.datetime.now(dt.timezone.utc)
    await session.commit()

    await _log(
        session,
        event_type="sell_pending_auction",
        code=code,
        price=order_price,
        reason=reason + f" -> 동시호가 구간이라 매도 주문 유지(주문번호 {order_no}, 지정가 {order_price}원, "
        f"{held_qty}주, 매수1호가 {bid} 대비 -2%) — 단일가 체결 후 확인",
        signal_snapshot={**signal_snapshot, "qty": held_qty, "pending_event": event_type},
        order_response=order_response,
    )
    result["action"] = "sell_pending"
    return result


async def _settle_pending_sell(
    session: AsyncSession, state: AutoTradeState, now_kst: dt.time, result: dict
) -> bool:
    """Phase 5.134 — 동시호가 대기 매도를 정산한다(`_position_lock` 보유 중 호출). 대기가 없으면
    False. 대기가 있으면 True(호출부는 이번 폴링에서 다른 매매 판단을 하지 않고 반환한다).
    - 아직 동시호가 구간 안: 조용히 대기 유지(새 주문·취소·로그 없음)
    - 구간 종료 후(또는 전날 대기분): 실보유로 정산 — 0주=원래 이벤트로 기록·idle / 일부=잔량 갱신·
      잔여 취소 / 그대로=취소·sell_unconfirmed·포지션 유지 / 조회 불가=대기 유지(추측하지 않음)"""
    if state.pending_sell_at is None:
        return False
    result["action"] = "sell_pending"
    code = state.code or TARGET_CODE
    placed_date = state.pending_sell_at.astimezone(KST).date()
    if is_auction_settle_wait(now_kst) and placed_date >= dt.datetime.now(KST).date():
        return True  # 단일가 체결 전이거나 체결 직후 잔고 반영 유예(1분) 중 — 아무것도 하지 않는다

    order_no = state.pending_sell_order_no
    qty = int(state.pending_sell_qty or state.entry_qty or TARGET_QTY)
    order_price = float(state.pending_sell_price) if state.pending_sell_price is not None else None
    event_type = state.pending_sell_event or "exit_manual"
    base_reason = state.pending_sell_reason or ""
    snapshot = {"pending_order_no": order_no, "qty": qty, "settled_at_kst": str(now_kst)}
    try:
        async with KiwoomClient() as client:
            actual = await _read_holdings_qty_with_retry(client, code, 0)
            if actual is None:
                logger.warning("auto-trade: 동시호가 대기 매도 정산 보류 — 실보유 조회 불가(다음 폴링 재시도)")
                return True
            if state.status not in ("holding", "trailing"):
                # 사용자가 이미 상태를 정정한 경우 — 남은 주문만 정리하고 대기 해제
                await _cancel_unfilled_order_silently(client, code, order_no, qty)
                _clear_pending_sell(state)
                await session.commit()
                return True
            fill_kind, remaining_qty = await _settle_sell_fill(client, code, order_no, qty, actual == 0)
    except Exception as e:  # noqa: BLE001 - 정산 실패는 대기 유지(다음 폴링 재시도)
        logger.warning("auto-trade: 동시호가 대기 매도 정산 실패(다음 폴링 재시도): %s", e)
        return True

    if fill_kind == "full":
        _reset_to_idle(state)
        _clear_pending_sell(state)
        await session.commit()
        await _log(
            session,
            event_type=event_type,
            code=code,
            price=order_price,
            reason=base_reason + f" -> 동시호가 매도 체결 확인(주문번호 {order_no}, 지정가 {order_price}원, {qty}주, "
            "실보유 0 — 실제 체결가는 단일가)",
            signal_snapshot=snapshot,
        )
        result["action"] = event_type
        return True

    if fill_kind == "partial":
        state.entry_qty = remaining_qty
        _clear_pending_sell(state)
        await session.commit()
        await _log(
            session,
            event_type="sell_unconfirmed",
            code=code,
            price=order_price,
            reason=base_reason + f" -> 동시호가 매도 {qty}주 중 {qty - remaining_qty}주만 체결 — 부분 체결, "
            f"잔량 {remaining_qty}주 유지(미체결분 취소)",
            signal_snapshot={**snapshot, "qty": qty - remaining_qty, "remaining_qty": remaining_qty},
        )
        result["action"] = "sell_partial"
        return True

    _clear_pending_sell(state)
    await session.commit()
    await _log(
        session,
        event_type="sell_unconfirmed",
        code=code,
        price=order_price,
        reason=base_reason + f" -> 동시호가 매도 주문(주문번호 {order_no}, 지정가 {order_price}원)이 단일가 "
        "체결 후에도 체결되지 않음(취소 시도) — 포지션 상태 유지, 다음 폴링에서 재시도",
        signal_snapshot=snapshot,
    )
    result["action"] = "sell_unconfirmed"
    return True


async def _get_risk_alert_active(session: AsyncSession) -> bool:
    """PLAN.md §5.55-3 — `index-tiles/live`의 `risk.alerts`가 하나라도 있으면
    True. `_warm_index_tiles_live`는 이 파일이 이미 새 TR 호출 없이 재사용하는
    캐시된 warm 함수(60초 TTL)라, 매 폴링 호출해도 대부분 캐시 히트다. 조회
    자체가 실패하면(DB 오류 등) **위험 없음(False)으로 폴백**한다(fail-open) —
    이 배너는 "지금 당장의 위험"을 알리는 보조 신호이고, 이 값이 틀려도 절대
    손절(-1.5%, `STOP_LOSS_PCT`)은 항상 살아있어 최소 안전망은 유지되기
    때문이다. 장 마감이면 `risk`가 `None`이라(`_warm_index_tiles_live` 참고)
    자연히 False가 된다."""
    try:
        index_tiles = await _warm_index_tiles_live(session)
    except Exception as e:  # noqa: BLE001 - 보조 신호 조회 실패는 위험 없음으로 폴백
        logger.warning("auto-trade: 리스크 경보 조회 실패, 위험 없음으로 폴백: %s", e)
        return False
    risk = index_tiles.get("risk")
    return bool(risk and risk.get("alerts"))


# Phase 5.121 — 가장 최근 run_auto_trade 평가 결과(프로세스 메모리, 조회 전용).
# 재시작 직후엔 None. 킬스위치 OFF일 땐 갱신하지 않는다(화면은 '정지' 표시).
LAST_EVAL: dict | None = None

_LAST_EVAL_BLOCK_ACTIONS = (
    "entry_blocked_time", "entry_blocked_risk", "budget_blocked", "stale_data", "no_bars", "error",
)


def build_last_eval(result: dict, at: dt.datetime) -> dict | None:
    """run_auto_trade 결과 dict -> 화면용 last_eval. 꺼짐(enabled False)이면 None(갱신 안 함)."""
    if not result.get("enabled"):
        return None
    action = result.get("action")
    return {
        "at": at.astimezone(KST).isoformat(),
        "action": action,
        "status": result.get("status"),
        "golden_recent": result.get("golden_recent"),
        "spike_recent": result.get("spike_recent"),
        "current_price": result.get("current_price"),
        "block_reason": action if action in _LAST_EVAL_BLOCK_ACTIONS else None,
    }


def record_last_eval(result: dict, at: dt.datetime | None = None) -> None:
    """평가 결과를 LAST_EVAL에 보관한다(enabled일 때만)."""
    global LAST_EVAL
    built = build_last_eval(result, at or dt.datetime.now(KST))
    if built is not None:
        LAST_EVAL = built


async def run_auto_trade(
    session: AsyncSession, now_kst: dt.time | None = None, today_str: str | None = None
) -> dict:
    """PLAN.md §5.54 상태 기계를 1회 평가·실행한다. PLAN.md §5.55(2026-08-06,
    실제 손실 사고 이후 안전 규칙) 4개가 이 함수와 `_handle_idle`/
    `_handle_position`에 배선돼 있다 — 모듈 상단 "안전 규칙 추가" 절 참고.

    `now_kst`(선택, 기본 None)를 넘기면 §5.55-1/§5.55-2의 시간 판정에 실제
    시계 대신 그 값을 쓴다 — `collectors/live_refresh.py`의 실제 스케줄러
    호출부는 이 인자를 넘기지 않아(기본 동작 그대로 실제 시계) 동작이
    바뀌지 않는다. 테스트가 15:20~15:30 KST 같은 특정 시각을 재현하려고
    실제 벽시계를 기다리지 않게 하기 위한 주입 지점이다(house rule — "시간
    관련 테스트는 now_kst를 파라미터로 주입 가능하게 설계", 하드코딩된
    `dt.datetime.now()`를 함수 내부 깊은 곳에 두지 않는다). `today_str`
    (선택, 기본 None, "YYYYMMDD")도 동일한 이유로 주입 가능하게 뒀다 — 아래
    "오늘 데이터 아니면 스킵" 가드용, 기본값은 실제 KST 오늘 날짜.

    Returns 요약 dict(로깅용, `scalp_tracker.track_scalp_picks`/
    `positioning_snapshot.track_positioning_snapshot`의 반환 관례 참고) —
    ``{"enabled": bool, "action": str, ...}``. ``action``은
    "none"(꺼짐/조건 미충족/신고가만 조용히 갱신)|"stale_data"(오늘 거래
    데이터 없음, 공휴일 등)|"enter"|"trail_activate"|"exit_stop_loss"|
    "exit_trail"|"exit_eod_forced"|"exit_flow_reversal"|"entry_blocked_time"|
    "entry_blocked_risk"|"budget_blocked"|"buy_failed"|"buy_unconfirmed"|
    "sell_failed"|"sell_unconfirmed"|"error" 중 하나(뒤 둘은 PLAN.md §5.71 —
    주문 접수는 됐지만 체결 미확인, 모듈 상단 "미체결 확인" 절 참고).
    """
    result: dict = {"enabled": False, "action": "none"}

    # PLAN.md §5.54-6 — `watch_stop_loss`(30초 잡)와 동일한 락을 잡는다. 상태를
    # 읽는 시점부터 잡아야 두 잡이 동시에 "지금 보유 중"을 보고 동시에 매도를
    # 시도하는 걸 막을 수 있다(모듈 상단 `_position_lock` 주석 참고).
    async with _position_lock:
        state = await _get_state(session)
        # Phase 5.134 — 동시호가에 낸 대기 매도가 있으면 킬스위치와 무관하게(이미 나간 실주문의
        # 정산이므로) 정산/대기만 하고 이번 폴링의 다른 매매 판단은 하지 않는다.
        if state is not None and state.pending_sell_at is not None:
            result["enabled"] = bool(state.enabled)
            result["status"] = state.status
            settle_now = now_kst if now_kst is not None else dt.datetime.now(KST).time()
            await _settle_pending_sell(session, state, settle_now, result)
            return result
        if state is None or not state.enabled:
            # 킬스위치 꺼짐(또는 시드 행이 없는 비정상 상태) — 신호 평가조차 하지
            # 않고 즉시 반환. 로그도 남기지 않는다(노이즈 방지, 모듈 docstring 참고).
            return result

        result["enabled"] = True
        result["status"] = state.status  # Phase 5.121 — 조회용 노출(판정과 무관)
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

        # 2026-08-18(리소스 점검 중 발견) — 공휴일 등 실제로 오늘 거래 데이터가
        # 없는 날에도 이 함수 자체는 계속 평가를 돌았다. `_warm_stock_intraday`가
        # 부르는 ka10080은 공휴일에 조회해도 마지막 실제 거래일 데이터를 그대로
        # 주므로(`parse_minute_chart_rows`가 "가장 최근 날짜"만 골라 반환,
        # `clients/kiwoom.py` 참고) golden cross/volume spike가 그 정지된
        # 데이터에서 계속 "최근"으로 재평가돼, 조건이 맞으면 매 폴링(60초)마다
        # 실제 매수 주문을 제출했다 — 키움 서버가 매번 거부했지만(장이 열리지
        # 않는 날), 그 실패가 매번 AutoTradeLog에 error로 쌓여 하루 288건까지
        # 노이즈가 생겼다(2026-08-17 실측). 새 홀리데이 달력 없이, 이미 매
        # 폴링 가져오는 1분봉의 날짜가 "오늘"과 다르면 킬스위치 꺼짐과 동일하게
        # 신호 평가/주문 시도 자체를 하지 않는다 — 로그도 남기지 않는다(노이즈
        # 방지 원칙, 모듈 docstring 참고).
        effective_today_str = today_str if today_str is not None else dt.datetime.now(KST).strftime("%Y%m%d")
        if bars[-1].get("date") != effective_today_str:
            result["action"] = "stale_data"
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
        # Phase 5.121 — 매매 탭 '엔진 감시 현황' 표시용(조회 전용, 판정·분기에 쓰지 않는다).
        result["golden_recent"] = bool(golden_recent)
        result["spike_recent"] = bool(spike_recent)
        result["current_price"] = current_price

        # PLAN.md §5.55-1/3 — 진입 시간 필터(idle 경로)와 리스크 경보 연동(idle
        # 진입 차단 + holding/trailing 손절선 조정) 둘 다 "지금 몇 시인지"/
        # "리스크 경보가 활성인지"가 필요하다. 상태(idle/holding/trailing)와
        # 무관하게 매 폴링 한 번만 구한다. `now_kst` 인자가 주입됐으면(테스트)
        # 그 값을 그대로 쓰고, 아니면(실제 스케줄러 호출) 실제 시계를 읽는다.
        effective_now_kst = now_kst if now_kst is not None else dt.datetime.now(KST).time()
        risk_alert_active = await _get_risk_alert_active(session)

        if state.status == "idle":
            return await _handle_idle(
                session,
                state,
                code,
                current_price,
                golden_recent,
                spike_recent,
                signal_snapshot,
                result,
                effective_now_kst,
                risk_alert_active,
            )

        return await _handle_position(
            session, state, code, current_price, signal_snapshot, result, effective_now_kst, risk_alert_active
        )


async def _handle_idle(
    session: AsyncSession,
    state: AutoTradeState,
    code: str,
    current_price: float,
    golden_recent: bool,
    spike_recent: bool,
    signal_snapshot: dict,
    result: dict,
    now_kst: dt.time,
    risk_alert_active: bool,
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

    # PLAN.md §5.55-1 — 개장 후 10분/마감 전 10분엔 신규 진입만 금지. 신호
    # 판정(decide_idle_action) 자체는 순수하게 그대로 두고, 실행 여부를
    # 가르는 추가 가드로 여기서 겹겹이 확인한다(`check_entry_budget`과 동일한
    # 위치·관례). 손절/트레일청산/강제청산 경로는 이 체크를 절대 거치지
    # 않으므로(모두 다른 함수) 항상 정상 작동한다.
    if is_entry_blocked_by_time(now_kst):
        reason = (
            f"{decision['reason']} — 그러나 진입 제한 시간대(now={now_kst.isoformat()}, "
            "09:00~09:10 또는 15:10~15:30 KST)라 진입 보류"
        )
        await _log(
            session, event_type="entry_blocked_time", code=code, price=current_price,
            reason=reason, signal_snapshot=signal_snapshot,
        )
        result["action"] = "entry_blocked_time"
        return result

    # PLAN.md §5.55-3 — 리스크 경보(서킷브레이커/사이드카/거래량급증/수급가속도)
    # 활성 중엔 신규 진입 금지.
    if risk_alert_active:
        reason = f"{decision['reason']} — 그러나 리스크 경보 활성 중이라 진입 보류"
        await _log(
            session, event_type="entry_blocked_risk", code=code, price=current_price,
            reason=reason, signal_snapshot=signal_snapshot,
        )
        result["action"] = "entry_blocked_risk"
        return result

    # Phase 5.122 — 수량 = floor(min(주문캡, 누적예산, 주문가능금액) / 매도1호가). 주문 시점에 산출.
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
            except Exception as e:  # noqa: BLE001 - 주문가능금액을 모르면 진입 보류(보수적)
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

    if order_qty < 1:
        await _log(
            session,
            event_type="error",
            code=code,
            price=order_price,
            reason=(
                f"{decision['reason']} — 그러나 주문 수량 0주라 진입 보류: 지정가 {order_price}원, "
                f"한도(주문캡 {MAX_ORDER_NOTIONAL_KRW}, 누적예산 {AUTO_TRADE_TOTAL_BUDGET_KRW}), "
                f"주문가능금액 {orderable_cash}원" + (f" / {block_note}" if block_note else "")
            ),
            signal_snapshot=signal_snapshot,
        )
        result["action"] = "budget_blocked"
        return result

    if fill_kind == "unconfirmed":
        # PLAN.md §5.78 — 주문은 접수됐지만 실보유수량으로 체결이 확인되지 않음(취소 시도함).
        # status를 holding으로 넘기지 않고 idle을 유지해 다음 폴링이 새 호가로 다시 시도한다.
        await _log(
            session,
            event_type="buy_unconfirmed",
            code=code,
            price=order_price,
            reason=(
                decision["reason"] + f" -> 매수 주문 제출(지정가 {order_price}원, {order_qty}주)했으나 "
                "체결 미확인(실보유수량 불일치, 취소 시도) — 진입 보류, 다음 폴링에서 재시도"
            ),
            signal_snapshot=signal_snapshot,
            order_response=order_response,
        )
        result["action"] = "buy_unconfirmed"
        return result

    partial_note = f" [부분 체결 {filled_qty}/{order_qty}주, 잔량 취소]" if fill_kind == "partial" else ""
    signal_snapshot = {**signal_snapshot, "qty": filled_qty}
    now = dt.datetime.now(dt.timezone.utc)
    state.status = "holding"
    state.entry_price = order_price
    state.entry_qty = filled_qty
    state.entry_at = now
    state.entry_order_no = str(order_response.get("ord_no")) if order_response.get("ord_no") else None
    state.peak_price = None

    # PLAN.md §5.55-4 — 진입 시점 외인 현물 누적 순매수 부호를 기록해 둔다
    # (이후 반전 감지용). 조회 실패해도 진입 자체는 이미 체결됐으니 막지
    # 않는다 — None으로 남으면 `evaluate_foreign_flow_reversal_exit`이
    # "판정 불가"로 처리해 조기청산을 시도하지 않는다(안전한 기본값).
    try:
        foreign_series = await intraday_snapshot.get_foreign_position_series(session, days=1)
        spot_points = foreign_series.get("spot") or []
        entry_flow_value = spot_points[-1]["value"] if spot_points else None
    except Exception as e:  # noqa: BLE001 - 조회 실패는 기록 생략, 진입 자체는 계속 진행
        logger.warning("auto-trade: 진입 시점 외인 현물 수급 조회 실패(반전 감지 기록 생략): %s", e)
        entry_flow_value = None
    state.entry_foreign_flow_sign = foreign_flow_sign(entry_flow_value)

    await session.commit()

    await _log(
        session,
        event_type="entry",
        code=code,
        price=order_price,
        reason=(
            decision["reason"] + f" -> 매수 주문 제출(지정가 {order_price}원, {filled_qty}주){partial_note}, "
            f"진입 시점 외인 현물 수급 부호={state.entry_foreign_flow_sign!r}"
        ),
        signal_snapshot=signal_snapshot,
        order_response=order_response,
    )
    result["action"] = "entry"
    return result


async def _execute_sell(
    session: AsyncSession,
    state: AutoTradeState,
    code: str,
    current_price: float,
    event_type: str,
    reason: str,
    signal_snapshot: dict,
    result: dict,
    now_kst: dt.time | None = None,
) -> dict:
    """공통 매도 실행 — 손절/트레일청산/EOD강제청산(§5.55-2)/수급반전조기청산
    (§5.55-4)이 전부 이 로직(호가 조회 -> 매도 주문 -> 상태 초기화 -> 로그)을
    공유한다. **이 함수를 호출한다는 것 자체가 이미 "매도하기로 결정됨"을
    뜻한다** — 여기서는 더 이상 조건을 판단하지 않고 기계적으로 실행만 한다
    (모듈 상단 "매도는 항상 최우선/무조건 허용" 원칙).

    Phase 5.134 — 이미 동시호가 대기 매도가 있으면 새 주문 없이 반환하고, 지금이 동시호가
    구간이면 주문만 내고 유지한다(`_place_auction_sell`, 모듈 상단 "동시호가 구간 매도" 절).
    `now_kst`는 테스트 주입용(없으면 실제 시계)."""
    if state.pending_sell_at is not None:
        result["action"] = "sell_pending"
        return result
    effective_now = now_kst if now_kst is not None else dt.datetime.now(KST).time()
    if is_auction_window(effective_now):
        return await _place_auction_sell(
            session, state, code, current_price, event_type, reason, signal_snapshot, result
        )
    try:
        async with KiwoomClient() as client:
            order_price = await _best_fill_price(client, code, "sell")
            if order_price is None:
                raise RuntimeError("매수호가를 확인할 수 없어 주문가를 정할 수 없음")
            held_qty = int(state.entry_qty or TARGET_QTY)
            order_response = await client.place_sell_order(code, held_qty, int(order_price))
            order_no = str(order_response.get("ord_no")) if order_response.get("ord_no") else None
            confirmed = await _confirm_holdings_qty(client, code, 0)
            # Phase 5.122 — 부분 체결이면 잔량 취소 후 잔여 수량 확정, 아니면 기존 동작.
            fill_kind, remaining_qty = await _settle_sell_fill(client, code, order_no, held_qty, confirmed)
    except Exception as e:  # noqa: BLE001 - 주문 실패는 재시도 루프에 빠지지 않고 로그만
        logger.warning("auto-trade: 매도 주문 실패(%s): %s", event_type, e)
        await _log(
            session,
            event_type="error",
            code=code,
            price=current_price,
            reason=f"매도 주문 시도 실패({event_type}): {reason} / 오류: {e}",
            signal_snapshot=signal_snapshot,
        )
        result["action"] = "sell_failed"
        result["error"] = str(e)[:300]
        return result

    if fill_kind == "partial":
        # Phase 5.122 — 일부만 체결: 실보유 잔량으로 entry_qty를 줄이고 status는 유지해 다음
        # 폴링이 잔량을 다시 매도한다(수량 초과 거부 반복 방지).
        state.entry_qty = remaining_qty
        await session.commit()
        await _log(
            session,
            event_type="sell_unconfirmed",
            code=code,
            price=order_price,
            reason=reason + f" -> 매도 주문 제출(지정가 {order_price}원, {held_qty}주) 중 "
            f"{held_qty - remaining_qty}주만 체결 — 부분 체결, 잔량 {remaining_qty}주 유지(미체결분 취소)",
            signal_snapshot={**signal_snapshot, "qty": held_qty - remaining_qty, "remaining_qty": remaining_qty},
            order_response=order_response,
        )
        result["action"] = "sell_partial"
        return result

    if fill_kind != "full":
        # PLAN.md §5.78 — 8/14 실사고(§5.71)에 더해 8/26 실사고(모듈 상단
        # "미체결 확인" 절 참고): 주문은 접수됐지만 실보유수량으로 체결이
        # 확인되지 않음(취소 시도함). status를 idle로 넘기지 않고
        # holding/trailing 그대로 유지해 손절 감시가 계속 이 포지션을 보게
        # 한다 — 다음 폴링이 새 호가로 다시 매도를 시도한다.
        await _log(
            session,
            event_type="sell_unconfirmed",
            code=code,
            price=order_price,
            reason=reason + f" -> 매도 주문 제출(지정가 {order_price}원)했으나 "
            "체결 미확인(실보유수량 불일치, 취소 시도) — 포지션 상태 유지, 다음 폴링에서 재시도",
            signal_snapshot=signal_snapshot,
            order_response=order_response,
        )
        result["action"] = "sell_unconfirmed"
        return result

    state.status = "idle"
    state.entry_price = None
    state.entry_qty = None
    state.entry_at = None
    state.entry_order_no = None
    state.peak_price = None
    state.entry_foreign_flow_sign = None
    state.entry_source = None  # Phase 5.118 — 수동 진입 표식·조건도 함께 리셋
    state.manual_exit_rules = None
    await session.commit()

    await _log(
        session,
        event_type=event_type,
        code=code,
        price=order_price,
        reason=reason + f" -> 매도 주문 제출(지정가 {order_price}원, {held_qty}주)",
        signal_snapshot={**signal_snapshot, "qty": held_qty},
        order_response=order_response,
    )
    # `result["action"]`은 여기서 건드리지 않는다 — 손절/트레일청산 호출부
    # (`_handle_position`)는 이미 `decision["action"]`("stop_loss"/"exit_trail",
    # `event_type`과 다른 문자열일 수 있음 — 기존 관례 그대로 유지)을 미리
    # 넣어 뒀고, EOD강제청산/수급반전 호출부(`_check_forced_exits`)는 각자
    # 호출 전에 명시적으로 넣는다. 실패(sell_failed)만 위에서 이미 덮어썼다.
    return result


async def _check_forced_exits(
    session: AsyncSession,
    state: AutoTradeState,
    code: str,
    current_price: float,
    signal_snapshot: dict,
    result: dict,
    now_kst: dt.time,
    risk_alert_active: bool,
) -> dict:
    """PLAN.md §5.55-2/§5.55-4 — `decide_position_action`이 "none"/
    "peak_update"/"trail_activate"를 반환해 매도로 이어지지 않은 뒤에도, 이
    두 가지 강제청산 조건(가격과 무관)을 추가로 확인한다. `_handle_position`이
    stop_loss/exit_trail일 때는 이 함수를 아예 호출하지 않는다(모듈 상단
    "매도는 항상 최우선" 원칙 — 이미 손절/트레일청산이 나갔으면 사족 없이
    바로 나간다).

    확인 순서: 수급 방향 반전(§5.55-4, 시간 무관 — DB 조회뿐이라 매 폴링
    확인해도 가볍다) -> EOD 강제청산(§5.55-2, 15:10~15:30 KST에서만 확인 —
    regime 조회가 상대적으로 무겁고 이 시간대 밖에서는 애초에 의미가 없다).
    """
    entry_price = float(state.entry_price)

    # §5.55-4 — 수급 방향 전환 조기청산.
    try:
        foreign_series = await intraday_snapshot.get_foreign_position_series(session, days=1)
        spot_points = foreign_series.get("spot") or []
        current_flow_value = spot_points[-1]["value"] if spot_points else None
    except Exception as e:  # noqa: BLE001 - 조회 실패는 이번 폴링만 건너뜀(다음 폴링 재시도)
        logger.warning("auto-trade: 외인 현물 수급 조회 실패, 반전 조기청산 체크 건너뜀: %s", e)
        current_flow_value = None
    current_flow_sign = foreign_flow_sign(current_flow_value)

    flow_decision = evaluate_foreign_flow_reversal_exit(state.entry_foreign_flow_sign, current_flow_sign)
    if flow_decision["should_exit"]:
        result["action"] = "exit_flow_reversal"
        return await _execute_sell(
            session, state, code, current_price, "exit_flow_reversal", flow_decision["reason"],
            signal_snapshot, result, now_kst,
        )

    # §5.55-2 — 장마감 전 조건부 오버나잇 청산. 15:10~15:30 KST 밖이면 리스크
    # 경보/regime 조회 없이 즉시 반환(`evaluate_eod_forced_exit`도 시간 자체를
    # 다시 확인하지만, 여기서 먼저 걸러야 이 시간대 밖에서 불필요한 regime
    # 조회를 하지 않는다).
    if not (EOD_FORCED_EXIT_START <= now_kst <= EOD_FORCED_EXIT_END):
        return result

    try:
        regime = await _warm_regime(session)
        kosdaq_foreign_streak_ok = regime["kosdaq"]["외국인"]["confirmed_streak"] >= 0
    except Exception as e:  # noqa: BLE001 - "확인 못 함"을 안전 측(청산)으로 폴백
        logger.warning("auto-trade: EOD 강제청산 판정용 regime 조회 실패, 안전 측(청산)으로 폴백: %s", e)
        kosdaq_foreign_streak_ok = False

    unrealized_pnl_positive = current_price > entry_price

    eod_decision = evaluate_eod_forced_exit(
        now_kst=now_kst,
        status=state.status,
        unrealized_pnl_positive=unrealized_pnl_positive,
        kosdaq_foreign_streak_ok=kosdaq_foreign_streak_ok,
        risk_alert_active=risk_alert_active,
    )
    if eod_decision["should_exit"]:
        result["action"] = "exit_eod_forced"
        return await _execute_sell(
            session, state, code, current_price, "exit_eod_forced", eod_decision["reason"],
            signal_snapshot, result, now_kst,
        )
    return result


def _manual_rules_of(state: AutoTradeState) -> list[dict] | None:
    """수동 진입 포지션이면 사용자 조건 목록을, 아니면 None(= 엔진 고정 규칙 경로).
    표식이 manual인데 조건 JSON이 깨졌으면 안전 측으로 None(고정 손절 규칙 적용)을 돌려준다."""
    if getattr(state, "entry_source", None) != "manual":
        return None
    try:
        data = json.loads(state.manual_exit_rules or "")
        rules = data["rules"]
        if not isinstance(rules, list):
            raise ValueError("rules가 목록이 아님")
        return rules
    except Exception as e:  # noqa: BLE001 - 깨진 조건은 고정 규칙으로 폴백(손절 없는 포지션 방지)
        logger.warning("auto-trade: 수동 청산 조건 JSON 해석 실패, 엔진 고정 규칙으로 폴백: %s", e)
        return None


async def _handle_manual_position(
    session: AsyncSession,
    state: AutoTradeState,
    code: str,
    current_price: float,
    signal_snapshot: dict,
    result: dict,
    now_kst: dt.time,
    risk_alert_active: bool,
    rules: list[dict],
) -> dict:
    """Phase 5.118 — 수동 진입 포지션(60초 경로). 사용자 조건에 닿으면 `_execute_sell`로 매도하고,
    아니면 고점만 갱신한 뒤 장마감 강제청산·외인 수급 반전(`_check_forced_exits`)은 그대로 적용한다.
    기본 손절(-1.5%)·위험경보 손절(-0.8%)은 적용하지 않는다(사용자 조건이 대신함)."""
    decision = decide_manual_exit(
        rules,
        float(state.entry_price),
        float(state.peak_price) if state.peak_price is not None else None,
        current_price,
    )
    result["action"] = decision["action"]
    if decision["action"] == "exit":
        result["action"] = "exit_manual_rule"
        return await _execute_sell(
            session, state, code, current_price, "exit_manual_rule",
            "수동 매수 사용자 조건 청산 — " + decision["reason"], signal_snapshot, result,
            now_kst,
        )
    if decision["action"] == "peak_update":
        state.peak_price = decision["new_peak_price"]
        await session.commit()
    return await _check_forced_exits(
        session, state, code, current_price, signal_snapshot, result, now_kst, risk_alert_active
    )


async def _watch_manual_position(
    session: AsyncSession,
    state: AutoTradeState,
    code: str,
    entry_price: float,
    rules: list[dict],
    result: dict,
    now_kst: dt.time | None = None,
) -> dict:
    """Phase 5.118 — 수동 진입 포지션의 30초 실시간 호가 감시(`watch_stop_loss`가 호출, 락 보유 중).
    실시간 매수1호가로 사용자 조건을 판정하고 닿으면 `_execute_sell`로 매도한다. 강제청산·외인반전은
    이 30초 잡 범위 밖이라 60초 경로가 맡는다(자동 진입과 동일 분업)."""
    try:
        async with KiwoomClient() as client:
            live_price = await _best_fill_price(client, code, "sell")
    except Exception as e:  # noqa: BLE001 - 호가 조회 실패는 다음 폴링에서 재시도
        logger.warning("auto-trade(fast watch, manual): 호가 조회 실패, 이번 폴링 건너뜀: %s", e)
        result["action"] = "error"
        result["error"] = str(e)[:300]
        return result
    if live_price is None:
        return result

    decision = decide_manual_exit(
        rules, entry_price, float(state.peak_price) if state.peak_price is not None else None, live_price
    )
    if decision["action"] == "exit":
        pct = round((live_price - entry_price) / entry_price * 100, 4) if entry_price else None
        snapshot = {
            "live_price": live_price,
            "entry_price": entry_price,
            "pct": pct,
            "source": "realtime_quote_fast_watch_manual",
            "rules": rules,
        }
        result["action"] = "exit_manual_rule"
        return await _execute_sell(
            session, state, code, live_price, "exit_manual_rule",
            "[실시간 호가 감시(30초)] 수동 매수 사용자 조건 청산 — " + decision["reason"], snapshot, result,
            now_kst,
        )
    if decision["action"] == "peak_update":
        state.peak_price = decision["new_peak_price"]
        await session.commit()
    return result


async def _handle_position(
    session: AsyncSession,
    state: AutoTradeState,
    code: str,
    current_price: float,
    signal_snapshot: dict,
    result: dict,
    now_kst: dt.time,
    risk_alert_active: bool,
) -> dict:
    # Phase 5.118 — 수동 매수로 진입한 포지션은 사용자가 고른 조건으로 청산 판정한다
    # (고정 손절/트레일 대신). 자동 진입(entry_source가 None/'auto')은 아래 기존 경로 그대로.
    manual_rules = _manual_rules_of(state)
    if manual_rules is not None:
        return await _handle_manual_position(
            session, state, code, current_price, signal_snapshot, result, now_kst, risk_alert_active,
            manual_rules,
        )

    # PLAN.md §5.83(2026-09-09) — 트레일 청산이 ratchet 방식(고점 대비 반납폭)
    # 으로 바뀌면서 ma_cross가 더 이상 필요 없다(quant/auto_trade_rules.py
    # 모듈 docstring 참고) — 이 호출에서 ma_cross를 넘기지 않는다. 호출부
    # (아래 run_auto_trade)는 여전히 signal_snapshot 로깅용으로 계산해 둔다.
    decision = decide_position_action(
        state.status,
        float(state.entry_price),
        float(state.peak_price) if state.peak_price is not None else None,
        current_price,
        risk_alert_active=risk_alert_active,
    )
    result["action"] = decision["action"]

    if decision["action"] == "none":
        return await _check_forced_exits(
            session, state, code, current_price, signal_snapshot, result, now_kst, risk_alert_active
        )

    if decision["action"] == "peak_update":
        # trailing 중 신고가만 조용히 갱신 — 로그하지 않는다(모듈 docstring 원칙 3).
        state.peak_price = decision["new_peak_price"]
        await session.commit()
        return await _check_forced_exits(
            session, state, code, current_price, signal_snapshot, result, now_kst, risk_alert_active
        )

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
        return await _check_forced_exits(
            session, state, code, current_price, signal_snapshot, result, now_kst, risk_alert_active
        )

    # stop_loss 또는 exit_trail -> 매도. 손절은 항상 최우선(decide_position_action이
    # 이미 다른 판정보다 먼저 확인) — 여기서 §5.55의 새 강제청산 판정 없이
    # 바로 나간다(모듈 상단 "매도는 항상 최우선" 원칙, `_check_forced_exits`를
    # 아예 호출하지 않는다).
    event_type = "exit_stop_loss" if decision["action"] == "stop_loss" else "exit_trail"
    return await _execute_sell(
        session, state, code, current_price, event_type, decision["reason"], signal_snapshot, result, now_kst
    )


async def watch_stop_loss(session: AsyncSession, now_kst: dt.time | None = None) -> dict:
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

    **PLAN.md §5.55-3(2026-08-06 추가)**: 리스크 경보 활성 중엔 손절선을
    `STOP_LOSS_PCT`(-1.5%) 대신 `STOP_LOSS_PCT_RISK_ALERT`(-0.8%)로 타이트하게
    적용한다 — "장중 변동성 급등 대응"이 바로 이 30초 고빈도 감시의 존재
    이유와 정확히 일치하는 상황이라, 느린 60초 잡뿐 아니라 이 잡에도 같은
    조정을 반영한다. `_warm_index_tiles_live`는 60초 캐시라 이 잡이 매번
    호출해도 대부분 캐시 히트라 실질적으로 추가 지연이 없다. EOD 강제청산
    (§5.55-2)/수급 반전 조기청산(§5.55-4)은 이 잡에 넣지 않는다(모듈 상단
    "진입/트레일전환/트레일청산은 건드리지 않는다" 원칙 그대로 — regime/외인
    수급 조회가 이 30초 잡의 "딱 손절 판정에만" 범위를 벗어난다, `run_auto_
    trade`가 전담).

    Returns ``{"enabled": bool, "action": "none"|"exit_stop_loss"|
    "sell_unconfirmed"|"sell_failed"|"error"}`` — "sell_unconfirmed"는
    PLAN.md §5.71(주문 접수는 됐지만 체결 미확인, 모듈 상단 "미체결 확인"
    절 참고).
    """
    result: dict = {"enabled": False, "action": "none"}

    async with _position_lock:
        state = await _get_state(session)
        # Phase 5.134 — 동시호가 대기 매도 정산/대기(`run_auto_trade`와 동일, 새 매도 금지).
        if state is not None and state.pending_sell_at is not None:
            result["enabled"] = bool(state.enabled)
            settle_now = now_kst if now_kst is not None else dt.datetime.now(KST).time()
            await _settle_pending_sell(session, state, settle_now, result)
            return result
        if state is None or not state.enabled:
            return result
        result["enabled"] = True
        effective_now_kst = now_kst if now_kst is not None else dt.datetime.now(KST).time()

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

        # Phase 5.118 — 수동 진입 포지션은 고정 손절/리스크 경보 손절 대신 사용자 조건으로 감시.
        manual_rules = _manual_rules_of(state)
        if manual_rules is not None:
            return await _watch_manual_position(
                session, state, code, entry_price, manual_rules, result, effective_now_kst
            )

        # PLAN.md §5.55-3 — 리스크 경보 활성 중이면 손절선을 타이트하게(위
        # 모듈 docstring 참고). 조회 실패는 `_get_risk_alert_active` 내부에서
        # 이미 위험 없음(False)으로 폴백한다.
        risk_alert_active = await _get_risk_alert_active(session)
        stop_loss_threshold = STOP_LOSS_PCT_RISK_ALERT if risk_alert_active else STOP_LOSS_PCT

        try:
            async with KiwoomClient() as client:
                live_price = await _best_fill_price(client, code, "sell")
        except Exception as e:  # noqa: BLE001 - 호가 조회 실패는 다음 폴링에서 재시도
            logger.warning("auto-trade(fast watch): 호가 조회 실패, 이번 폴링 건너뜀: %s", e)
            result["action"] = "error"
            result["error"] = str(e)[:300]
            return result

        if live_price is None or not evaluate_stop_loss(entry_price, live_price, stop_loss_threshold):
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
            "risk_alert_active": risk_alert_active,
            "stop_loss_threshold": stop_loss_threshold,
        }

        if is_auction_window(effective_now_kst):
            # Phase 5.134 — 동시호가(장 시작 08:30~09:00 등): 주문을 유지하고 구간 종료 후 정산.
            auction_reason = (
                f"[실시간 호가 손절 감시(30초)] 손절 조건 충족: 실시간가 {live_price} vs "
                f"진입가 {entry_price} ({pct:+.2f}%) <= {stop_loss_threshold}%"
                + (" (리스크 경보 활성 -> 임시 손절선 적용)" if risk_alert_active else "")
            )
            result["action"] = "exit_stop_loss"
            return await _place_auction_sell(
                session, state, code, live_price, "exit_stop_loss", auction_reason, signal_snapshot, result,
                bid=live_price,
            )

        try:
            async with KiwoomClient() as client:
                held_qty = int(state.entry_qty or TARGET_QTY)
                order_response = await client.place_sell_order(code, held_qty, int(live_price))
                order_no = str(order_response.get("ord_no")) if order_response.get("ord_no") else None
                confirmed = await _confirm_holdings_qty(client, code, 0)
                # Phase 5.122 — 부분 체결 정산(잔량 취소 + 실보유 잔량 확정)
                fill_kind, remaining_qty = await _settle_sell_fill(client, code, order_no, held_qty, confirmed)
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

        if fill_kind == "partial":
            state.entry_qty = remaining_qty
            await session.commit()
            await _log(
                session,
                event_type="sell_unconfirmed",
                code=code,
                price=live_price,
                reason=(
                    f"[실시간 호가 손절 감시(30초)] 손절 조건 충족: 실시간가 {live_price} vs "
                    f"진입가 {entry_price} ({pct:+.2f}%) <= {stop_loss_threshold}% -> 매도 {held_qty}주 중 "
                    f"{held_qty - remaining_qty}주만 체결 — 부분 체결, 잔량 {remaining_qty}주 유지(미체결분 취소)"
                ),
                signal_snapshot={**signal_snapshot, "qty": held_qty - remaining_qty, "remaining_qty": remaining_qty},
                order_response=order_response,
            )
            result["action"] = "sell_partial"
            return result

        if fill_kind != "full":
            # PLAN.md §5.78(§5.71 후속) — `_execute_sell`과 동일한 이유(모듈
            # 상단 "미체결 확인" 절 참고). 이 30초 고빈도 잡은 `_execute_sell`
            # 을 안 쓰고 자체 구현이라 별도로 동일 패턴을 적용한다.
            await _log(
                session,
                event_type="sell_unconfirmed",
                code=code,
                price=live_price,
                reason=(
                    f"[실시간 호가 손절 감시(30초)] 손절 조건 충족: 실시간가 {live_price} vs "
                    f"진입가 {entry_price} ({pct:+.2f}%) <= {stop_loss_threshold}%"
                    + (" (리스크 경보 활성 -> 임시 손절선 적용)" if risk_alert_active else "")
                    + " -> 매도 주문 제출했으나 체결 미확인(취소 시도) — 포지션 상태 유지"
                ),
                signal_snapshot=signal_snapshot,
                order_response=order_response,
            )
            result["action"] = "sell_unconfirmed"
            return result

        state.status = "idle"
        state.entry_price = None
        state.entry_qty = None
        state.entry_at = None
        state.entry_order_no = None
        state.peak_price = None
        state.entry_foreign_flow_sign = None
        state.entry_source = None  # Phase 5.118 — 수동 진입 표식·조건도 함께 리셋
        state.manual_exit_rules = None
        await session.commit()

        await _log(
            session,
            event_type="exit_stop_loss",
            code=code,
            price=live_price,
            reason=(
                f"[실시간 호가 손절 감시(30초)] 손절 조건 충족: 실시간가 {live_price} vs "
                f"진입가 {entry_price} ({pct:+.2f}%) <= {stop_loss_threshold}%"
                + (" (리스크 경보 활성 -> 임시 손절선 적용)" if risk_alert_active else "")
                + f" -> 매도 주문 제출({held_qty}주)"
            ),
            signal_snapshot={**signal_snapshot, "qty": held_qty},
            order_response=order_response,
        )
        result["action"] = "exit_stop_loss"
        return result
