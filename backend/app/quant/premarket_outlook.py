"""프리마켓 예상 시작점 — 밤사이 나스닥선물(NQ) 변화로 NXT 08:00 시가 갭을 회귀 예측 (PLAN.md §5.107).

**참고용, 매매 신호 아님.** 실거래 판정(`auto_trade_rules.py`)·킬스위치와 연결하지 않는다.
이 모듈은 순수 함수만 둔다(DB·네트워크 없음) — 입력 적재는 `collectors/premarket_outlook_log.py`.

정의(모두 로그수익률, 외부로 낼 때만 단순 변화율 %로 변환):
- 갭(거래일 d) = ln( d일 NXT 08:00 봉 시가 / 직전 거래일 KRX 정규장 종가(15:30 종가 단일가) )
- NQ 변화 = ln( NQ_now / NQ_base )
    NQ_base = 직전 거래일 15:30 KST 이전 가장 최근 NQ 봉 종가(봉 시작 <= 15:25 KST)
    NQ_now  = d일 08:00 KST 이전 가장 최근 봉 종가(봉 시작 <= 07:55 KST). CME 정비 시간(미 동부 17~18시)
              · 주말에는 봉이 없어 더 이전 봉이 잡히고, 그 시각을 함께 돌려준다.
- 종목별 OLS 갭 = alpha + beta x NQ변화, 적합 창 = 대상일 **이전** 최근 60거래일(대상일 자신 절대 제외 —
  미래 참조 방지), 유효 표본 20일 미만이면 예측하지 않는다("insufficient").
- weak: |NQ 변화| < 0.5%이면 "참고 정보 약함"(사전 분석에서 NQ가 조금 움직인 날은 정보가 거의 없었다).
"""

from __future__ import annotations

import bisect
import datetime as dt
import math
import statistics
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from ..krx_calendar import is_krx_closed

KST = dt.timezone(dt.timedelta(hours=9))

# 예측 대상 종목(수집기·API·로그가 공유하는 단일 정의).
OUTLOOK_STOCKS: tuple[tuple[str, str], ...] = (
    ("005930", "삼성전자"),
    ("000660", "SK하이닉스"),
    ("005380", "현대차"),
    ("035420", "NAVER"),
    ("373220", "LG에너지솔루션"),
)
OUTLOOK_CODES = tuple(c for c, _ in OUTLOOK_STOCKS)
OUTLOOK_NAMES = dict(OUTLOOK_STOCKS)

WINDOW_DAYS = 60  # 적합 창(거래일)
MIN_SAMPLES = 20  # 이 미만이면 예측하지 않는다
WEAK_NQ_PCT = 0.5  # |NQ 변화| 이 미만이면 weak
NQ_BASE_CUTOFF = dt.time(15, 25)  # 직전 거래일 이 시각 이전(봉 시작 기준) 마지막 봉
NQ_NOW_CUTOFF = dt.time(7, 55)  # 대상일 이 시각 이전(봉 시작 기준) 마지막 봉
NQ_MAX_STALE = dt.timedelta(hours=72)  # 기준 시각보다 이만큼 넘게 오래된 봉은 쓰지 않는다(주말 + 여유)

DISCLAIMER = "참고용, 매매 신호 아님"


# -- 세션 상태(phase) ----------------------------------------------------------------


def next_open_day(day: dt.date, closed: Callable[[dt.date], bool] = is_krx_closed) -> dt.date:
    """day 다음 날부터 처음 만나는 개장일."""
    d = day + dt.timedelta(days=1)
    while closed(d):
        d += dt.timedelta(days=1)
    return d


def prev_open_day(day: dt.date, closed: Callable[[dt.date], bool] = is_krx_closed) -> dt.date:
    """day 이전 날부터 거꾸로 처음 만나는 개장일."""
    d = day - dt.timedelta(days=1)
    while closed(d):
        d -= dt.timedelta(days=1)
    return d


def session_phase(now_kst: dt.datetime, closed: Callable[[dt.date], bool] = is_krx_closed) -> dict:
    """KST 시각 -> {phase, base_date, target_date, target_date_assumed}.

    - 개장일 00:00~08:00: forecast, 대상=오늘, 기준=직전 거래일
    - 개장일 08:00~09:00: opened, 대상=오늘(예측은 08:00 직전 NQ 기준 고정 + 실제 프리 시가)
    - 개장일 09:00~15:30: hidden
    - 개장일 15:30~24:00: forecast, 대상=다음 개장일(가정), 기준=오늘
    - 휴장일(주말·알려진 공휴일): forecast, 대상=다음 개장일(가정), 기준=직전 거래일
    """
    today = now_kst.date()
    t = now_kst.time()
    if closed(today):
        return {
            "phase": "forecast",
            "base_date": prev_open_day(today, closed),
            "target_date": next_open_day(today, closed),
            "target_date_assumed": True,
        }
    if t < dt.time(8, 0):
        return {
            "phase": "forecast",
            "base_date": prev_open_day(today, closed),
            "target_date": today,
            "target_date_assumed": False,
        }
    if t < dt.time(9, 0):
        return {
            "phase": "opened",
            "base_date": prev_open_day(today, closed),
            "target_date": today,
            "target_date_assumed": False,
        }
    if t < dt.time(15, 30):
        return {"phase": "hidden", "base_date": today, "target_date": today, "target_date_assumed": False}
    return {
        "phase": "forecast",
        "base_date": today,
        "target_date": next_open_day(today, closed),
        "target_date_assumed": True,
    }


