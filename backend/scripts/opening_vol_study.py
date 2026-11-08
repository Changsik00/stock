"""시초 정보(갭 + 장 초반 N분 변동성)로 '그날 하루 변동성'을 예측할 수 있는가 (PLAN.md §5.94, 읽기 전용).

§5.92는 일봉만 있어 '시초 N분'을 직접 못 쟀다. §5.93이 쌓은 지수 1분봉(index_intraday_bar: 001 코스피/101 코스닥)과
ETF 1분봉(stock_intraday_bar)으로 재검증한다. **실거래 규칙/킬스위치/수집기는 건드리지 않는다(조회·오프라인 계산뿐).**

섹션
 0) 데이터 점검: 첫 봉 09:00 여부, 결측 분, 일봉(index_ohlcv)과의 시가/종가 일치, 구멍(30분 결측) 일수
 1) 예측력: 결과변수(당일 나머지 구간 레인지 / 실현변동성 / 당일 레인지 ÷ 직전20일 평균 레인지) ~ 갭만 vs 갭+초반 N분 변동성
    (로그-로그 OLS R², 조정 R², 시간순 10분할 교차검증 R², Spearman, 상위20% vs 나머지, 블록 부트스트랩 CI)
 2) ETF 트레일 성과: 초반 N분 변동성 상위 vs 나머지에 진입한 거래를 baseline 0.5 / 변동성비례(k=4,N=60) / 고정 1.0으로 비교
 3) 진입 휴식 가정: 시초 N분 신규 진입을 쉬면? (갭 기준 = 09:00에 알 수 있어 실행 가능 / 초반변동성 기준 = 사후 라벨, 기술용)
 4) 사례: 2026-11-05(코스피 -5%대 급락일), 2026-11-06(당일) 판정

통계: 인접일 군집을 감안해 이동 블록 부트스트랩(블록 5일, 5000회, seed 고정) 95% CI. 다중비교(N 4개 x 시장 2 x 결과변수 3 x 모델 6 ...)는
보정하지 않는다 — 출력의 '비교 횟수'를 참고. 단일 국면(2026-06~11, 급등락) 표본이라 일반화 금지.

Usage:
    docker exec stock-backend-1 python -m scripts.opening_vol_study
    docker exec stock-backend-1 python -m scripts.opening_vol_study --no-trail   # 1)만 (빠름)
"""

import argparse
import warnings
import asyncio
import datetime as dt
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.collectors.auto_trader import (  # noqa: E402
    ENTRY_SIGNAL_LOOKBACK_BARS,
    _golden_cross_in_lookback,
    _volume_spike_in_lookback,
)
from app.db import async_session_factory  # noqa: E402
from app.quant.auto_trade_rules import decide_idle_action  # noqa: E402
from app.quant.nq_trail_backtest import (  # noqa: E402
    add_ts,
    fixed_giveback,
    run_giveback_backtest,
    vol_giveback,
)
from app.quant.opening_vol import (  # noqa: E402
    MIN_BARS,
    NS,
    OPEN_MIN,
    auction_gap,
    build_daily_context,
    day_features,
    hhmm_to_min,
    log_returns,
)
from app.quant.signals import moving_average_cross, volume_spike  # noqa: E402

BOOT_N = 5000
BLOCK = 5
TOP_Q = 0.8  # 상위 20%
TRAIL_TOP_Q = 0.7  # 트레일 비교는 표본 확보를 위해 상위 30%
INDEX = {"kospi": "001", "kosdaq": "101"}
ETFS = {"069500": "kospi", "229200": "kosdaq", "0167A0": "kospi"}  # 0167A0 기초는 반도체 -> 코스피 근사(참고용)
rng = np.random.default_rng(20261106)
warnings.filterwarnings("ignore", category=RuntimeWarning)  # 부트스트랩 퇴화 표본(빈 슬라이스) 경고 억제


_m = hhmm_to_min


# ---------------------------------------------------------------- 로딩


async def load_index_bars(code: str) -> dict[dt.date, list[dict]]:
    sql = text("select date,time,open,high,low,close,volume from index_intraday_bar where code=:c order by date,time")
    return await _bars(sql, {"c": code})


