"""시초 정보(갭 + 장 초반 N분 실현변동성) 순수 계산 — PLAN.md §5.94 / §5.95.

`scripts/opening_vol_study.py`(연구)와 `GET /api/markets/vol-regime`(대시보드 표시)이 **같은 정의**를
쓰도록 공통화한 모듈이다. 입출력은 모두 메모리 값(DB/네트워크 없음). **표시·연구 전용 — 매매 판정/킬스위치와
무관하다.**

정의(§5.94 (B)):
- 봉 dict: {"m": 분(09:00=540), "o","h","l","c"}. 시간대는 KST.
- 갭 = 09:00 봉 시가 / 전일 종가 - 1 (%).
- rv(N) = 초반 N분(09:00 <= m < 09:00+N) 1분 로그수익률(%) 제곱합의 제곱근. 첫 수익률의 기준은 09:00 봉 시가.
  직전 봉과 2분 이상 간격이 벌어진 구간(결측/서킷브레이커)을 건너뛴 수익률은 버린다.
  단 15:19/15:20 -> 15:30 종가 단일가 점프는 정상이라 유지.
- 초반 윈도우에 결측 분이 있으면 표준(strict) 정의에서 그 N은 사용하지 않는다(통계 표본 제외, 표시는 partial).
"""

from __future__ import annotations

import math
from typing import Sequence

NS = [5, 10, 15, 30]  # 연구용 초반 윈도우(분)
LIVE_NS = [5, 15, 30]  # 대시보드 표시 윈도우
RANGE_WIN = 20  # 직전 평균 레인지 일수
OPEN_MIN = 9 * 60
MIN_BARS = 300  # 한 거래일을 '완전한 날'로 보는 최소 봉 수

CALM_BELOW = 40.0  # 백분위 < 40 평온
HIGH_FROM = 80.0  # 백분위 >= 80 고변동(연구의 상위 20%와 일치)


def hhmm_to_min(t: str) -> int:
    return int(t[:2]) * 60 + int(t[2:4])


def auction_gap(a: int, b: int) -> bool:
    """15:20~15:30 종가 단일가 구간(봉 없음, 정상 공백): 15:19/15:20 -> 15:30."""
    return a >= 15 * 60 + 19 and b == 15 * 60 + 30


def log_returns(bars: Sequence[dict], first_prev: float) -> list[float]:
    """1분 로그수익률(%). 결측 구간(직전 봉과 2분 이상 간격)을 건너뛴 수익률은 버린다. 단 종가 단일가 점프는 유지."""
    out = []
    prev_c, prev_m = first_prev, None
    for b in bars:
        if prev_m is None or b["m"] - prev_m == 1 or auction_gap(prev_m, b["m"]) or prev_m >= 15 * 60 + 30:
            out.append(math.log(b["c"] / prev_c) * 100)
        prev_c, prev_m = b["c"], b["m"]
    return out


def realized_vol(bars: Sequence[dict], first_prev: float) -> float:
    """실현변동성(%) = 1분 로그수익률 제곱합의 제곱근."""
    return math.sqrt(sum(r * r for r in log_returns(bars, first_prev)))


def build_daily_context(rows: Sequence[tuple]) -> dict:
    """일봉 행 [(date,o,h,l,c), ...](날짜 오름차순) -> {date: {o,h,l,c,prev_c,avg20}} + "__next".
    avg20 = 직전 20거래일 평균 일레인지((h-l)/전일종가 %), 20일 미만이면 None.
    "__next"는 일봉이 아직 없는 최신 거래일(장중/마감 직후)용 직전 종가·최근 20일 평균."""
    days: dict = {}
    prev = None
    ranges: list[float] = []

    def _avg():
        return sum(ranges[-RANGE_WIN:]) / RANGE_WIN if len(ranges) >= RANGE_WIN else None

    for d, o, h, l, c in rows:
        if None in (o, h, l, c):
            continue
        o, h, l, c = map(float, (o, h, l, c))
        days[d] = {"o": o, "h": h, "l": l, "c": c, "prev_c": prev, "avg20": _avg()}
        if prev:
            ranges.append((h - l) / prev * 100)
        prev = c
    days["__next"] = {"o": None, "h": None, "l": None, "c": None, "prev_c": prev, "avg20": _avg()}
    return days


