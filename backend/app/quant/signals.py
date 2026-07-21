"""진입 타이밍 시그널 — 분봉 기반 순수 계산부 (PLAN.md §5.3).

이 모듈은 DB/네트워크 무관 순수 함수만 담는다(단위테스트 대상,
tests/test_quant_signals.py) — 분봉 조회(``GET /api/stocks/{code}/intraday``,
5.1)와 응답 조립은 routers/stocks.py의 시그널 핸들러가 담당한다
(sentiment.py의 "계산부/조립부 분리" 패턴과 동일).

**원칙(§5 전체 원칙 그대로): 전부 관찰 서술이다 — "매수/매도하라" 판단·지시를
내리는 필드는 두지 않는다.** 방향/상태를 나타내는 값(``breakout``, ``ma_cross``
등)도 "지금까지 관측된 사실"이지 권고가 아니다. 호출부(라우터·프런트)도 이
값을 그대로 서술형 배지("거래량 급증 3.2배")로만 노출해야 한다.

입력 형식: 모든 함수는 오름차순(과거→최신) 분봉 리스트를 받는다 —
``GET /api/stocks/{code}/intraday`` 응답의 ``bars``와 동일한 딕셔너리 형태
``{"open", "high", "low", "close", "volume", ...}`` (그 외 키는 무시).
빈 리스트·부족한 개수 등 계산 불가 상황은 예외를 던지지 않고 ``None``/중립
상태를 반환한다 — 장 시작 직후처럼 봉이 몇 개 없을 때도 API가 500 없이
"아직 계산할 수 없음"을 그대로 전달할 수 있게 한다.
"""

from __future__ import annotations

import statistics
from typing import Any

Bar = dict[str, Any]


def _closes(bars: list[Bar]) -> list[float]:
    return [float(b["close"]) for b in bars]


def _volumes(bars: list[Bar]) -> list[float]:
    return [float(b["volume"]) for b in bars]


def compute_vwap(bars: list[Bar]) -> dict[str, float | None]:
    """VWAP(거래량가중평균가) = Σ(전형가격×거래량)/Σ거래량, 누적(당일 분봉 전체).

    전형가격(typical price) = (고가+저가+종가)/3을 쓴다(표준 VWAP 정의 — 종가만
    쓰면 봉 내 변동을 무시하게 되어 전형가격이 더 일반적).

    Returns:
        ``{"value": vwap 또는 None, "deviation_pct": (현재가-vwap)/vwap*100 또는
        None}`` — 현재가는 마지막 봉의 종가. 거래량 합이 0(또는 bars가 비어있음)이면
        둘 다 None.
    """
    if not bars:
        return {"value": None, "deviation_pct": None}

    total_pv = 0.0
    total_vol = 0.0
    for b in bars:
        typical = (float(b["high"]) + float(b["low"]) + float(b["close"])) / 3
        vol = float(b["volume"])
        total_pv += typical * vol
        total_vol += vol

    if total_vol == 0:
        return {"value": None, "deviation_pct": None}

    vwap = total_pv / total_vol
    current = float(bars[-1]["close"])
    deviation_pct = ((current - vwap) / vwap * 100) if vwap else None
    return {
        "value": round(vwap, 2),
        "deviation_pct": round(deviation_pct, 2) if deviation_pct is not None else None,
    }


def detect_breakout(bars: list[Bar]) -> dict[str, str]:
    """당일 신고가/신저가 돌파 — 마지막 봉의 종가가 "그 이전까지"의 당일 고가/저가를
    갱신했는지(관찰 사실, 지시 아님).

    ``high`` = 마지막 봉 이전 모든 봉의 최고가보다 마지막 종가가 높거나 같음,
    ``low`` = 마지막 봉 이전 모든 봉의 최저가보다 마지막 종가가 낮거나 같음.
    둘 다 해당(예: 봉이 1개뿐이라 "이전"이 없는 경우)하거나 둘 다 아니면
    ``none`` — 모순되는 신호를 주지 않기 위함.
    """
    if not bars:
        return {"direction": "none"}
    if len(bars) < 2:
        return {"direction": "none"}

    prior = bars[:-1]
    last_close = float(bars[-1]["close"])
    prior_high = max(float(b["high"]) for b in prior)
    prior_low = min(float(b["low"]) for b in prior)

    is_new_high = last_close >= prior_high
    is_new_low = last_close <= prior_low

    if is_new_high and not is_new_low:
        return {"direction": "high"}
    if is_new_low and not is_new_high:
        return {"direction": "low"}
    return {"direction": "none"}