async def load_etf_bars(code: str) -> dict[dt.date, list[dict]]:
    sql = text("select date,time,open,high,low,close,volume from stock_intraday_bar where code=:c order by date,time")
    return await _bars(sql, {"c": code})


async def _bars(sql, params) -> dict[dt.date, list[dict]]:
    async with async_session_factory() as s:
        rows = (await s.execute(sql, params)).all()
    out: dict[dt.date, list[dict]] = defaultdict(list)
    for d, t, o, h, l, c, v in rows:
        if None in (o, h, l, c):
            continue
        out[d].append({"time": t, "m": _m(t), "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": v or 0})
    return out


async def load_daily(market: str) -> dict[dt.date, dict]:
    sql = text("select date,open,high,low,close from index_ohlcv where market=:m order by date")
    async with async_session_factory() as s:
        rows = (await s.execute(sql, {"m": market})).all()
    return build_daily_context(rows)


# ---------------------------------------------------------------- 피처


# 공통 정의는 app/quant/opening_vol.py (대시보드 /api/markets/vol-regime과 공유, PLAN.md §5.95)
_rets = log_returns
_auction_gap = auction_gap


# ---------------------------------------------------------------- 통계


def ranks(a):
    order = np.argsort(a, kind="mergesort")
    r = np.empty(len(a))
    r[order] = np.arange(len(a))
    # 동순위 평균
    for v in np.unique(a):
        idx = np.where(a == v)[0]
        if len(idx) > 1:
            r[idx] = r[idx].mean()
    return r


def spearman(x, y):
    if len(x) < 5:
        return None
    rx, ry = ranks(np.asarray(x)), ranks(np.asarray(y))
    if rx.std() == 0 or ry.std() == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def block_idx(n: int) -> np.ndarray:
    """이동(원형) 블록 부트스트랩 인덱스."""
    nb = math.ceil(n / BLOCK)
    starts = rng.integers(0, n, nb)
    return np.concatenate([(s + np.arange(BLOCK)) % n for s in starts])[:n]


def boot(stat_fn, n: int, reps: int = BOOT_N):
    vals = []
    for _ in range(reps):
        try:
            v = stat_fn(block_idx(n))
        except Exception:  # noqa: BLE001 - 퇴화 표본
            continue
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            vals.append(v)
    if len(vals) < reps * 0.8:
        return None
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def ols(X, y):
    X1 = np.column_stack([np.ones(len(y)), X])
    beta, *_ = np.linalg.lstsq(X1, y, rcond=None)
    res = y - X1 @ beta
    sst = ((y - y.mean()) ** 2).sum()
    r2 = 1 - (res**2).sum() / sst if sst > 0 else float("nan")
    k = X1.shape[1] - 1
    adj = 1 - (1 - r2) * (len(y) - 1) / (len(y) - k - 1)
    return beta, r2, adj


def cv_se(X, y, folds: int = 10):
    """시간순 연속 folds분할 교차검증의 일별 제곱오차(모델)와 기준(훈련평균) 제곱오차."""
    n = len(y)
    se = np.empty(n)
    se0 = np.empty(n)
    edges = np.linspace(0, n, folds + 1).astype(int)
    for a, b in zip(edges[:-1], edges[1:]):
        te = np.arange(a, b)
        tr = np.setdiff1d(np.arange(n), te)
        X1 = np.column_stack([np.ones(len(tr)), X[tr]])
        beta, *_ = np.linalg.lstsq(X1, y[tr], rcond=None)
        pred = np.column_stack([np.ones(len(te)), X[te]]) @ beta
        se[te] = (y[te] - pred) ** 2
        se0[te] = (y[te] - y[tr].mean()) ** 2
    return se, se0


def lg(v, eps=0.0):
    return np.log(np.asarray(v, dtype=float) + eps)


def f2(v, d=2):
    return "-" if v is None else f"{v:.{d}f}"


def ci_s(ci, d=2):
    return "-" if ci is None else f"[{ci[0]:.{d}f},{ci[1]:.{d}f}]"


# ---------------------------------------------------------------- 0) 데이터 점검