def day_features(bars: Sequence[dict], daily: dict | None, strict: bool = True) -> dict | None:
    """한 거래일의 시초 피처 + 결과변수. 첫 봉이 09:00이 아니거나 봉이 너무 적으면 None.
    strict=False는 초반 윈도우에 결측 분이 있어도 존재하는 봉만으로 부분 계산한다(사례 확인용, 통계 표본에는 쓰지 않음)."""
    if not bars or bars[0]["m"] != OPEN_MIN or len(bars) < (MIN_BARS if strict else 200) or not daily or not daily["prev_c"]:
        return None
    pc = daily["prev_c"]
    open0 = bars[0]["o"]
    f = {"gap": (open0 / pc - 1) * 100, "avg20": daily["avg20"], "pc": pc, "open0": open0}
    f["day_range"] = (max(b["h"] for b in bars) - min(b["l"] for b in bars)) / pc * 100
    f["ratio"] = f["day_range"] / daily["avg20"] if daily["avg20"] else None
    for n in NS:
        early = [b for b in bars if b["m"] < OPEN_MIN + n]
        rest = [b for b in bars if b["m"] >= OPEN_MIN + n]
        if (strict and len(early) < n) or not early or not rest:  # 초반 윈도우에 결측 분이 있으면 해당 N은 사용하지 않는다
            f[f"ok{n}"] = False
            continue
        f[f"ok{n}"] = True
        f[f"rng{n}"] = (max(b["h"] for b in early) - min(b["l"] for b in early)) / pc * 100
        f[f"rv{n}"] = realized_vol(early, open0)
        f[f"early_ret{n}"] = (early[-1]["c"] / open0 - 1) * 100
        # 나머지 구간: 첫 수익률의 기준은 초반 윈도우 마지막 종가
        f[f"rest_rng{n}"] = (max(b["h"] for b in rest) - min(b["l"] for b in rest)) / pc * 100
        f[f"rest_rv{n}"] = realized_vol(rest, early[-1]["c"])
    return f


def percentile_rank(v: float, sample: Sequence[float]) -> float | None:
    """표본 중 v보다 작은 비율(%). 표본이 비면 None. (연구 스크립트 pct와 동일: strict <)"""
    if not sample:
        return None
    return sum(1 for x in sample if x < v) / len(sample) * 100


def regime_label(pct: float | None) -> str | None:
    """백분위 -> 'calm'(<40) / 'normal'(40~80) / 'high'(>=80). None이면 None."""
    if pct is None:
        return None
    if pct < CALM_BELOW:
        return "calm"
    if pct < HIGH_FROM:
        return "normal"
    return "high"


def live_window(bars: Sequence[dict], open0: float, n: int) -> dict:
    """오늘(또는 최신 거래일) 분봉으로 초반 N분 rv를 계산한다.
    status: 'pending'(아직 N분 안 지남 — rv 없음) / 'ok'(결측 없음) / 'partial'(윈도우 안에 결측 분 있음, 존재 봉만으로 계산).
    '지났다' 기준 = 마지막 봉 분 >= 09:00+n-1 (n번째 봉 도착)."""
    last_m = bars[-1]["m"] if bars else None
    if last_m is None or last_m < OPEN_MIN + n - 1:
        return {"minutes": n, "rv_pct": None, "status": "pending", "bars": 0, "expected": n}
    early = [b for b in bars if b["m"] < OPEN_MIN + n]
    status = "ok" if len(early) >= n else "partial"
    return {"minutes": n, "rv_pct": realized_vol(early, open0), "status": status, "bars": len(early), "expected": n}


def pick_label(windows: Sequence[dict]) -> tuple[str, int | None]:
    """windows(각 'status','percentile')에서 레짐 라벨을 고른다 -> (label, 근거 윈도우 분).
    가장 긴 '완전(ok)' 윈도우의 백분위 기준(30 -> 15 -> 5). 완전 윈도우가 없고 부분계산만 있으면 'partial'(라벨 만들지 않음),
    아무 윈도우도 안 지났으면 'wait', 완전 윈도우인데 분포가 없으면 'nodata'."""
    ok = [w for w in windows if w["status"] == "ok"]
    if ok:
        w = max(ok, key=lambda x: x["minutes"])
        lab = regime_label(w.get("percentile"))
        return (lab or "nodata", w["minutes"])
    if any(w["status"] == "partial" for w in windows):
        return "partial", None
    return "wait", None


