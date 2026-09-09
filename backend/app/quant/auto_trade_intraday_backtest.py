"""자동매매 전략 1분봉 백테스트 (PLAN.md §5.77, §5.74/§5.75 후속).

**이 모듈은 실거래 경로(`collectors/auto_trader.py`)의 어디에서도 import되지
않는다 — 순수 조회/분석 도구다.** §5.74의 일봉 근사 백테스트는 하루에 가격이
하나뿐이라 "오늘 장중 고점"이라는 개념 자체가 없었다. §5.75가 쌓기 시작한
`StockIntradayBar`(1분봉)를 실제로 쓰는 게 이 모듈의 존재 이유 — `moving_
average_cross`/`volume_spike`의 기본 5/20 윈도우가 일봉 버전에선 "5일/20일"
로 왜곡됐던 것이, 여기선 원래 의도인 "5분/20분"으로 그대로 맞아떨어진다.

**2026-09-09 후속(PLAN.md §5.83)** — 이 모듈로 쌓은 1분봉(그때는 하루~이틀
치뿐이었지만 이후 계속 쌓여 지금은 약 2.5주치)으로 `fixed_floor`/`ratchet`/
`daily_reset`를 다시 비교해보니 `fixed_floor`(당시 실거래 규칙)만 뚜렷하게
나빴고(누적수익률 -10.97% vs -1.43% vs +4.61%), 실거래 감사 로그(5주 26건)도
같은 방향을 가리켜(트레일 청산이 사실상 발동 안 함) 라이브 규칙을 `ratchet`
으로 교체했다(`quant/auto_trade_rules.py::evaluate_trail_exit`). 이 모듈은
그 결정 이후에도 계속 독립적으로 남는다 — `auto_trade_backtest.py`와 동일한
이유로 `fixed_floor` 상수(`LEGACY_FIXED_FLOOR_PCT`)를 그쪽에서 그대로
가져다 쓰고 `quant/auto_trade_rules.py`는 더 이상 참조하지 않는다.

## 계기 — 진입가 고정 플로어의 여러 날 보유 반납 문제

사용자가 실계좌 트레일 청산(`TRAIL_EXIT_FLOOR_PCT`, 진입가 기준 고정값)이
"고점이 아니라 진입가 기준"이라 여러 날에 걸쳐 크게 오른 뒤 반전되면 수익을
거의 다 반납할 수 있다고 지적했다(§5.74에서 이미 `ratchet` 모드로 "고점
기준"까지는 확인했다). 이번엔 한 단계 더 — "당일 수급 분위기가 크게
작용하니 당일 변동성을 우선해야 한다"는 지적에 따라 `daily_reset` 모드를
추가한다. 이건 일봉으로는 표현할 수 없는 개념이라(하루=가격 1개) 1분봉이
꼭 필요했다.

**처음 설계는 틀렸었다(직접 확인 후 수정)**: "giveback 기준 고점을 매일
그날 시가로 통째로 교체"하는 첫 시도는 실제로 테스트해보니 여러 날에 걸친
완만한 하락에서 오히려 `ratchet`보다 **더 많이** 반납했다 — 진입 이후
전체 고점(`ratchet`의 기준)은 항상 그날의 자체 고점보다 크거나 같으므로,
그 기준을 매일 더 낮은 값으로 갈아 치우면 청산까지 더 멀어질 뿐이었다
(수학적으로 검증: `P_all_time >= P_today` 이므로
`P_all_time*(1-g) >= P_today*(1-g)`, 즉 하락하는 가격은 항상 더 높은
`ratchet` 기준선에 먼저 닿는다). 그래서 지금 버전은 **고점을 교체하지
않고, `ratchet`의 기존 조건(진입 이후 전체 고점 기준, 느슨한 쪽)에 더
타이트한 "오늘 자체 고점 기준" 조건을 OR로 추가**하는 방식으로 바꿨다 —
이러면 `ratchet`보다 절대 늦게 청산될 수 없고(항상 같거나 빠름), 오늘
자체 고점을 기준으로 한 급락만 유독 심할 때 더 빨리 반응할 수 있다.
`daily_giveback_pct`(기본 0.3%, `ratchet_giveback_pct` 0.5%보다 타이트)는
아직 실 1분봉 데이터로 보정된 값이 아니다 — 판단용 최종값이 아니라 비교
도구의 잠정 기본값이다(1분봉 이력이 더 쌓이면 재보정 필요).

## 재사용 원칙 — `auto_trade_backtest.py`와 동일

진입/손절/트레일전환 판정은 라이브 코드가 쓰는 것과 동일한 함수를 인자 하나도
바꾸지 않고 그대로 호출한다(`quant/signals.py::moving_average_cross`/
`volume_spike` 기본 인자, `quant/auto_trade_rules.py`의 순수 판정 함수,
`collectors/auto_trader.py`의 lookback 헬퍼). 집계(`_summarize`)도 새로
만들지 않고 `auto_trade_backtest.py`에서 그대로 가져다 쓴다 — 진입가와
무관한 순수 집계라 봉 주기가 달라져도 그대로 재사용 가능하다.

## 범위 — 여전히 §5.54 핵심 규칙만

1분봉엔 실제 시각이 있어 §5.55 안전규칙(EOD강제청산 등)도 흉내낼 수는
있지만 **일부러 포함하지 않는다** — 트레일 설계 3종(`fixed_floor`/
`ratchet`/`daily_reset`) 비교가 EOD강제청산에 의한 조기 청산으로 뒤섞이면
정작 보고 싶은 "여러 날 보유 시 얼마나 반납하는가"를 못 보게 된다.

## trail_mode 3종

- ``fixed_floor``: 현재 실거래 규칙 — 데드크로스 확인 AND 진입가+0.5% 이하.
- ``ratchet``(§5.74): 진입 이후 전체 기간의 고점 기준 반납폭, 데드크로스
  확인 불필요(순수 가격 기반) — 이유는 `auto_trade_backtest.py` 모듈
  docstring 참고(데드크로스는 순간 이벤트라 확인까지 요구하면 fixed_floor와
  똑같이 늦게 반응해 비교 의미가 없어짐, 여기서도 동일하게 적용).
- ``daily_reset``(신규): `ratchet` 조건(진입 이후 전체 고점 기준,
  `ratchet_giveback_pct`)은 그대로 두고, "그날의 러닝 최고가" 대비
  `daily_giveback_pct`(더 타이트한 값) 반납 조건을 OR로 추가한다 — 둘 중
  먼저 충족되는 쪽이 청산을 발동시킨다. `ratchet`보다 절대 늦게 청산될 수
  없다(항상 같거나 빠름). **하루 안에서는(트레일링이 그날 시작해서 그날
  끝나면) 그날의 러닝 최고가와 진입 이후 전체 고점이 같으므로, 두 조건 중
  `daily_giveback_pct`가 더 타이트한 만큼 이 조건이 먼저 충족돼 `ratchet`
  보다 빠르거나 같게 청산된다** — 완전히 동일하게 동작하지는 않는다(이
  모듈 위쪽 "처음 설계는 틀렸었다" 절 참고, 첫 시도와의 핵심 차이).
"""