# -- NQ 시리즈 ----------------------------------------------------------------------


class NqSeries:
    """NQ 5분봉(봉 시작 시각 -> 종가) 조회 인덱스. 시각은 tz-aware여야 한다."""

    def __init__(self, bars: Iterable[tuple[dt.datetime, float]]):
        d: dict[dt.datetime, float] = {}
        for t, v in bars:
            if t.tzinfo is None or v is None or v != v or v <= 0:
                continue
            d[t] = float(v)
        self._times = sorted(d)
        self._vals = [d[t] for t in self._times]

    def __len__(self) -> int:
        return len(self._times)

    def last_at_or_before(
        self, cutoff: dt.datetime, max_stale: dt.timedelta = NQ_MAX_STALE
    ) -> tuple[dt.datetime, float] | None:
        """봉 시작 <= cutoff 인 가장 최근 봉. cutoff보다 max_stale 넘게 오래됐으면 None."""
        i = bisect.bisect_right(self._times, cutoff)
        if i == 0:
            return None
        t = self._times[i - 1]
        if cutoff - t > max_stale:
            return None
        return t, self._vals[i - 1]


def _kst(day: dt.date, tm: dt.time) -> dt.datetime:
    return dt.datetime.combine(day, tm, tzinfo=KST)


def nq_base_cutoff(base_date: dt.date) -> dt.datetime:
    return _kst(base_date, NQ_BASE_CUTOFF)


def nq_now_cutoff(target_date: dt.date, now: dt.datetime | None = None) -> dt.datetime:
    """대상일 07:55 KST. 아직 그 시각 전이면(밤사이 잠정 예측) 지금 이전의 가장 최근 봉."""
    c = _kst(target_date, NQ_NOW_CUTOFF)
    if now is not None and now < c:
        return now
    return c


def nq_change(
    nq: NqSeries, base_date: dt.date, target_date: dt.date, now: dt.datetime | None = None
) -> dict | None:
    """NQ_base/NQ_now를 골라 변화(로그) 계산. 둘 중 하나라도 없으면 None.

    Returns {"base_time","base_value","now_time","now_value","log_change","change_pct","weak"}.
    """
    base = nq.last_at_or_before(nq_base_cutoff(base_date))
    cur = nq.last_at_or_before(nq_now_cutoff(target_date, now))
    if base is None or cur is None:
        return None
    x = math.log(cur[1] / base[1])
    pct = (math.exp(x) - 1) * 100
    return {
        "base_time": base[0],
        "base_value": base[1],
        "now_time": cur[0],
        "now_value": cur[1],
        "log_change": x,
        "change_pct": pct,
        "weak": is_weak(x),
    }


def is_weak(log_change: float) -> bool:
    """|NQ 변화| < 0.5% (단순 변화율 기준)."""
    return abs((math.exp(log_change) - 1) * 100) < WEAK_NQ_PCT


# -- 표본 구성 ----------------------------------------------------------------------


@dataclass(frozen=True)
class Sample:
    date: dt.date  # 대상 거래일 d
    x: float  # NQ 변화(로그)
    y: float  # 갭(로그)


def build_samples(
    nq: NqSeries,
    closes: dict[dt.date, float],
    opens: dict[dt.date, float],
    closed: Callable[[dt.date], bool] = is_krx_closed,
) -> list[Sample]:
    """한 종목의 (NQ 변화, 갭) 표본 목록(날짜 오름차순).

    closes: 날짜 -> KRX 정규장 종가, opens: 날짜 -> NXT 08:00 시가. 거래일 d의 직전 거래일 p는 "closes에 있는
    d보다 앞선 가장 늦은 날"이되, p와 d 사이에 (알려진) 개장일이 끼어 있으면 수집 공백이라 표본에서 뺀다."""
    close_days = sorted(closes)
    out: list[Sample] = []
    for d in sorted(opens):
        i = bisect.bisect_left(close_days, d)
        if i == 0:
            continue
        p = close_days[i - 1]
        if prev_open_day(d, closed) != p:  # p가 진짜 직전 거래일이 아님(공백) 또는 d가 휴장일
            continue
        o, c = opens[d], closes[p]
        if not o or not c or o <= 0 or c <= 0:
            continue
        ch = nq_change(nq, p, d)
        if ch is None:
            continue
        out.append(Sample(date=d, x=ch["log_change"], y=math.log(o / c)))
    return out


# -- OLS ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fit:
    n: int
    alpha: float
    beta: float
    resid_std: float  # sqrt(SSE/(n-2))
    resid_mad: float  # |잔차|의 중앙값