def moving_average_cross(
    bars: list[Bar], short_window: int = 5, long_window: int = 20
) -> dict[str, Any]:
    """이동평균(단기 5분/장기 20분, 봉 개수 기준) 골든/데드크로스.

    "크로스"는 사건이다 — 직전 봉 시점엔 단기<=장기였다가 마지막 봉 시점에
    단기>장기로 바뀌면 ``golden``, 반대는 ``dead``. 계속 같은 방향으로 벌어져
    있던 상태(교차 순간이 아님)는 ``none``으로 둔다(이미 지나간 크로스를 매
    봉마다 반복 알림하지 않기 위함).

    데이터가 ``long_window + 1``개 미만이면(단기/장기 평균 + 직전 시점 비교에
    필요) 계산 불가 -> ``{"state": "none", "short_ma": None, "long_ma": None}``.
    """
    closes = _closes(bars)
    if len(closes) < long_window + 1:
        return {"state": "none", "short_ma": None, "long_ma": None}

    def sma(values: list[float], window: int) -> float:
        return sum(values[-window:]) / window

    prev_short = sma(closes[:-1], short_window)
    prev_long = sma(closes[:-1], long_window)
    cur_short = sma(closes, short_window)
    cur_long = sma(closes, long_window)

    if prev_short <= prev_long and cur_short > cur_long:
        state = "golden"
    elif prev_short >= prev_long and cur_short < cur_long:
        state = "dead"
    else:
        state = "none"

    return {"state": state, "short_ma": round(cur_short, 2), "long_ma": round(cur_long, 2)}


def volume_spike(
    bars: list[Bar], window: int = 20, threshold: float = 2.0
) -> dict[str, Any]:
    """거래량 스파이크 — 마지막 봉 거래량이 "그 이전 최근 window봉" 평균 대비
    z-score(모표준편차 기준). ``ratio``(마지막 거래량/이전 평균)를 함께 반환해
    "거래량 급증 3.2배" 같은 서술형 배지에 바로 쓸 수 있게 한다.

    비교 대상 이전 봉이 2개 미만이거나 표준편차가 0(거래량이 전부 동일)이면
    z-score를 정의할 수 없어 ``None``, ``is_spike``는 ``False``.
    """
    if len(bars) < 2:
        return {"zscore": None, "is_spike": False, "ratio": None}

    volumes = _volumes(bars)
    latest = volumes[-1]
    history = volumes[max(0, len(volumes) - 1 - window) : -1]

    if len(history) < 2:
        return {"zscore": None, "is_spike": False, "ratio": None}

    mean = statistics.mean(history)
    stdev = statistics.pstdev(history)
    ratio = round(latest / mean, 2) if mean > 0 else None

    if stdev == 0:
        return {"zscore": None, "is_spike": False, "ratio": ratio}

    zscore = (latest - mean) / stdev
    return {"zscore": round(zscore, 2), "is_spike": zscore >= threshold, "ratio": ratio}


def momentum(bars: list[Bar], window_bars: int = 5) -> dict[str, Any]:
    """최근 N봉 모멘텀 = (마지막 종가-N봉 전 종가)/N봉 전 종가*100.

    데이터가 ``window_bars + 1``개 미만이면 가진 만큼(첫 봉 기준)으로 근사
    계산한다 — 봉이 2개 미만이면 계산 불가 -> ``return_pct`` None.
    ``window_bars``는 항상 반환값에 그대로 담아, 실제로 몇 봉으로 계산했는지
    (요청한 window_bars와 다를 수 있음, 데이터 부족 시 짧아짐)를 호출부가
    구분할 필요는 없고 호출부가 interval을 곱해 "분" 단위로 환산한다.
    """
    closes = _closes(bars)
    if len(closes) < 2:
        return {"return_pct": None, "window_bars": window_bars}

    if len(closes) >= window_bars + 1:
        start = closes[-(window_bars + 1)]
    else:
        start = closes[0]

    end = closes[-1]
    if not start:
        return {"return_pct": None, "window_bars": window_bars}

    return_pct = (end - start) / start * 100
    return {"return_pct": round(return_pct, 2), "window_bars": window_bars}