async def qc(name, bars_by_day, daily):
    days = sorted(bars_by_day)
    first_ok = sum(1 for d in days if bars_by_day[d][0]["m"] == OPEN_MIN)
    full = [d for d in days if len(bars_by_day[d]) >= MIN_BARS and bars_by_day[d][0]["m"] == OPEN_MIN]
    holes = []
    for d in full:
        ms = [b["m"] for b in bars_by_day[d]]
        # 15:20 -> 15:30은 종가 단일가(정상)이므로 제외하고 센다
        gaps = [(a, b) for a, b in zip(ms, ms[1:]) if b - a > 1 and not _auction_gap(a, b) and a < 15 * 60 + 30]
        if gaps:
            holes.append((d, sum(b - a - 1 for a, b in gaps), gaps[0][0] // 60 * 100 + gaps[0][0] % 60))
    mism_o = mism_c = chk = 0
    if daily:
        for d in days:
            if d in daily and bars_by_day[d][0]["m"] == OPEN_MIN:
                chk += 1
                if abs(bars_by_day[d][0]["o"] / daily[d]["o"] - 1) > 0.002:
                    mism_o += 1
                if abs(bars_by_day[d][-1]["c"] / daily[d]["c"] - 1) > 0.002:
                    mism_c += 1
    print(f"  {name}: 일수 {len(days)} ({days[0]}~{days[-1]}), 첫봉 09:00인 날 {first_ok}, 사용 가능(>= {MIN_BARS}봉 & 09:00) {len(full)}, "
          f"봉수 {sum(len(v) for v in bars_by_day.values())}")
    print(f"    30분 안팎 중간 결측이 있는 날 {len(holes)}일 (첫 결측 시각 HHMM): {[(str(d), miss_min, hhmm) for d, miss_min, hhmm in holes[:12]]}")
    dropped = [str(d) for d in days if d not in full]
    print(f"    제외일: {dropped}")
    if daily:
        print(f"    일봉(index_ohlcv) 대조 {chk}일: 시가 불일치(>0.2%) {mism_o}, 종가 불일치 {mism_c}")


# ---------------------------------------------------------------- 1) 예측력


def predict_section(mkt, feats: dict[dt.date, dict]):
    days = sorted(feats)
    print(f"\n### 1) {mkt}: 일수 {len(days)} (avg20 사용 가능한 날만)")
    g = np.array([abs(feats[d]["gap"]) for d in days])
    a20 = np.array([feats[d]["avg20"] for d in days])
    ratio = np.array([feats[d]["ratio"] for d in days])
    n_cmp = 0
    for n in NS:
        ok = [d for d in days if feats[d].get(f"ok{n}")]
        if len(ok) < 30:
            print(f"  N={n}: 사용 가능 {len(ok)}일 - 표본 부족")
            continue
        idx = [days.index(d) for d in ok]
        gg, aa = g[idx], a20[idx]
        rv = np.array([feats[d][f"rv{n}"] for d in ok])
        rg = np.array([feats[d][f"rng{n}"] for d in ok])
        outs = {
            "나머지 레인지": np.array([feats[d][f"rest_rng{n}"] for d in ok]),
            "나머지 실현변동성": np.array([feats[d][f"rest_rv{n}"] for d in ok]),
            "당일레인지/20일평균(배율)": ratio[idx],
        }
        print(f"\n  [N={n}분] 사용 {len(ok)}일. 로그-로그 OLS. cvR2 = 시간순 10분할 교차검증 R2(훈련평균 대비), Δmse CI = 모델 B가 A보다 MSE를 줄인 양의 블록부트 95% CI(0 초과면 개선)")
        for oname, y in outs.items():
            ly = lg(y)
            if oname.startswith("당일"):
                # 배율은 20일 평균으로 나눈 상대 예측변수 사용
                models = {
                    "A 갭": np.column_stack([lg(gg / aa, 0.02)]),
                    "B 갭+rv": np.column_stack([lg(gg / aa, 0.02), lg(rv / aa)]),
                    "B' 갭+range": np.column_stack([lg(gg / aa, 0.02), lg(rg / aa, 0.02)]),
                    "R rv만": np.column_stack([lg(rv / aa)]),
                }
            else:
                models = {
                    "A 갭": np.column_stack([lg(gg, 0.05)]),
                    "B 갭+rv": np.column_stack([lg(gg, 0.05), lg(rv)]),
                    "B' 갭+range": np.column_stack([lg(gg, 0.05), lg(rg, 0.02)]),
                    "R rv만": np.column_stack([lg(rv)]),
                    "C 20일평균": np.column_stack([lg(aa)]),
                    "D 20일평균+갭": np.column_stack([lg(aa), lg(gg, 0.05)]),
                    "E 20일평균+갭+rv": np.column_stack([lg(aa), lg(gg, 0.05), lg(rv)]),
                }
            mse_ref = {}
            print(f"    결과변수 = {oname}")
            for mname, X in models.items():
                _, r2, adj = ols(X, ly)
                se, se0 = cv_se(X, ly)
                cvr2 = 1 - se.sum() / se0.sum()
                mse_ref[mname] = se
                n_cmp += 1
                print(f"      {mname:<16} R2={r2:.3f} adjR2={adj:.3f} cvR2={cvr2:+.3f}")
            # 핵심 비교: 갭만(A) vs 갭+초반변동성(B), 그리고 20일평균 있는 모델 D vs E
            pairs = [("A 갭", "B 갭+rv")] + ([("D 20일평균+갭", "E 20일평균+갭+rv")] if "E 20일평균+갭+rv" in models else [])
            for a_, b_ in pairs:
                diff = mse_ref[a_] - mse_ref[b_]
                ci = boot(lambda ix: diff[ix].mean(), len(diff), 2000)
                print(f"      -> {b_} vs {a_}: ΔMSE(로그) 평균 {diff.mean():+.4f} CI {ci_s(ci, 4)} (양수면 초반 변동성 추가가 예측 개선)")
            # 상관 / 상위20%
            for pname, pv in (("|갭|", gg), (f"rv{n}", rv), (f"range{n}", rg)):
                sp = spearman(pv, y)
                ci = boot(lambda ix: spearman(pv[ix], y[ix]), len(y), 1000)
                thr = np.quantile(pv, TOP_Q)
                top = pv >= thr
                diffm = y[top].mean() - y[~top].mean()
                cid = boot(lambda ix, pv=pv, y=y, thr=thr: y[ix][pv[ix] >= thr].mean() - y[ix][pv[ix] < thr].mean(), len(y), 1000)
                # 포착률: 결과 상위20% 날 중 예측 상위20%에 든 비율
                ytop = y >= np.quantile(y, TOP_Q)
                prec = ytop[top].mean() * 100
                print(f"      {pname:<9} Spearman {sp:+.2f} {ci_s(ci)} | 상위20% 평균 {y[top].mean():.2f} vs 나머지 {y[~top].mean():.2f} (차 {diffm:+.2f} {ci_s(cid)}, n_top={top.sum()}) | 예측상위20% 중 실제상위20% 비율 {prec:.0f}% (우연 20%)")
    print(f"  (이 시장에서 위 모델 비교 {n_cmp}개 + 상관/분위 다수 - 다중비교 미보정)")


# ---------------------------------------------------------------- 2,3) 트레일


TAIL = 120  # 진입 신호 계산에 쓰는 최근 봉 수 (룩백 3 + 장기 MA 20 + 여유; full과 동일함을 verify에서 확인)


def entry_flags_fast(bars: list[dict], tail: int = TAIL) -> list[bool]:
    """precompute_entry_flags와 동일 규칙, 꼬리 창만 사용(O(n^2) 슬라이스 회피). 동일성은 verify_fast에서 확인."""
    assert tail >= ENTRY_SIGNAL_LOOKBACK_BARS + 25
    flags = []
    for i in range(1, len(bars) + 1):
        w = bars[max(0, i - tail) : i]
        ma = moving_average_cross(w)
        vs = volume_spike(w)
        golden = ma["state"] == "golden" or _golden_cross_in_lookback(w)
        spike = bool(vs["is_spike"]) or _volume_spike_in_lookback(w)
        flags.append(decide_idle_action("golden" if golden else "none", spike)["action"] == "enter")
    return flags


def verify_fast(bars):
    from app.quant.nq_trail_backtest import precompute_entry_flags

    sub = bars[:3000]
    return precompute_entry_flags(sub) == entry_flags_fast(sub)


VARIANTS = {
    "baseline 0.5": fixed_giveback(0.5),
    "변동성비례 k4N60": vol_giveback(4, 60),
    "고정 1.0": fixed_giveback(1.0),
}


def etf_trades(code, bars_by_day, flags_mask_fn=None):
    """ETF 전체 구간 거래(변형별). bars는 close/volume만 사용, 진입 신호는 변형 공유."""
    bars = []
    for d in sorted(bars_by_day):
        for b in bars_by_day[d]:
            bars.append({"date": d.isoformat(), "time": b["time"], "close": b["c"], "volume": b["v"]})
    bars = add_ts(bars)
    flags = entry_flags_fast(bars)
    return bars, flags


def boot_days_diff(units_a, units_b, reps=3000):
    """두 그룹(리스트 of (date_ord, value))의 평균 차이를 '날짜 블록'으로 재표본해 CI. 단위=(ETF, 날짜) 거래 합."""
    dates = sorted({d for d, _ in units_a} | {d for d, _ in units_b})
    pos = {d: i for i, d in enumerate(dates)}
    n = len(dates)
    by_a, by_b = defaultdict(list), defaultdict(list)
    for d, v in units_a:
        by_a[pos[d]].append(v)
    for d, v in units_b:
        by_b[pos[d]].append(v)
    vals = []
    for _ in range(reps):
        ix = block_idx(n)
        a = [v for i in ix for v in by_a.get(i, [])]
        b = [v for i in ix for v in by_b.get(i, [])]
        if len(a) < 3 or len(b) < 3:
            continue
        vals.append(np.mean(a) - np.mean(b))
    if len(vals) < reps * 0.8:
        return None
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def trail_sections(feat_by_etf, etf_bars):
    """2) 초반 N분 변동성 상위 vs 나머지 진입 거래의 변형별 성과, 3) 진입 휴식."""
    res = {}  # (code) -> dict(variant -> trades), bars, flags
    for code, bd in etf_bars.items():
        bars, flags = etf_trades(code, bd)
        trs = {vn: run_giveback_backtest(bars, flags, fn) for vn, fn in VARIANTS.items()}
        res[code] = (bars, flags, trs)
        days = len({b["date"] for b in bars})
        print(f"  {code}: 봉 {len(bars)}, 거래일 {days}, 거래 수 " + ", ".join(f"{vn} {len(t)}" for vn, t in trs.items()))

    for n in NS:
        print(f"\n### 2) 초반 {n}분 변동성(해당 ETF 자신의 실현변동성) 상위 {int((1-TRAIL_TOP_Q)*100)}% 진입일 vs 나머지 — 진입일 기준, 거래 수익률 % (슬리피지/수수료 미반영)")
        grp = {}  # code -> {date -> top?}
        for code, feats in feat_by_etf.items():
            ok = [d for d in sorted(feats) if feats[d].get(f"ok{n}")]
            if len(ok) < 20:
                continue
            thr = np.quantile([feats[d][f"rv{n}"] for d in ok], TRAIL_TOP_Q)
            grp[code] = {d.isoformat(): feats[d][f"rv{n}"] >= thr for d in ok}
        rows = {}
        for vn in VARIANTS:
            for gname in ("상위", "나머지"):
                units = []  # (date, 일별 거래합) 단위, 군집 부트스트랩용
                allr = []
                for code, tmap in grp.items():
                    byday = defaultdict(float)
                    for t in res[code][2][vn]:
                        k = tmap.get(t["entry_date"])
                        if k is None or (k != (gname == "상위")):
                            continue
                        byday[t["entry_date"]] += t["pct_return"]
                        allr.append(t["pct_return"])
                    units += [(dt.date.fromisoformat(d), v) for d, v in byday.items()]
                rows[(vn, gname)] = (allr, units)
        print(f"    {'변형':<18}{'그룹':<6}{'거래n':>6}{'평균%/건':>9}{'승률%':>7}{'합계%':>8}")
        for vn in VARIANTS:
            for gname in ("상위", "나머지"):
                r = rows[(vn, gname)][0]
                if r:
                    print(f"    {vn:<18}{gname:<6}{len(r):>6}{np.mean(r):>9.3f}{np.mean([x > 0 for x in r])*100:>7.0f}{sum(r):>8.2f}")
        # 변형 - baseline 일별 차이(진입일 귀속)를 그룹별로
        print("    변형-baseline 진입일별 성과차(%p/일, 진입일 귀속 거래합 차) — 상위/나머지 및 차이의 차(DiD) 블록부트 CI")
        for vn in list(VARIANTS)[1:]:
            d_by = {}
            for gname in ("상위", "나머지"):
                dd = []
                for code, tmap in grp.items():
                    sums = defaultdict(float)
                    for t in res[code][2][vn]:
                        sums[t["entry_date"]] += t["pct_return"]
                    for t in res[code][2]["baseline 0.5"]:
                        sums[t["entry_date"]] -= t["pct_return"]
                    for d, top in tmap.items():
                        if top == (gname == "상위"):
                            dd.append((dt.date.fromisoformat(d), sums.get(d, 0.0)))
                d_by[gname] = dd
            a, b = d_by["상위"], d_by["나머지"]
            if len(a) < 5 or len(b) < 5:
                continue
            ma_, mb_ = np.mean([v for _, v in a]), np.mean([v for _, v in b])
            ci = boot_days_diff(a, b)
            print(f"      {vn:<18} 상위 {ma_:+.3f} (일수 {len(a)}) / 나머지 {mb_:+.3f} (일수 {len(b)}) / DiD {ma_-mb_:+.3f} {ci_s(ci, 3)}")
        # 그룹 간 baseline 평균 거래수익 차 (고변동 진입일 거래가 다른가)
        ra, ua = rows[("baseline 0.5", "상위")]
        rb, ub = rows[("baseline 0.5", "나머지")]
        if len(ra) >= 3 and len(rb) >= 3:
            print(f"    baseline 거래당 평균 상위-나머지 {np.mean(ra)-np.mean(rb):+.3f}%p, 일 합 기준 블록부트 CI {ci_s(boot_days_diff(ua, ub), 3)}")

    # 3) 진입 휴식
    print("\n### 3) 시초 N분 신규 진입 휴식 가정 (거래 = baseline 0.5 / 변동성비례 / 고정1.0 각각)")
    for flag_kind in ("gap", "early_vol"):
        lab = ("갭 상위 30% 날 (|갭|은 09:00에 알 수 있음 -> 실행 가능)" if flag_kind == "gap"
               else "초반 N분 변동성 상위 30% 날 (사후 라벨: N분이 끝나야 알 수 있어 휴식 규칙으로는 실행 불가, '그 구간 거래가 어땠나'만 기술)")
        for n in NS:
            print(f"\n  [{lab}] N={n}")
            hdr_done = False
            for vn in VARIANTS:
                early, late, other = [], [], []
                early_u, late_u = [], []
                for code, feats in feat_by_etf.items():
                    ok = [d for d in sorted(feats) if feats[d].get(f"ok{n}")]
                    if len(ok) < 20:
                        continue
                    key = (lambda d: abs(feats[d]["gap"])) if flag_kind == "gap" else (lambda d: feats[d][f"rv{n}"])
                    thr = np.quantile([key(d) for d in ok], TRAIL_TOP_Q)
                    flagged = {d.isoformat() for d in ok if key(d) >= thr}
                    for t in res[code][2][vn]:
                        dte = t["entry_date"]
                        if dte in flagged:
                            if _m(t["entry_time"]) < OPEN_MIN + n:
                                early.append(t["pct_return"])
                                early_u.append((dt.date.fromisoformat(dte), t["pct_return"]))
                            else:
                                late.append(t["pct_return"])
                                late_u.append((dt.date.fromisoformat(dte), t["pct_return"]))
                        else:
                            other.append(t["pct_return"])
                if not hdr_done:
                    print(f"    {'변형':<18}{'고변동일 초반진입 n/평균/합':>30}{'  고변동일 이후진입 n/평균/합':>32}{'  평소일 n/평균':>16}  초반-이후 차 CI")
                    hdr_done = True
                def s(r):
                    return f"{len(r):>3}/{np.mean(r):+.3f}/{sum(r):+.2f}" if r else "  0/-/-"
                ci = boot_days_diff(early_u, late_u) if len(early_u) >= 3 and len(late_u) >= 3 else None
                print(f"    {vn:<18}{s(early):>30}{s(late):>32}{(f'{len(other)}/{np.mean(other):+.3f}' if other else '-'):>16}  "
                      f"{(np.mean(early)-np.mean(late)) if early and late else float('nan'):+.3f} {ci_s(ci, 3)}")

    # 3') 실제 재실행: 갭 상위 30% 날 초반 N분 진입 마스킹 (실행 가능한 규칙)
    print("\n### 3') 갭 상위 30% 날의 초반 N분 신규 진입을 막고 백테스트를 다시 돌린 누적(%): 전체 / 막은 날 효과")
    for n in (10, 30):
        print(f"  N={n}  (변형: 막지 않음 cum / 막음 cum / 차)")
        for vn, fn in VARIANTS.items():
            tot0 = tot1 = 0.0
            nn0 = nn1 = 0
            for code, (bars, flags, trs) in res.items():
                feats = feat_by_etf[code]
                ok = [d for d in sorted(feats) if feats[d].get(f"ok{n}")]
                thr = np.quantile([abs(feats[d]["gap"]) for d in ok], TRAIL_TOP_Q)
                flagged = {d.isoformat() for d in ok if abs(feats[d]["gap"]) >= thr}
                mflags = [f and not (b["date"] in flagged and _m(b["time"]) < OPEN_MIN + n) for f, b in zip(flags, bars)]
                t1 = run_giveback_backtest(bars, mflags, fn)
                tot0 += sum(t["pct_return"] for t in trs[vn])
                tot1 += sum(t["pct_return"] for t in t1)
                nn0 += len(trs[vn])
                nn1 += len(t1)
            print(f"    {vn:<18} {tot0:+8.2f} (n={nn0}) / {tot1:+8.2f} (n={nn1}) / {tot1-tot0:+.2f}  [3개 ETF 합산; ETF간 같은 날 상관 큼 -> 독립 아님]")


# ---------------------------------------------------------------- 4) 사례


def cases(mkt_feats, mkt_partial, etf_feats, dates):
    print("\n### 4) 사례 (분위는 해당 시장 전체 표본 내 백분위; '직전만' = 그 날 이전 날들만으로 낸 백분위, 사후편향 제거)")
    for mkt, feats in mkt_feats.items():
        days = sorted(feats)
        for d in dates:
            partial = d not in feats or not all(feats[d].get(f"ok{n}") for n in NS)
            f = (mkt_partial[mkt].get(d) if partial else feats[d])
            if f is None:
                print(f"  {mkt} {d}: 피처 없음 (분봉 없음)")
                continue
            if partial:
                print(f"  ({mkt} {d}: 초반 윈도우에 결측 분이 있어 표준 정의로는 제외 -> 존재하는 봉만으로 '부분 계산'. 통계 표본에는 불포함)")
            prior = [x for x in days if x < d]
            print(f"  {mkt} {d}: 갭 {f['gap']:+.2f}% (|갭| 전체백분위 {pct(abs(f['gap']), [abs(feats[x]['gap']) for x in days if x != d]):.0f} / 직전만 {pct(abs(f['gap']), [abs(feats[x]['gap']) for x in prior]):.0f}), "
                  f"20일평균레인지 {f['avg20']:.2f}%, 당일레인지 {f['day_range']:.2f}% (배율 {f['ratio']:.2f}x)")
            for n in NS:
                if not f.get(f"ok{n}"):
                    print(f"     N={n}: 계산 불가")
                    continue
                allv = [feats[x][f"rv{n}"] for x in days if feats[x].get(f"ok{n}") and x != d]
                prv = [feats[x][f"rv{n}"] for x in prior if feats[x].get(f"ok{n}")]
                flag = "상위20%" if f[f"rv{n}"] >= np.quantile(prv, TOP_Q) else "비상위"
                print(f"     N={n}: range {f[f'rng{n}']:.2f}% rv {f[f'rv{n}']:.2f}% (rv 백분위 전체 {pct(f[f'rv{n}'], allv):.0f} / 직전만 {pct(f[f'rv{n}'], prv):.0f} -> {flag}), "
                      f"나머지 레인지 {f[f'rest_rng{n}']:.2f}% 나머지 rv {f[f'rest_rv{n}']:.2f}%")
    for code, feats in etf_feats.items():
        for d in dates:
            f = feats.get(d)
            if f:
                print(f"  ETF {code} {d}: 갭 {f['gap']:+.2f}%, rv10 {f.get('rv10', float('nan')):.2f}% rv30 {f.get('rv30', float('nan')):.2f}% (nan=초반 결측), 당일레인지 {f['day_range']:.2f}%")


def pct(v, arr):
    arr = np.asarray(arr, dtype=float)
    return float((arr < v).mean() * 100) if len(arr) else float("nan")


# ---------------------------------------------------------------- main


async def main(no_trail: bool):
    daily = {m: await load_daily(m) for m in INDEX}
    idx_bars = {m: await load_index_bars(c) for m, c in INDEX.items()}
    etf_bars = {c: await load_etf_bars(c) for c in ETFS}

    print("## 0) 데이터 점검")
    for m in INDEX:
        await qc(f"지수 {m}({INDEX[m]})", idx_bars[m], daily[m])
    for c in ETFS:
        await qc(f"ETF {c}", etf_bars[c], None)
    print("  시간대: 분봉 time은 'HHMM'(KST 장중 시각)이며 지수는 15:30~15:33(종가 단일가), ETF는 15:35까지 봉이 존재. 15:21~15:29는 단일가 구간이라 봉이 없는 것이 정상.")

    mkt_feats: dict[str, dict] = {}
    mkt_partial: dict[str, dict] = {}
    for m in INDEX:
        feats, part = {}, {}
        last_daily = max(k for k in daily[m] if isinstance(k, dt.date))
        for d, bars in idx_bars[m].items():
            dd = daily[m].get(d) or (daily[m]["__next"] if d > last_daily else None)
            f = day_features(bars, dd)
            if f:
                feats[d] = f
            fp = day_features(bars, dd, strict=False)
            if fp:
                part[d] = fp
        mkt_feats[m] = feats
        mkt_partial[m] = part
    for m, feats in mkt_feats.items():
        ready = {d: f for d, f in feats.items() if f["avg20"]}
        predict_section(m, ready)

    # ETF 피처 (자기 종가 기반 갭: 직전 거래일 마지막 봉 종가)
    etf_feats: dict[str, dict] = {}
    for c, bd in etf_bars.items():
        days = sorted(bd)
        feats = {}
        for i, d in enumerate(days):
            if i == 0:
                continue
            prev_c = [b for b in bd[days[i - 1]] if b["m"] <= 15 * 60 + 30][-1]["c"]
            f = day_features(bd[d], {"prev_c": prev_c, "avg20": None})
            if f:
                feats[d] = f
        etf_feats[c] = feats

    if not no_trail:
        print("\n## 2~3) ETF 트레일 (진입/손절/전환 규칙은 §5.91과 동일, 진입 신호는 변형 공유)")
        ok = verify_fast(_flatten(etf_bars["0167A0"]))
        print(f"  꼬리창 진입신호 == 전체창 진입신호 (0167A0 앞 3000봉): {ok}")
        trail_sections(etf_feats, etf_bars)

    cases(mkt_feats, mkt_partial, etf_feats, [dt.date(2026, 11, 5), dt.date(2026, 11, 6)])
    print("\n(끝) 표본: 단일 국면(2026-06~11), 인접일 군집, 다중비교 미보정 - 해석 시 PLAN.md §5.94 한계 참고")


def _flatten(bd):
    out = []
    for d in sorted(bd):
        for b in bd[d]:
            out.append({"date": d.isoformat(), "time": b["time"], "close": b["c"], "volume": b["v"]})
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-trail", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(args.no_trail))