# ---------------------------------------------------------------- 대시보드용 스냅샷 (PLAN.md §5.95)

DISCLAIMER = "참고용 지표이며 매매 신호가 아닙니다."


def find_gaps(bars: Sequence[dict]) -> list[tuple[int, int]]:
    """분봉 중간 결측 구간 [(직전 봉 분, 다음 봉 분)] — 종가 단일가(15:20~15:30)는 정상이라 제외."""
    ms = [b["m"] for b in bars]
    return [(a, b) for a, b in zip(ms, ms[1:]) if b - a > 1 and not auction_gap(a, b) and a < 15 * 60 + 30]


def _hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def rv_distribution(
    bars_by_day: dict, daily_ctx: dict, before_date, ns: Sequence[int] = LIVE_NS
) -> dict[int, list[float]]:
    """before_date 이전 거래일들의 표준(strict) 정의 rv 분포 {N: [rv...]}. 결측 윈도우 날은 해당 N에서 제외.
    daily_ctx는 build_daily_context 결과(일봉이 없는 최신일은 "__next" 사용)."""
    real = [k for k in daily_ctx if k != "__next"]
    last_daily = max(real) if real else None
    out: dict[int, list[float]] = {n: [] for n in ns}
    for d, bars in bars_by_day.items():
        if d >= before_date:
            continue
        dd = daily_ctx.get(d) or (daily_ctx["__next"] if last_daily is not None and d > last_daily else None)
        f = day_features(bars, dd)
        if not f:
            continue
        for n in ns:
            if f.get(f"ok{n}"):
                out[n].append(f[f"rv{n}"])
    return out


def build_market_snapshot(bars: Sequence[dict], daily: dict | None, dist: dict[int, list[float]]) -> dict:
    """한 시장의 (최신 거래일) 분봉 -> 레짐 스냅샷. 분봉이 없으면 label 'nodata'.
    daily = {"prev_c","avg20"}(해당 일자 기준), dist = rv_distribution 결과."""
    if not bars:
        return {"label": "nodata", "label_basis_minutes": None, "gap_pct": None, "windows": [], "warnings": ["분봉 데이터 없음"]}
    warnings: list[str] = []
    prev_c = (daily or {}).get("prev_c")
    avg20 = (daily or {}).get("avg20")
    open0 = bars[0]["o"]
    first_ok = bars[0]["m"] == OPEN_MIN
    gap_pct = (open0 / prev_c - 1) * 100 if (prev_c and first_ok) else None
    if not first_ok:
        warnings.append(f"첫 봉이 {_hhmm(bars[0]['m'])}(09:00 아님) — 시초 갭/초반 변동성이 부정확할 수 있음")
    if not prev_c:
        warnings.append("전일 종가 없음 — 갭/레인지 배율 계산 불가")

    windows = []
    for n in LIVE_NS:
        w = live_window(bars, open0, n)
        sample = dist.get(n, [])
        w["percentile"] = percentile_rank(w["rv_pct"], sample) if w["rv_pct"] is not None else None
        w["n_days"] = len(sample)
        windows.append(w)
        if w["status"] == "partial":
            warnings.append(f"초반 {n}분 구간에 분봉 결측({w['bars']}/{n}봉) — 부분계산 값이며 라벨 근거로 쓰지 않음")
    gaps = find_gaps(bars)
    if gaps:
        total = sum(b - a - 1 for a, b in gaps)
        warnings.append(f"분봉 중간 공백 {total}분 (첫 공백 {_hhmm(gaps[0][0] + 1)}~{_hhmm(gaps[0][1] - 1)}) — 거래 정지(서킷브레이커 등) 또는 수집 공백, 원인 미확정")

    label, basis = pick_label(windows)
    day_range = (max(b["h"] for b in bars) - min(b["l"] for b in bars)) / prev_c * 100 if prev_c else None
    return {
        "label": label,
        "label_basis_minutes": basis,
        "gap_pct": gap_pct,
        "windows": windows,
        "range_today_pct": day_range,
        "avg20_range_pct": avg20,
        "range_ratio": day_range / avg20 if (day_range is not None and avg20) else None,
        "last_bar_time": _hhmm(bars[-1]["m"]),
        "bar_count": len(bars),
        "has_gap": bool(gaps),
        "warnings": warnings,
    }
