"""지표 성적표 — 대시보드 신호들을 같은 형식으로 채점하는 순수 계산부 (PLAN.md §5.109).

**표시·관찰 전용.** 매매 지시나 자동매매(킬스위치·실거래 규칙)와 연결하지 않는다. DB·네트워크 없음 —
입력 적재는 `routers/scorecard.py`가 한다.

공통 채점 규칙(모든 신호 동일):
- 행(row) = ``{"date", "group", "outcome", "hit"}``. ``outcome``은 결과 수치(수익률 등), ``hit``은 적중 여부
  (True/False) 또는 None(적중 판정이 없는 그룹 — n과 평균만 본다). ``outcome``이 None이면 결과가 아직 없는 날이라
  표본에서 뺀다(`drop_pending`).
- 그룹별: n, hits, hit_rate(%), baseline_rate(%, 신호와 무관하게 같은 기간·같은 결과 정의에서 그 결과가 나온 비율),
  edge(hit_rate - baseline_rate, %p), avg_outcome, recent(최근 20회), status(ok | insufficient: n < 30).
- **워크포워드**: 날짜 t의 판정·임계값·버킷 통계·회귀계수는 t 이전 데이터로만 정한다. 결과(outcome)만 t 이후를 쓴다.
  그 보증은 각 `walkforward_*` 함수가 진다(미래 누출 방지 테스트는 tests/test_scorecard.py).
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from typing import Sequence

from .opening_vol import day_features, percentile_rank, regime_label
from .premarket_outlook import WEAK_NQ_PCT, Sample, fit_for_target
from .regime_backtest import bucket_label, next_streak

MIN_N = 30  # 이 미만이면 status = insufficient
RECENT_N = 20  # 최근 몇 회를 따로 볼지
REGIME_WARMUP_DAYS = 250  # 시장 우세 판정: 첫 N거래일은 판정만 만들고 채점 제외
VOL_MIN_PRIOR_DAYS = 20  # 변동성 국면: 임계값·분포를 정하려면 최소 이만큼의 이전 거래일
VOL_OPEN_WINDOW_MIN = 30  # 초반 구간(09:00~09:30). 결과는 09:30 이후 -> 겹치지 않는다
RANGE_THEORY_RATE = 68.0  # 범위 적중의 이론값(예상 ± 1표준편차, 정규 가정 약 68%)
CLOSE_BAR_MIN = 15 * 60 + 19  # 마지막 봉이 이 시각 이후여야 '하루가 끝난' 것으로 본다


# -- 공통 채점 -------------------------------------------------------------------------


def _r(x: float | None, nd: int = 1) -> float | None:
    return None if x is None else round(x, nd)


def drop_pending(rows: Sequence[dict]) -> list[dict]:
    """결과(outcome)가 아직 없는 행(다음 날이 아직 안 온 최근 날짜 등)을 표본에서 뺀다."""
    return [r for r in rows if r.get("outcome") is not None]


def rate(flags: Sequence[bool]) -> float | None:
    """True 비율(%). 비어 있으면 None."""
    return None if not flags else sum(1 for f in flags if f) / len(flags) * 100


def _recent(rows: Sequence[dict], scored: bool) -> dict:
    tail = list(rows)[-RECENT_N:]
    if not scored:
        return {"n": len(tail), "hits": None, "hit_rate": None}
    hits = sum(1 for r in tail if r["hit"])
    return {"n": len(tail), "hits": hits, "hit_rate": _r(hits / len(tail) * 100) if tail else None}


def score_group(
    rows: Sequence[dict],
    baseline_rate: float | None,
    key: str,
    label: str,
    rule: str,
    scored: bool = True,
    outcome_label: str | None = None,
) -> dict:
    """한 그룹의 채점 결과. rows는 이미 이 그룹 행만, 날짜 오름차순(최근 20회는 끝에서 센다)."""
    n = len(rows)
    out_vals = [r["outcome"] for r in rows if r.get("outcome") is not None]
    avg = _r(sum(out_vals) / len(out_vals), 3) if out_vals else None
    g = {
        "key": key,
        "label": label,
        "rule": rule,
        "n": n,
        "hits": None,
        "hit_rate": None,
        "baseline_rate": None,
        "edge": None,
        "avg_outcome": avg,
        "avg_outcome_label": outcome_label,
        "recent": _recent(rows, scored),
        "status": "ok" if n >= MIN_N else "insufficient",
    }
    if scored:
        hits = sum(1 for r in rows if r["hit"])
        hr = hits / n * 100 if n else None
        g["hits"] = hits
        g["hit_rate"] = _r(hr)
        g["baseline_rate"] = _r(baseline_rate)
        g["edge"] = _r(hr - baseline_rate) if (hr is not None and baseline_rate is not None) else None
    return g


def build_signal(
    key: str,
    label: str,
    claim: str,
    outcome: str,
    rows: Sequence[dict],
    group_defs: Sequence[dict],
    baselines: dict[str, float | None],
    notes: Sequence[str] = (),
    outcome_label: str | None = None,
) -> dict:
    """행 -> 응답 한 신호분. 결과 없는 행은 여기서 한 번 더 걸러낸다(이중 안전).
    group_defs: [{"key","label","rule","scored"(기본 True)}]. period = 표본 행의 시작·종료일과 서로 다른 날짜 수."""
    rows = sorted(drop_pending(rows), key=lambda r: r["date"])  # 안정 정렬: 같은 날 안의 순서 유지
    groups = []
    for gd in group_defs:
        grows = [r for r in rows if r["group"] == gd["key"]]
        groups.append(
            score_group(
                grows,
                baselines.get(gd["key"]),
                gd["key"],
                gd["label"],
                gd["rule"],
                scored=gd.get("scored", True),
                outcome_label=outcome_label,
            )
        )
    dates = sorted({r["date"] for r in rows})
    return {
        "key": key,
        "label": label,
        "claim": claim,
        "outcome": outcome,
        "period": {
            "start": dates[0].isoformat() if dates else None,
            "end": dates[-1].isoformat() if dates else None,
            "trading_days": len(dates),
        },
        "groups": groups,
        "notes": list(notes),
        "error": None,
    }


def error_signal(key: str, label: str, claim: str, outcome: str, reason: str) -> dict:
    """계산 실패한 신호 자리 — 나머지 신호는 그대로 내려가고 이 신호만 error에 사유를 담는다."""
    return {
        "key": key,
        "label": label,
        "claim": claim,
        "outcome": outcome,
        "period": {"start": None, "end": None, "trading_days": 0},
        "groups": [],
        "notes": [],
        "error": reason,
    }


# -- 1. 시장 우세 판정 (코스닥 외국인 스트릭) -------------------------------------------------


def next_day_returns(closes: Sequence[tuple[dt.date, float | None]]) -> dict[dt.date, tuple[dt.date, float]]:
    """종가 시계열(날짜 오름차순) -> {날짜 d: (다음 저장 거래일, 다음날 등락률 %)}.
    `regime_backtest._daily_returns`와 같은 규칙(다음 저장 행 사용, 종가 None/0 시작점은 건너뜀)이되 다음 날짜도 함께 담는다."""
    out: dict[dt.date, tuple[dt.date, float]] = {}
    for (d0, c0), (d1, c1) in zip(closes, closes[1:]):
        if c0 is None or c1 is None or float(c0) == 0:
            continue
        out[d0] = (d1, (float(c1) - float(c0)) / float(c0) * 100.0)
    return out


def walkforward_regime(
    flows: Sequence[tuple[dt.date, float | None]],
    closes: Sequence[tuple[dt.date, float | None]],
    warmup: int = REGIME_WARMUP_DAYS,
) -> list[dict]:
    """코스닥 외국인 연속 순매수/매도(스트릭)로 날짜 t의 "우세/중립(+열세 참고)"을 **t 이전 데이터의 버킷 통계로만** 재현한다.

    판정 규칙은 `routers/markets._judge_regime`을 그대로 따른다: 스트릭 >= 2(2일+ 연속 순매수)이고 그 버킷에 과거 표본이
    있으면 "우세", 나머지는 "중립". 기존 판정은 매도 스트릭을 "중립"으로만 두지만, 성적표는 2일+ 연속 매도를
    "열세"(참고)로 따로 떼어 본다(기존 문구 "코스닥에 불리한 신호"를 같은 형식으로 채점해 보기 위함).

    t 시점 버킷 통계에는 "다음 거래일이 t 이전(포함)인" 날 d만 들어간다 = t의 종가까지로 결과가 확정된 과거 표본.
    t 자신의 결과(다음날 등락)는 절대 쓰지 않는다. 첫 `warmup`개 스트릭 거래일은 판정만 만들고 in_warmup=True.

    반환: [{"date","streak","label"("우세"|"열세"|"중립"),"in_warmup","outcome"(다음날 등락률 % 또는 None)}]"""
    rets = next_day_returns(closes)
    streak = 0
    series: list[tuple[dt.date, int]] = []
    for d, net in sorted(flows, key=lambda x: x[0]):
        if net is None:
            continue
        streak = next_streak(streak, net)
        series.append((d, streak))

    buckets: dict[str, list[float]] = {}
    pending: list[tuple[dt.date, str, float]] = []  # (결과 확정일, 버킷, 다음날 등락률) — 확정일이 되면 버킷에 편입
    out: list[dict] = []
    for i, (t, s) in enumerate(series):
        keep = []
        for known_on, lab, ret in pending:
            if known_on <= t:
                buckets.setdefault(lab, []).append(ret)
            else:
                keep.append((known_on, lab, ret))
        pending = keep

        lab_t = bucket_label(s)
        if s >= 2 and lab_t is not None and buckets.get(lab_t):
            label = "우세"
        elif s <= -2 and lab_t is not None and buckets.get(lab_t):
            label = "열세"
        else:
            label = "중립"
        r = rets.get(t)
        out.append(
            {
                "date": t,
                "streak": s,
                "label": label,
                "in_warmup": i < warmup,
                "outcome": r[1] if r else None,
            }
        )
        # t의 결과는 t 다음 거래일 종가가 나와야 확정 -> 그날 이후의 판정부터 통계에 들어간다
        if r and lab_t is not None:
            pending.append((r[0], lab_t, r[1]))
    return out


def build_regime_signal(
    flows: Sequence[tuple[dt.date, float | None]],
    closes: Sequence[tuple[dt.date, float | None]],
    warmup: int = REGIME_WARMUP_DAYS,
) -> dict:
    wf = [j for j in walkforward_regime(flows, closes, warmup) if not j["in_warmup"]]
    scored = drop_pending(wf)
    rows = []
    for j in scored:
        g = j["label"]
        o = j["outcome"]
        hit = (o > 0) if g == "우세" else (o < 0) if g == "열세" else None
        rows.append({"date": j["date"], "group": g, "outcome": o, "hit": hit})
    outs = [j["outcome"] for j in scored]
    baselines = {"우세": rate([o > 0 for o in outs]), "열세": rate([o < 0 for o in outs])}
    return build_signal(
        key="regime",
        label="시장 우세 판정",
        claim="코스닥에서 외국인이 2일 이상 연속 순매수하면 다음 거래일 코스닥이 오를 가능성이 평소보다 높다.",
        outcome="우세 판정일의 다음 거래일 코스닥 지수가 상승하면 적중(열세 참고 그룹은 하락하면 적중). 중립은 n과 평균만 본다.",
        rows=rows,
        group_defs=[
            {"key": "우세", "label": "우세", "rule": "다음 거래일 코스닥 상승"},
            {"key": "열세", "label": "열세(참고)", "rule": "다음 거래일 코스닥 하락"},
            {"key": "중립", "label": "중립", "rule": "판정 없음(n·평균만)", "scored": False},
        ],
        baselines=baselines,
        outcome_label="다음 거래일 코스닥 등락률(%)",
        notes=[
            f"각 날짜의 판정은 그 날짜 이전에 결과가 확정된 날들의 버킷 통계로만 다시 만들었다(표본 내 통계 아님). 첫 {warmup}거래일은 워밍업이라 채점에서 제외.",
            "기준선 = 같은 기간 전체 거래일 중 다음 날 코스닥이 상승(열세는 하락)한 비율.",
        ],
    )


# -- 2. 외인 현·선 방향 --------------------------------------------------------------------


def _sign(v: float | None) -> int | None:
    if v is None:
        return None
    return (v > 0) - (v < 0)


def build_positioning_signal(snapshots: Sequence[dict]) -> dict:
    """positioning_snapshot 행(dict: date, foreign_spot_cum, foreign_futures_cum, next_day_change_rate)을 채점.
    현물·선물 누적이 둘 다 있는 날만 표본. 둘 다 양수=둘 다 매수, 둘 다 음수=둘 다 매도, 그 외(부호 엇갈림·0)=엇갈림.
    신호 자체에 과거로 정하는 값이 없어(부호만 본다) 워크포워드 문제는 없다."""
    rows = []
    for s in sorted(snapshots, key=lambda x: x["date"]):
        sp, fu = _sign(s.get("foreign_spot_cum")), _sign(s.get("foreign_futures_cum"))
        if sp is None or fu is None:
            continue
        g = "both_buy" if (sp > 0 and fu > 0) else "both_sell" if (sp < 0 and fu < 0) else "mixed"
        o = s.get("next_day_change_rate")
        o = None if o is None else float(o)
        hit = None if o is None else (o > 0) if g == "both_buy" else (o < 0) if g == "both_sell" else None
        rows.append({"date": s["date"], "group": g, "outcome": o, "hit": hit})
    known = [r["outcome"] for r in drop_pending(rows)]
    return build_signal(
        key="positioning",
        label="외인 현·선 방향",
        claim="외국인이 현물과 선물을 같은 방향으로 누적 매매하면 다음 거래일 SK하이닉스가 그 방향으로 움직인다.",
        outcome="둘 다 매수인 날은 다음 거래일 SK하이닉스(000660)가 상승하면, 둘 다 매도인 날은 하락하면 적중. 엇갈림은 n과 평균만 본다.",
        rows=rows,
        group_defs=[
            {"key": "both_buy", "label": "둘 다 매수", "rule": "다음 거래일 SK하이닉스 상승"},
            {"key": "both_sell", "label": "둘 다 매도", "rule": "다음 거래일 SK하이닉스 하락"},
            {"key": "mixed", "label": "엇갈림", "rule": "n·평균만", "scored": False},
        ],
        baselines={"both_buy": rate([o > 0 for o in known]), "both_sell": rate([o < 0 for o in known])},
        outcome_label="다음 거래일 SK하이닉스 등락률(%)",
        notes=["기준선 = 신호 값이 있는 전체 날짜 중 다음 날 SK하이닉스가 상승(매도 그룹은 하락)한 비율."],
    )


# -- 3. 프리마켓 예상 ----------------------------------------------------------------------


def _pct(log_ret: float) -> float:
    return (math.exp(log_ret) - 1) * 100


def walkforward_premarket(samples_by_code: dict[str, Sequence[Sample]]) -> list[dict]:
    """종목별 표본에서, 각 날짜를 **그 날짜 이전 표본으로만 적합한**(`fit_for_target`) 예상과 실제 갭을 짝짓는다.
    적합 불가(표본 20일 미만) 날은 건너뛴다. 반환 행: date, code, nq_pct, pred_pct, actual_pct, error_pct(실제-예상), within(1σ 안)."""
    rows: list[dict] = []
    for code, samples in samples_by_code.items():
        ordered = sorted(samples, key=lambda s: s.date)
        for s in ordered:
            fit = fit_for_target(ordered, s.date)
            if fit is None:
                continue
            yhat = fit.alpha + fit.beta * s.x
            rows.append(
                {
                    "date": s.date,
                    "code": code,
                    "nq_pct": _pct(s.x),
                    "pred_pct": _pct(yhat),
                    "actual_pct": _pct(s.y),
                    "error_pct": _pct(s.y) - _pct(yhat),
                    "within": abs(s.y - yhat) <= fit.resid_std,
                }
            )
    rows.sort(key=lambda r: (r["date"], r["code"]))
    return rows


def build_premarket_signal(samples_by_code: dict[str, Sequence[Sample]], logged: dict | None = None) -> dict:
    wf = walkforward_premarket(samples_by_code)
    strong = [r for r in wf if abs(r["nq_pct"]) >= WEAK_NQ_PCT]
    rows = []
    for r in strong:
        rows.append(
            {
                "date": r["date"],
                "group": "direction",
                "outcome": abs(r["error_pct"]),
                "hit": r["pred_pct"] * r["actual_pct"] > 0,
            }
        )
    for r in wf:
        rows.append({"date": r["date"], "group": "range", "outcome": abs(r["error_pct"]), "hit": r["within"]})

    def mae(rs: Sequence[dict], zero: bool) -> float | None:
        vals = [abs(r["actual_pct"]) if zero else abs(r["error_pct"]) for r in rs]
        return sum(vals) / len(vals) if vals else None

    pos, neg = rate([r["actual_pct"] > 0 for r in strong]), rate([r["actual_pct"] < 0 for r in strong])
    base_dir = max(pos, neg) if pos is not None and neg is not None else None
    notes = [
        "5종목(삼성전자·SK하이닉스·현대차·NAVER·LG에너지솔루션)의 종목-일 표본을 합산했다. 같은 날 종목들은 서로 독립이 아니라 실질 표본은 n보다 적다.",
        "각 날짜는 그 날짜 이전 최근 60거래일 표본으로만 적합해 재계산했다(유효 표본 20일 미만이면 제외). 방향 기준선 = 같은 날들에서 실제 갭이 양수였던 비율과 음수였던 비율 중 큰 쪽.",
        "범위 그룹의 baseline_rate는 이론값 68%(예상 ± 1표준편차, 정규 가정).",
        "avg_outcome = 예상과 실제 갭의 평균 절대오차(%p).",
        f"예상을 0으로 뒀을 때의 평균 절대오차: 방향 그룹 표본 { _r(mae(strong, True), 3) }%p(예상 { _r(mae(strong, False), 3) }%p), "
        f"범위 그룹 표본 { _r(mae(wf, True), 3) }%p(예상 { _r(mae(wf, False), 3) }%p).",
    ]
    if logged is not None:
        notes.append(
            f"실제 기록(premarket_outlook_log): {logged.get('rows', 0)}건, {logged.get('days', 0)}거래일"
            + (f"({logged['start']} ~ {logged['end']})" if logged.get("start") else "")
            + ". 성적표는 이 기록이 아니라 같은 모델을 과거 전 기간에 워크포워드로 재계산한 값이다."
        )
    return build_signal(
        key="premarket",
        label="프리마켓 예상",
        claim="밤사이 나스닥선물 변화로 NXT 08:00 시가의 갭(전일 정규장 종가 대비)을 예상할 수 있다.",
        outcome="방향: NQ가 0.5% 이상 움직인 날 예상 갭과 실제 갭의 부호가 같으면 적중. 범위: 실제 갭이 예상 ± 1표준편차 안이면 적중.",
        rows=rows,
        group_defs=[
            {"key": "direction", "label": "방향", "rule": "|NQ 변화| >= 0.5%인 날 예상·실제 갭 부호 일치"},
            {"key": "range", "label": "범위", "rule": "실제 갭이 예상 ± 1표준편차 안"},
        ],
        baselines={"direction": base_dir, "range": RANGE_THEORY_RATE},
        outcome_label="예상 갭 절대오차(%p)",
        notes=notes,
    )


# -- 4. 변동성 국면 ------------------------------------------------------------------------


def vol_day_stats(bars: Sequence[dict], daily: dict | None, n: int = VOL_OPEN_WINDOW_MIN) -> dict | None:
    """한 거래일 분봉 -> {rv, rest_rng}. rv = 초반 n분 실현변동성(기존 `opening_vol.day_features` strict 정의),
    rest_rng = n분 **이후**~장 마감 구간의 (고가-저가)/전일종가 %. 초반(m < 09:00+n)과 결과(m >= 09:00+n)는 겹치지 않는다.
    분봉이 마감 근처(15:19)까지 오지 않았으면 결과는 아직 미확정(rest_rng=None). 정의 불가한 날은 None."""
    f = day_features(bars, daily, strict=True)
    if not f or not f.get(f"ok{n}"):
        return None
    complete = bool(bars) and bars[-1]["m"] >= CLOSE_BAR_MIN
    return {"rv": f[f"rv{n}"], "rest_rng": f[f"rest_rng{n}"] if complete else None}


def walkforward_vol(days: Sequence[dict], min_prior: int = VOL_MIN_PRIOR_DAYS) -> list[dict]:
    """days = [{"date","rv","rest_rng"(None이면 결과 미확정)}] 날짜 오름차순.
    날짜 t의 라벨은 t **이전** 거래일들의 rv 분포 백분위로(`opening_vol`의 기존 정의: <40 calm, >=80 high), 비교 기준(중앙값)은
    t 이전 거래일들의 rest_rng 중앙값으로 정한다. 이전 표본이 min_prior 미만이면 판정하지 않는다(워밍업).
    반환: [{"date","label"(high|calm|normal),"pct","rest_rng","median","above"(bool|None)}]"""
    ordered = sorted(days, key=lambda d: d["date"])
    out = []
    for i, d in enumerate(ordered):
        prior = [p for p in ordered[:i] if p["rv"] is not None and p["rest_rng"] is not None]
        if len(prior) < min_prior:
            continue
        pct = percentile_rank(d["rv"], [p["rv"] for p in prior])
        med = statistics.median(p["rest_rng"] for p in prior)
        rr = d["rest_rng"]
        out.append(
            {
                "date": d["date"],
                "label": regime_label(pct),
                "pct": pct,
                "rest_rng": rr,
                "median": med,
                "above": None if rr is None else rr > med,
            }
        )
    return out


def build_vol_signal(days: Sequence[dict], min_prior: int = VOL_MIN_PRIOR_DAYS) -> dict:
    wf = walkforward_vol(days, min_prior)
    rows = []
    for j in wf:
        g = j["label"]
        if g is None:
            continue
        rr = j["rest_rng"]
        hit = None if rr is None else (rr > j["median"]) if g == "high" else (rr < j["median"]) if g == "calm" else None
        rows.append({"date": j["date"], "group": g, "outcome": rr, "hit": hit})
    known = [j for j in wf if j["rest_rng"] is not None]
    baselines = {
        "high": rate([j["rest_rng"] > j["median"] for j in known]),
        "calm": rate([j["rest_rng"] < j["median"] for j in known]),
    }
    return build_signal(
        key="vol_regime",
        label="변동성 국면",
        claim="장 초반 30분 실현변동성이 높은 날은 그 이후 하루 변동 폭이 크고, 낮은 날은 작다.",
        outcome="09:30 이후 당일 고가-저가 폭(%)이 그 이전 거래일들의 중앙값보다 크면(높음) / 작으면(낮음) 적중. 보통은 n·평균만 본다.",
        rows=rows,
        group_defs=[
            {"key": "high", "label": "높음", "rule": "초반 rv 백분위 >= 80 → 09:30 이후 폭이 이전 중앙값보다 큼"},
            {"key": "calm", "label": "낮음", "rule": "초반 rv 백분위 < 40 → 09:30 이후 폭이 이전 중앙값보다 작음"},
            {"key": "normal", "label": "보통", "rule": "n·평균만", "scored": False},
        ],
        baselines=baselines,
        outcome_label="09:30 이후 당일 고가-저가 폭(전일종가 대비 %)",
        notes=[
            f"라벨 기준은 기존 vol-regime 정의(초반 30분 rv의 과거 분포 백분위: <40 낮음, >=80 높음). 분포와 중앙값은 각 날짜 이전 거래일로만 정했고, 이전 표본이 {min_prior}일 미만인 초기 날짜는 판정하지 않았다.",
            "초반 구간(09:00~09:30)과 결과 구간(09:30 이후)은 겹치지 않는다.",
            "기준선 = 판정 가능한 전체 날짜 중 이후 폭이 이전 중앙값보다 컸던(낮음 그룹은 작았던) 비율.",
        ],
    )


# -- 5. 위험 경보 --------------------------------------------------------------------------


def build_risk_signal(snapshots: Sequence[dict]) -> dict:
    """positioning_snapshot 행(dict: date, risk_alert_count, same_day_remaining_change_rate, next_day_change_rate).
    경보일(risk_alert_count > 0) 두 그룹: 당일 남은 시간 수익률 음수 / 다음 날 수익률 음수. 기준선 = 전체 날짜에서 그 수익률이 음수였던 비율."""
    snaps = sorted(snapshots, key=lambda x: x["date"])
    rows = []
    for s in snaps:
        if (s.get("risk_alert_count") or 0) <= 0:
            continue
        for g, col in (("same_day", "same_day_remaining_change_rate"), ("next_day", "next_day_change_rate")):
            v = s.get(col)
            v = None if v is None else float(v)
            rows.append({"date": s["date"], "group": g, "outcome": v, "hit": None if v is None else v < 0})

    def neg_rate(col: str) -> float | None:
        vals = [float(s[col]) for s in snaps if s.get(col) is not None]
        return rate([v < 0 for v in vals])

    return build_signal(
        key="risk_alert",
        label="위험 경보",
        claim="위험 경보가 켜진 날은 SK하이닉스가 하락할 가능성이 평소보다 높다.",
        outcome="경보일의 스냅샷 이후 당일 남은 시간 SK하이닉스 수익률이 음수이면(당일) / 다음 거래일 수익률이 음수이면(다음 날) 적중.",
        rows=rows,
        group_defs=[
            {"key": "same_day", "label": "경보 후 당일", "rule": "스냅샷 이후 당일 남은 시간 수익률 < 0"},
            {"key": "next_day", "label": "경보 후 다음 날", "rule": "다음 거래일 수익률 < 0"},
        ],
        baselines={"same_day": neg_rate("same_day_remaining_change_rate"), "next_day": neg_rate("next_day_change_rate")},
        outcome_label="SK하이닉스 수익률(%)",
        notes=["기준선 = 경보 여부와 무관하게 전체 스냅샷 날짜에서 그 수익률이 음수였던 비율."],
    )


# -- 6. 스켈핑 후보 (기존 집계를 같은 그룹 형식으로 변환) ------------------------------------------


def build_scalp_signal(agg: dict, recent_by_horizon: dict[str, list[float]]) -> dict:
    """`scalp_hitrate.aggregate_scalp_hitrate` 결과를 그룹 형식으로 변환만 한다(새 집계 없음).
    recent_by_horizon = 호라이즌 -> 진입 시각 오름차순 값 리스트(최근 20회용)."""
    labels = {"5m": "5분 후", "15m": "15분 후", "30m": "30분 후", "60m": "60분 후", "eod": "당일 마감"}
    groups = []
    for hz, st in agg["horizons"].items():
        n = st["n"]
        wr = st["win_rate"]
        hits = round(n * wr / 100) if (n and wr is not None) else None
        tail = recent_by_horizon.get(hz, [])[-RECENT_N:]
        th = sum(1 for v in tail if v > 0)
        groups.append(
            {
                "key": hz,
                "label": labels.get(hz, hz),
                "rule": f"{labels.get(hz, hz)} 등락률 > 0",
                "n": n,
                "hits": hits,
                "hit_rate": wr,
                "baseline_rate": None,
                "edge": None,
                "avg_outcome": st["avg_change_rate"],
                "avg_outcome_label": "해당 시점 등락률(%)",
                "recent": {"n": len(tail), "hits": th, "hit_rate": _r(th / len(tail) * 100) if tail else None},
                "status": "ok" if n >= MIN_N else "insufficient",
            }
        )
    return {
        "key": "scalp",
        "label": "스켈핑 후보",
        "claim": "스켈핑 후보 스코어 상위에 처음 오른 종목은 이후에도 강세를 유지한다.",
        "outcome": "후보 진입 후 5/15/30/60분·당일 마감 시점의 종목 등락률이 0보다 크면 적중(기존 scalp_hitrate 집계의 승률 정의 그대로).",
        "period": {"start": agg.get("date_from"), "end": agg.get("date_to"), "trading_days": agg.get("distinct_days", 0)},
        "groups": groups,
        "notes": [
            "기존 집계(quant/scalp_hitrate.py)를 그대로 변환했다. 기준선 집계가 없어 baseline_rate·edge는 null.",
            "기록되는 등락률은 진입가 대비 수익률이 아니라 그 시점의 전일 종가 대비 등락률(진입 시점에 이미 오른 종목이라 0 초과가 기본적으로 유리한 정의).",
            "n은 종목-일 표본이다(하루 최대 10종목, 같은 날 종목끼리는 시장 흐름을 공유).",
        ],
        "error": None,
    }
