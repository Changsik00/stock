"""코스피–나스닥100 선물(NQ) 장중 동조도 (PLAN.md Phase 5.116). 참고용 표시 — 매매 신호 아님.

DB·네트워크를 모르는 순수 함수 모음이다(라우터가 DB에서 읽어 넘긴다).

시각 정렬 규약(2026-11-23 실측으로 확정):
- NQ 5분봉(yfinance)의 타임스탬프는 *봉 시작* 시각이다. 라벨 T 봉의 종가는 T+5분 시점의 가격.
- 코스피 1분봉의 "HHMM"은 같은 5분 버킷에 묶고(분 // 5 * 5) 버킷의 마지막 종가를 쓴다.
  버킷 T의 마지막 종가도 T+5분 무렵 값이라 NQ 라벨 T 봉 종가와 같은 순간이다.
- 기준점(시가): 코스피는 첫 1분봉 시가, NQ는 라벨 08:55 봉 종가(= 09:00 시점).
- 모든 시각은 KST 분 단위 정수(시*60+분)로 다룬다.

등락 분해: 코스피 시가 대비 등락(%) = beta × NQ 같은 구간 등락(%) + 한국 고유(잔차).
beta는 5분 수익률 쌍의 최소제곱 기울기(cov/var). 상관은 인과·예측이 아니다.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

BUCKET = 5
OPEN_MIN = 9 * 60  # 09:00
CLOSE_MIN = 15 * 60 + 30  # 15:30
MIN_PAIRS = 12  # 유효 5분 수익률 쌍이 이보다 적으면 판정 없음
MIN_HISTORY_DAYS = 10  # 과거 일별 corr이 이보다 적으면 라벨 없음
LABEL_STRONG = "동조 강함"
LABEL_WEAK = "동조 약함"
LABEL_MID = "보통"


def hhmm_to_min(s: str) -> int | None:
    """"HHMM" -> 분(시*60+분). 형식이 틀리면 None."""
    if not isinstance(s, str) or len(s) != 4 or not s.isdigit():
        return None
    h, m = int(s[:2]), int(s[2:])
    return h * 60 + m if h < 24 and m < 60 else None


def resample_kospi(bars: Iterable[tuple[int, float]]) -> dict[int, float]:
    """(분, 종가) 1분봉 -> {버킷 시작 분: 버킷 마지막 종가}. 정규장(09:00~15:30)만. 15:30~15:35 봉은 마지막 버킷에 합친다."""
    last: dict[int, tuple[int, float]] = {}
    for minute, close in bars:
        if close is None or close != close or close <= 0:
            continue
        b = minute // BUCKET * BUCKET
        if CLOSE_MIN <= minute <= CLOSE_MIN + 5:
            b = CLOSE_MIN - BUCKET  # 15:30~15:35 봉(종가 단일가 확정값)은 마지막 버킷(15:25~15:30)에 합친다
        if b < OPEN_MIN or b >= CLOSE_MIN:
            continue
        if b not in last or minute >= last[b][0]:
            last[b] = (minute, float(close))
    return {b: v for b, (_, v) in last.items()}


def pair_returns(kospi: dict[int, float], nq: dict[int, float], anchor_k: float | None = None, anchor_nq: float | None = None) -> list[tuple[int, float, float]]:
    """같은 버킷 두 시리즈의 연속 5분 수익률(%) 쌍 [(버킷, 코스피, NQ)].

    직전 버킷이 양쪽 모두 있어야 수익률이 성립한다. 첫 버킷(09:00)은 기준점(시가)이 주어지면
    그것을 직전 값으로 쓴다."""
    out: list[tuple[int, float, float]] = []
    for b in sorted(set(kospi) & set(nq)):
        pk, pn = kospi.get(b - BUCKET), nq.get(b - BUCKET)
        if b == OPEN_MIN:
            pk, pn = anchor_k, anchor_nq
        if pk is None or pn is None or pk <= 0 or pn <= 0:
            continue
        out.append((b, (kospi[b] / pk - 1) * 100, (nq[b] / pn - 1) * 100))
    return out


def regression(pairs: Sequence[tuple[int, float, float]]) -> dict | None:
    """수익률 쌍 -> {n, corr, beta, r2}. 쌍이 MIN_PAIRS 미만이거나 분산 0이면 None."""
    n = len(pairs)
    if n < MIN_PAIRS:
        return None
    xs = [p[2] for p in pairs]  # NQ
    ys = [p[1] for p in pairs]  # 코스피
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0 or syy <= 0:
        return None
    corr = sxy / math.sqrt(sxx * syy)
    return {"n": n, "corr": corr, "beta": sxy / sxx, "r2": corr * corr}


def decompose(kospi_change_pct: float, nq_change_pct: float, beta: float) -> dict:
    """코스피 시가 대비 등락(%p) = 나스닥으로 설명되는 부분(beta × NQ 등락) + 한국 고유(잔차)."""
    explained = beta * nq_change_pct
    return {
        "kospi_change_pct": kospi_change_pct,
        "nq_change_pct": nq_change_pct,
        "explained_pct": explained,
        "unique_pct": kospi_change_pct - explained,
    }


def overnight_gap(prev_close_kospi: float | None, open_kospi: float | None, nq_prev_1530: float | None, nq_open: float | None) -> dict | None:
    """전 거래일 15:30 → 오늘 09:00 변화. NQ 변화 vs 코스피 시가 갭(%). 값이 모자라면 None."""
    if not (prev_close_kospi and open_kospi and nq_prev_1530 and nq_open):
        return None
    return {
        "nq_pct": (nq_open / nq_prev_1530 - 1) * 100,
        "kospi_gap_pct": (open_kospi / prev_close_kospi - 1) * 100,
    }


def percentile_of(value: float, sample: Sequence[float]) -> float | None:
    """표본 중 value 이하인 비율(0~100). 표본이 비면 None."""
    if not sample:
        return None
    return sum(1 for s in sample if s <= value) / len(sample) * 100


def quantile(sample: Sequence[float], q: float) -> float | None:
    """선형 보간 분위수(0~1). 표본이 비면 None."""
    if not sample:
        return None
    s = sorted(sample)
    pos = q * (len(s) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def history_summary(daily_corrs: Sequence[float]) -> dict:
    """과거 일별 corr 분포 요약. 경계(하위/상위 1/3)는 분포에서 계산한다(하드코딩 아님)."""
    n = len(daily_corrs)
    if n == 0:
        return {"days": 0, "median": None, "lower": None, "upper": None}
    return {
        "days": n,
        "median": quantile(daily_corrs, 0.5),
        "lower": quantile(daily_corrs, 1 / 3),
        "upper": quantile(daily_corrs, 2 / 3),
    }


def label_corr(corr: float | None, daily_corrs: Sequence[float]) -> str | None:
    """상위 1/3 -> 동조 강함, 하위 1/3 -> 동조 약함, 나머지 보통. 판정 불가(corr 없음·과거 표본 부족)는 None."""
    if corr is None or len(daily_corrs) < MIN_HISTORY_DAYS:
        return None
    lower, upper = quantile(daily_corrs, 1 / 3), quantile(daily_corrs, 2 / 3)
    if corr >= upper:
        return LABEL_STRONG
    if corr <= lower:
        return LABEL_WEAK
    return LABEL_MID