def fit_ols(xs: Sequence[float], ys: Sequence[float]) -> Fit | None:
    """단순 선형 OLS. 표본 3개 미만이거나 x 분산이 0이면 None."""
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 1e-18:
        return None
    beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    alpha = my - beta * mx
    resid = [y - (alpha + beta * x) for x, y in zip(xs, ys)]
    std = math.sqrt(sum(r * r for r in resid) / (n - 2))
    mad = statistics.median(abs(r) for r in resid)
    return Fit(n=n, alpha=alpha, beta=beta, resid_std=std, resid_mad=mad)


def fit_for_target(
    samples: Sequence[Sample],
    target_date: dt.date,
    window: int = WINDOW_DAYS,
    min_n: int = MIN_SAMPLES,
) -> Fit | None:
    """target_date **이전** 표본 중 최근 window개로 적합(대상일 포함 금지). min_n 미만이면 None."""
    pool = sorted((s for s in samples if s.date < target_date), key=lambda s: s.date)[-window:]
    if len(pool) < min_n:
        return None
    return fit_ols([s.x for s in pool], [s.y for s in pool])


# -- 예측 ---------------------------------------------------------------------------


def _pct(log_ret: float) -> float:
    return (math.exp(log_ret) - 1) * 100


def predict(fit: Fit, x: float, base_close: float) -> dict:
    """적합 결과 + NQ 변화(로그) + 기준 종가 -> 예측 갭·범위·예상 시가. 퍼센트는 단순 변화율 %."""
    g = fit.alpha + fit.beta * x
    return {
        "log_gap": g,
        "expected_gap_pct": _pct(g),
        "expected_low_pct": _pct(g - fit.resid_std),
        "expected_high_pct": _pct(g + fit.resid_std),
        "expected_price": int(round(base_close * math.exp(g))),
    }


def fit_stats_pct(fit: Fit) -> dict:
    """fit의 alpha/잔차 통계를 % 단위로(로그값x100 근사가 아니라 단순 변화율 변환)."""
    return {
        "n": fit.n,
        "alpha_pct": _pct(fit.alpha),
        "beta": fit.beta,
        "resid_std_pct": _pct(fit.resid_std),
        "resid_mad_pct": _pct(fit.resid_mad),
    }


def outlook_for_stock(
    samples: Sequence[Sample],
    target_date: dt.date,
    x: float | None,
    base_close: float | None,
) -> dict:
    """한 종목의 예측 한 건. status: ok | insufficient | no_data.
    no_data = NQ 변화 또는 기준 종가가 없음, insufficient = 적합 표본 부족/퇴화."""
    if x is None or not base_close:
        return {"status": "no_data"}
    fit = fit_for_target(samples, target_date)
    if fit is None:
        n = len([s for s in samples if s.date < target_date][-WINDOW_DAYS:])
        return {"status": "insufficient", "n": n}
    return {"status": "ok", **fit_stats_pct(fit), **predict(fit, x, base_close)}


# -- 워크포워드 검증 -----------------------------------------------------------------


def walk_forward(
    samples: Sequence[Sample], window: int = WINDOW_DAYS, min_n: int = MIN_SAMPLES
) -> list[dict]:
    """각 표본일 d를 d 이전 데이터만으로 적합해 예측(최소 min_n일 확보 후부터). 행 목록."""
    ordered = sorted(samples, key=lambda s: s.date)
    rows = []
    for s in ordered:
        fit = fit_for_target(ordered, s.date, window, min_n)
        if fit is None:
            continue
        yhat = fit.alpha + fit.beta * s.x
        rows.append(
            {
                "date": s.date,
                "nq_pct": _pct(s.x),
                "actual_pct": _pct(s.y),
                "pred_pct": _pct(yhat),
                "error_pct": _pct(s.y) - _pct(yhat),
            }
        )
    return rows


def _direction_stats(rows: Sequence[dict], min_abs_nq: float) -> dict:
    sub = [r for r in rows if abs(r["nq_pct"]) >= min_abs_nq]
    if not sub:
        return {"n": 0, "hit_rate": None}
    hit = sum(1 for r in sub if r["pred_pct"] * r["actual_pct"] > 0)
    return {"n": len(sub), "hit_rate": hit / len(sub)}


def walk_forward_summary(rows: Sequence[dict]) -> dict:
    """워크포워드 행 -> 요약. 오차는 실제-예측(%p). 기준선 = 항상 갭 0으로 예측(MAE = 평균 |실제 갭|)."""
    if not rows:
        return {"n": 0}
    errs = [abs(r["error_pct"]) for r in rows]
    base = [abs(r["actual_pct"]) for r in rows]
    return {
        "n": len(rows),
        "mae_pct": sum(errs) / len(errs),
        "median_abs_error_pct": statistics.median(errs),
        "baseline_mae_pct": sum(base) / len(base),
        "direction_all": _direction_stats(rows, 0.0),
        "direction_nq_ge_0_5": _direction_stats(rows, 0.5),
        "direction_nq_ge_1": _direction_stats(rows, 1.0),
    }
