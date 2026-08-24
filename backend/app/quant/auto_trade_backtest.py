"""자동매매 전략 일봉 근사 백테스트 (PLAN.md §5.74).

**이 모듈은 실거래 경로(`collectors/auto_trader.py`)의 어디에서도 import되지
않는다 — 순수 조회/분석 도구다.** 사용자가 실거래 승률(11전 3승 8패, 27%)을
직접 계산해보고 "추천이 뭐냐"고 물어 만들었다. 백테스트 결과를 근거로 실제
전략 규칙을 바꾸는 건 이 모듈의 범위가 아니다 — 측정 도구만 제공한다.

## 중요한 제약 — 1분봉이 아니라 일봉 근사다

실제 자동매매는 1분봉 기준(5분/20분 이동평균 교차)으로 동작하지만, 그 1분봉
이력은 DB에 저장된 적이 없다(`routers/stocks.py` §5.1 원칙 — "오늘 하루치만
온디맨드 조회, DB 미저장"). `stock_ohlcv`(일봉)만 장기간 쌓여 있어, 이 모듈은
**같은 규칙 함수를 그대로 재사용하되 일봉에 적용**한다(5분→5일, 20분→20일).
실제 거래 빈도·신호 성격은 라이브와 다르지만, 손절/트레일링처럼 "가격 경로
구조"에 관한 로직은 봉 주기와 무관하게 검증 가능하다는 판단이다
(AskUserQuestion으로 사용자 확인 후 이 방식으로 진행).

## 포함하지 않는 것

§5.55 안전 규칙(진입시간필터/EOD강제청산/리스크경보 손절선 축소/수급반전
조기청산)은 전부 "몇 시인지"·"리스크 경보가 지금 켜져 있는지" 같은 장중·
실시간 개념이라 과거 일봉만으로는 재현할 수 없다 — 이 백테스트는 §5.54
핵심 규칙(진입: 골든크로스+거래량 스파이크, 손절 -1.5%, 트레일전환 +1%,
트레일청산: 데드크로스 AND 진입가+0.5% 이하)만 시뮬레이션한다.

## 재사용 원칙 — 새 판단 재구현 금지

진입/손절/트레일전환 판정은 라이브 코드(`collectors/auto_trader.py`)가 호출
하는 것과 동일한 함수를 인자 하나도 바꾸지 않고 그대로 호출한다
(`quant/signals.py::moving_average_cross`/`volume_spike` 기본 인자,
`quant/auto_trade_rules.py`의 순수 판정 함수, `collectors/auto_trader.py`의
`_golden_cross_in_lookback`/`_volume_spike_in_lookback` lookback 헬퍼). 이
백테스트가 "다른 규칙"을 테스트하는 게 아니라 "같은 규칙을 다른 봉 주기에
적용"한다는 걸 보장하기 위함이다.

``trail_mode="ratchet"``만 예외 — 사용자가 코드 리뷰 중 발견한 구조적 의심점
("트레일 청산 기준이 고점이 아니라 진입가 기준 고정값이라, 트레일링 중 크게
올랐다가 반전되면 수익을 거의 다 반납할 수 있다")을 검증하려고 만든 **비교
실험용 대안**이다 — 실거래 규칙이 아니다. 트레일 청산 조건을 데드크로스
확인 없이 순수 가격만으로 "현재가 <= peak*(1-RATCHET_GIVEBACK_PCT)"로 바꿔
고정 플로어(``fixed_floor``, 기본값·현재 실거래 규칙)와 나란히 비교할 수
있게 한다. 데드크로스 확인을 요구하지 않는 이유: 데드크로스는 "전환되는 그
봉에서만" 참인 순간 이벤트라, 확인까지 요구하면 fixed_floor와 똑같이
"그 순간 가격도 마침 플로어 밑"이어야 하는 우연이 겹쳐야만 청산돼 비교
의미가 없어진다(`run_backtest` 구현부 주석 참고).
"""

from __future__ import annotations

from typing import Any, Literal

from ..collectors.auto_trader import (
    _golden_cross_in_lookback,
    _volume_spike_in_lookback,
)
from ..quant.auto_trade_rules import (
    decide_idle_action,
    evaluate_stop_loss,
    evaluate_trail_activate,
    evaluate_trail_exit,
    next_peak_price,
)
from ..quant.signals import Bar, moving_average_cross, volume_spike

TrailMode = Literal["fixed_floor", "ratchet"]

# 비교 실험용 대안(위 모듈 docstring 참고) — 현재 TRAIL_EXIT_FLOOR_PCT(0.5,
# 진입가 기준)와 같은 숫자를 "고점 기준 반납폭"으로 바꿔 써서 두 기준선의
# 절대 크기를 맞춰 비교하기 쉽게 한다. 실거래 상수가 아니다.
RATCHET_GIVEBACK_PCT_DEFAULT = 0.5


def _summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {
            "n": 0,
            "win_rate_pct": None,
            "avg_win_pct": None,
            "avg_loss_pct": None,
            "cumulative_return_pct": 0.0,
            "best_trade_pct": None,
            "worst_trade_pct": None,
        }

    returns = [t["pct_return"] for t in trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]

    return {
        "n": n,
        "win_rate_pct": round(len(wins) / n * 100, 2),
        "avg_win_pct": round(sum(wins) / len(wins), 4) if wins else None,
        "avg_loss_pct": round(sum(losses) / len(losses), 4) if losses else None,
        "cumulative_return_pct": round(sum(returns), 4),
        "best_trade_pct": round(max(returns), 4),
        "worst_trade_pct": round(min(returns), 4),
    }