from __future__ import annotations

from typing import Any, Literal

from ..collectors.auto_trader import (
    _golden_cross_in_lookback,
    _volume_spike_in_lookback,
)
from ..quant.auto_trade_backtest import (
    LEGACY_FIXED_FLOOR_PCT,
    RATCHET_GIVEBACK_PCT_DEFAULT,
    _summarize,
)
from ..quant.auto_trade_rules import (
    decide_idle_action,
    evaluate_stop_loss,
    evaluate_trail_activate,
    next_peak_price,
)
from ..quant.signals import Bar, moving_average_cross, volume_spike

TrailMode = Literal["fixed_floor", "ratchet", "daily_reset"]

# daily_reset 전용 — 오늘 자체 고점 기준 반납 조건의 임계값. ratchet_giveback_pct
# (0.5%)보다 타이트해야 한다 — 안 그러면 이 조건은 절대 ratchet 조건보다 먼저
# 충족될 수 없다(모듈 docstring "처음 설계는 틀렸었다" 절 참고). 실 1분봉
# 데이터로 보정된 값이 아닌 잠정 기본값.
DAILY_GIVEBACK_PCT_DEFAULT = 0.3


def run_intraday_backtest(
    bars: list[Bar],
    trail_mode: TrailMode = "fixed_floor",
    ratchet_giveback_pct: float = RATCHET_GIVEBACK_PCT_DEFAULT,
    daily_giveback_pct: float = DAILY_GIVEBACK_PCT_DEFAULT,
) -> dict[str, Any]:
    """1분봉 리스트(오름차순, ``{"date","time","close","volume"}``)에 §5.54
    핵심 규칙을 그대로 시뮬레이션한다. 한 번에 포지션 하나만 허용(라이브와
    동일).

    Returns ``{"trades": [{"entry_date","entry_time","entry_price","exit_date",
    "exit_time","exit_price","exit_reason","pct_return"}, ...], "summary": {...}}``
    (summary 모양은 `auto_trade_backtest._summarize` 참고).
    """
    if trail_mode not in ("fixed_floor", "ratchet", "daily_reset"):
        raise ValueError(f"trail_mode must be 'fixed_floor', 'ratchet' or 'daily_reset', got {trail_mode!r}")

    trades: list[dict[str, Any]] = []
    status: Literal["idle", "holding", "trailing"] = "idle"
    entry_price: float | None = None
    entry_date: Any = None
    entry_time: Any = None
    peak_price: float | None = None
    daily_peak_price: float | None = None
    prev_date: Any = None

    for i in range(1, len(bars) + 1):
        window = bars[:i]
        current = window[-1]
        current_price = float(current["close"])
        current_date = current["date"]
        current_time = current["time"]

        # daily_reset 전용 — 직전 봉과 date가 다르면 새 거래일(오버나잇 경계).
        # 상태와 무관하게 매 봉 갱신해 둔다(trailing 진입 시점에 곧바로 옳은
        # 값을 참조할 수 있게).
        is_new_day = prev_date is not None and current_date != prev_date
        prev_date = current_date

        if status == "idle":
            ma_cross = moving_average_cross(window)
            vspike = volume_spike(window)
            golden_recent = ma_cross["state"] == "golden" or _golden_cross_in_lookback(window)
            spike_recent = bool(vspike["is_spike"]) or _volume_spike_in_lookback(window)
            decision = decide_idle_action("golden" if golden_recent else "none", spike_recent)
            if decision["action"] == "enter":
                status = "holding"
                entry_price = current_price
                entry_date = current_date
                entry_time = current_time
                peak_price = None
                daily_peak_price = None
            continue

        # 손절은 상태 무관 항상 최우선(라이브·일봉 버전과 동일).
        if evaluate_stop_loss(entry_price, current_price):
            trades.append(
                {
                    "entry_date": entry_date,
                    "entry_time": entry_time,
                    "entry_price": entry_price,
                    "exit_date": current_date,
                    "exit_time": current_time,
                    "exit_price": current_price,
                    "exit_reason": "stop_loss",
                    "pct_return": round((current_price - entry_price) / entry_price * 100, 4),
                }
            )
            status, entry_price, entry_date, entry_time = "idle", None, None, None
            peak_price, daily_peak_price = None, None
            continue

        if status == "holding":
            if evaluate_trail_activate(entry_price, current_price):
                status = "trailing"
                peak_price = current_price
                daily_peak_price = current_price
            continue

        # status == "trailing"
        daily_peak_price = current_price if is_new_day else next_peak_price(daily_peak_price, current_price)
        prev_peak = peak_price if peak_price is not None else entry_price
        new_peak = next_peak_price(prev_peak, current_price)
        peak_price = new_peak

        if trail_mode == "fixed_floor":
            # 레거시 규칙(2026-08-04~2026-09-09, PLAN.md §5.83로 교체됨) —
            # `auto_trade_backtest.py`와 동일하게 이 상수를 그대로 가져다
            # 쓴다(quant/auto_trade_rules.py는 더 이상 참조하지 않음).
            ma_cross = moving_average_cross(window)
            should_exit = ma_cross["state"] == "dead" and (
                (current_price - entry_price) / entry_price * 100
            ) <= LEGACY_FIXED_FLOOR_PCT
        elif trail_mode == "ratchet":
            should_exit = current_price <= new_peak * (1 - ratchet_giveback_pct / 100)
        else:  # daily_reset — ratchet 조건 OR 오늘 자체 고점 기준 타이트한 조건
            should_exit = current_price <= new_peak * (1 - ratchet_giveback_pct / 100) or (
                current_price <= daily_peak_price * (1 - daily_giveback_pct / 100)
            )

        if should_exit:
            trades.append(
                {
                    "entry_date": entry_date,
                    "entry_time": entry_time,
                    "entry_price": entry_price,
                    "exit_date": current_date,
                    "exit_time": current_time,
                    "exit_price": current_price,
                    "exit_reason": "exit_trail",
                    "pct_return": round((current_price - entry_price) / entry_price * 100, 4),
                }
            )
            status, entry_price, entry_date, entry_time = "idle", None, None, None
            peak_price, daily_peak_price = None, None

    return {"trades": trades, "summary": _summarize(trades)}
