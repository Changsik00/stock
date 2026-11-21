"""장중 누적 순매수 곡선의 "일관성" 분석 (PLAN.md Phase 5.113).

외국인 장중 누적 순매수가 하루 종일 거의 직선으로 내려가는 "일관 매도"일(바스켓/
프로그램 분할 매도 성격)을 찾는 순수 함수 모음. 입력은 ``intraday_sample``의
``flow_{market}_{investor}`` 누적 순매수 곡선(백만원) 하나이며 DB·네트워크를
모르는 순수 계산이다(라우터가 DB에서 읽어 넘긴다).

정의(2026-11-21 분석에서 쓴 것 그대로):
- 10분 격자로 리샘플(각 버킷의 마지막 값). 장 초반 값이 전부 0인 버킷은 버린다
  (소스가 개장 직후 0을 돌려주는 경우가 있다).
- ``down_share`` = 0이 아닌 10분 변화량 중 음수 비율, ``up_share``는 그 반대.
- ``rebound`` = 하루 중 (값 - 그때까지의 최저값) 최댓값, ``pullback`` = (그때까지의
  최고값 - 값) 최댓값.
- 일관 매도: final < 0 and down_share >= 0.75 and rebound < 0.2*|final|.
  일관 매수는 대칭(up_share >= 0.75, pullback < 0.2*final, final > 0). 그 외 혼조.

참고용 통계이며 매매 신호가 아니다. 과거 표본이 작아(82거래일 중 20일) 다음날
성과 통계는 특히 그렇다.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

BUCKET_MINUTES = 10
MIN_BUCKETS = 6
SHARE_THRESHOLD = 0.75
EXCURSION_RATIO = 0.2
LABEL_SELL = "일관 매도"
LABEL_BUY = "일관 매수"
LABEL_MIXED = "혼조"


def _bucket_key(t: dt.datetime | int) -> int:
    """시각 -> 10분 버킷 번호. datetime이면 하루 안의 분(시*60+분)//10, int면 분 단위로 간주."""
    minutes = t.hour * 60 + t.minute if isinstance(t, dt.datetime) else int(t)
    return minutes // BUCKET_MINUTES


def resample_10min(points: Iterable[tuple[dt.datetime | int, float]]) -> list[float]:
    """(시각, 값) 목록 -> 10분 버킷별 마지막 값 리스트(시간순). 선행 0 버킷은 제거."""
    last: dict[int, float] = {}
    for t, v in sorted(points, key=lambda p: (_bucket_key(p[0]), p[0] if isinstance(p[0], dt.datetime) else 0)):
        last[_bucket_key(t)] = float(v)
    values = [last[k] for k in sorted(last)]
    start = 0
    while start < len(values) and values[start] == 0:
        start += 1
    return values[start:]


def _r2(values: list[float]) -> float:
    """버킷 번호에 대한 단순 선형회귀 결정계수(직선성). 분산이 0이면 0."""
    n = len(values)
    mean_x = (n - 1) / 2
    mean_y = sum(values) / n
    sxx = sum((i - mean_x) ** 2 for i in range(n))
    syy = sum((v - mean_y) ** 2 for v in values)
    if sxx == 0 or syy == 0:
        return 0.0
    sxy = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    return (sxy * sxy) / (sxx * syy)


def analyze_curve(points: list[tuple[dt.datetime | int, float]]) -> dict | None:
    """누적 순매수 곡선 -> 일관성 분석. 유효 버킷이 6개 미만이면 None.

    반환: ``{final, down_share, up_share, rebound, pullback, r2, buckets, label}``
    (금액은 입력 단위 그대로, share는 0~1).
    """
    values = resample_10min(points)
    if len(values) < MIN_BUCKETS:
        return None

    changes = [b - a for a, b in zip(values, values[1:])]
    nonzero = [c for c in changes if c != 0]
    if nonzero:
        down_share = sum(1 for c in nonzero if c < 0) / len(nonzero)
        up_share = sum(1 for c in nonzero if c > 0) / len(nonzero)
    else:
        down_share = up_share = 0.0

    run_min = values[0]
    run_max = values[0]
    rebound = 0.0
    pullback = 0.0
    for v in values:
        run_min = min(run_min, v)
        run_max = max(run_max, v)
        rebound = max(rebound, v - run_min)
        pullback = max(pullback, run_max - v)

    final = values[-1]
    label = LABEL_MIXED
    if final < 0 and down_share >= SHARE_THRESHOLD and rebound < EXCURSION_RATIO * abs(final):
        label = LABEL_SELL
    elif final > 0 and up_share >= SHARE_THRESHOLD and pullback < EXCURSION_RATIO * final:
        label = LABEL_BUY

    return {
        "final": final,
        "down_share": down_share,
        "up_share": up_share,
        "rebound": rebound,
        "pullback": pullback,
        "r2": _r2(values),
        "buckets": len(values),
        "label": label,
    }


def percentile(values: list[float], q: float) -> float | None:
    """선형 보간 백분위수(q: 0~100). 빈 리스트면 None."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * q / 100
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def percentile_rank(value: float, population: list[float]) -> float | None:
    """population 중 value 이하인 비율(0~100). 낮을수록 과거 대비 더 큰 순매도."""
    if not population:
        return None
    return 100.0 * sum(1 for p in population if p <= value) / len(population)


def _next_stats(dates: list[dt.date], next_changes: dict[dt.date, float]) -> dict:
    vals = [next_changes[d] for d in dates if d in next_changes]
    if not vals:
        return {"n": len(dates), "n_next": 0, "next_day_avg_pct": None, "next_day_up_pct": None}
    return {
        "n": len(dates),
        "n_next": len(vals),
        "next_day_avg_pct": sum(vals) / len(vals),
        "next_day_up_pct": 100.0 * sum(1 for v in vals if v > 0) / len(vals),
    }


def history_stats(
    analyses: dict[dt.date, dict],
    next_changes: dict[dt.date, float],
) -> dict:
    """과거 일별 분석(날짜 -> analyze_curve 결과)과 다음 거래일 지수 등락률(%) 맵으로
    일관 매도일 통계를 만든다.

    반환: ``{days, n, size_threshold, n_large, next_day_avg_pct, next_day_up_pct,
    n_next, baseline}``
    - ``days``: 분석 가능한 전체 일수, ``n``: 일관 매도일 수.
    - ``size_threshold``: 전체 일의 final 하위 25% 경계(백만원, 음수일수록 큼).
    - ``n_large``: 일관 매도이면서 final <= size_threshold인 일수. 다음날 통계는
      이 "큰 일관 매도일"만 대상으로 한다(분석에서 의미 있던 표본).
    - ``baseline``: 전체 일 기준 같은 통계.
    """
    days = sorted(analyses)
    finals = [analyses[d]["final"] for d in days]
    threshold = percentile(finals, 25)
    sell_days = [d for d in days if analyses[d]["label"] == LABEL_SELL]
    large = [d for d in sell_days if threshold is not None and analyses[d]["final"] <= threshold]
    stats = _next_stats(large, next_changes)
    return {
        "days": len(days),
        "n": len(sell_days),
        "size_threshold": threshold,
        "n_large": len(large),
        "n_next": stats["n_next"],
        "next_day_avg_pct": stats["next_day_avg_pct"],
        "next_day_up_pct": stats["next_day_up_pct"],
        "baseline": _next_stats(days, next_changes),
    }