def run_backtest(
    bars: list[Bar],
    trail_mode: TrailMode = "fixed_floor",
    ratchet_giveback_pct: float = RATCHET_GIVEBACK_PCT_DEFAULT,
) -> dict[str, Any]:
    """일봉 리스트(오름차순, ``{"date","close","volume"}``)에 §5.54 핵심 규칙을
    그대로 시뮬레이션한다. 한 번에 포지션 하나만 허용(라이브와 동일 — 단일
    종목·단일 수량 전제이므로 보유 중엔 새 진입을 평가하지 않는다).

    Returns ``{"trades": [{"entry_date","entry_price","exit_date","exit_price",
    "exit_reason","pct_return"}, ...], "summary": {"n","win_rate_pct",
    "avg_win_pct","avg_loss_pct","cumulative_return_pct","best_trade_pct",
    "worst_trade_pct"}}``.
    """
    if trail_mode not in ("fixed_floor", "ratchet"):
        raise ValueError(f"trail_mode must be 'fixed_floor' or 'ratchet', got {trail_mode!r}")

    trades: list[dict[str, Any]] = []
    status: Literal["idle", "holding", "trailing"] = "idle"
    entry_price: float | None = None
    entry_date: Any = None
    peak_price: float | None = None

    for i in range(1, len(bars) + 1):
        window = bars[:i]
        current = window[-1]
        current_price = float(current["close"])
        current_date = current["date"]

        if status == "idle":
            ma_cross = moving_average_cross(window)
            vspike = volume_spike(window)
            # collectors/auto_trader.py::run_auto_trade와 동일한 조합(2026-08-05
            # lookback 수정 그대로 재사용) — "지금 이 순간"뿐 아니라 최근 몇 봉
            # 이내 신호가 있었는지도 함께 본다.
            golden_recent = ma_cross["state"] == "golden" or _golden_cross_in_lookback(window)
            spike_recent = bool(vspike["is_spike"]) or _volume_spike_in_lookback(window)
            decision = decide_idle_action("golden" if golden_recent else "none", spike_recent)
            if decision["action"] == "enter":
                status = "holding"
                entry_price = current_price
                entry_date = current_date
                peak_price = None
            continue

        # holding 또는 trailing — 손절은 상태 무관 항상 최우선(라이브와 동일).
        # §5.55-3 리스크 경보 손절선 축소는 장중 개념이라 재현 불가 — 항상
        # STOP_LOSS_PCT(-1.5%) 기본값만 쓴다(모듈 docstring 참고).
        if evaluate_stop_loss(entry_price, current_price):
            trades.append(
                {
                    "entry_date": entry_date,
                    "entry_price": entry_price,
                    "exit_date": current_date,
                    "exit_price": current_price,
                    "exit_reason": "stop_loss",
                    "pct_return": round((current_price - entry_price) / entry_price * 100, 4),
                }
            )
            status, entry_price, entry_date, peak_price = "idle", None, None, None
            continue

        if status == "holding":
            if evaluate_trail_activate(entry_price, current_price):
                status = "trailing"
                peak_price = current_price
            continue

        # status == "trailing"
        prev_peak = peak_price if peak_price is not None else entry_price
        new_peak = next_peak_price(prev_peak, current_price)
        peak_price = new_peak

        if trail_mode == "fixed_floor":
            # 청산(dead cross) 판정은 라이브와 동일하게 lookback 없이 "최신
            # 봉 기준" ma_cross를 그대로 쓴다(모듈 상단 docstring, collectors/
            # auto_trader.py 모듈 docstring "청산 판정은 이번 수정 범위 밖"
            # 절 참고).
            ma_cross = moving_average_cross(window)
            should_exit = evaluate_trail_exit(ma_cross["state"], entry_price, current_price)
        else:  # ratchet — 비교 실험용 대안(위 모듈 docstring 참고, 실거래 규칙
            # 아님). 데드크로스 확인을 요구하지 않는 순수 가격 기반 트레일링
            # 스탑이다 — 데드크로스는 "전환되는 그 봉에서만" 참인 순간
            # 이벤트라(quant/signals.py::moving_average_cross docstring
            # 참고) fixed_floor처럼 확인까지 요구하면 "그 순간 가격도 마침
            # 플로어 밑"이어야 하는 우연이 겹쳐야만 청산되는데, 그러면 이
            # 대안이 fixed_floor와 똑같이 늦게 반응해 비교 의미가 없어진다.
            should_exit = current_price <= new_peak * (1 - ratchet_giveback_pct / 100)

        if should_exit:
            trades.append(
                {
                    "entry_date": entry_date,
                    "entry_price": entry_price,
                    "exit_date": current_date,
                    "exit_price": current_price,
                    "exit_reason": "exit_trail",
                    "pct_return": round((current_price - entry_price) / entry_price * 100, 4),
                }
            )
            status, entry_price, entry_date, peak_price = "idle", None, None, None

    return {"trades": trades, "summary": _summarize(trades)}
