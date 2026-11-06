"""고변동일(시초 갭 / 장 초반 변동성 큰 날) 정의 가능성 + 평소일과의 차이 검증 (PLAN.md §5.92, 읽기 전용).

가설: 시초 갭이 크거나 당일 레인지가 평소보다 훨씬 큰 날은 평소와 통계적으로 다른 날이다
(시초->종가 수익률 분포, 되돌림, 다음날 연속성). 다르다면 트레일 반납폭/진입지연/사이즈를 변동성 비례로
조정할 근거가 될 수 있다. 다르지 않으면 '고변동일 특별 취급'의 근거가 없다.

데이터 한계(중요): 지수 장중 분봉이 DB에 없다 -> index_ohlcv 일봉(시/고/저/종)만 사용.
 '장 초반 변동성'은 직접 측정 불가, 당일 레인지/시가->종가/종가의 저가 대비 반등폭으로 근사한다.
 market_flow(외국인/기관 순매수)는 장마감 확정치라 시초 시점엔 알 수 없다 -> 당일 동행 지표 또는 다음날 예측용으로만 해석.
 원인 태그(정책/전쟁 등)는 과거에 달 방법이 없어 제외. 앞으로 narrative_proposal 이력이 쌓이면 별도 검증.

평소일 = 아래 정의 어디에도 걸리지 않은 날. 통계: Welch t + 부트스트랩(10000회, seed 고정) 95% CI.
다중비교(정의 x 방향 x 지표) 보정 안 함 -> Bonferroni 임계값을 함께 출력한다.

Usage:
    docker exec stock-backend-1 python -m scripts.high_vol_day_study
    docker exec stock-backend-1 python -m scripts.high_vol_day_study --no-etf   # 5)번 ETF 건수 생략
"""

import argparse
import asyncio
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import async_session_factory  # noqa: E402

MARKETS = ["kospi", "kosdaq"]
RANGE_WIN = 20  # 평균 레인지 산정 일수(직전 20거래일, 당일 제외)
PLUNGE = -2.0  # 정의 C: 전일 종가대비 하락률(%) 기준
BOOT_N = 10000
rng = np.random.default_rng(20261106)


# ---------------------------------------------------------------- 데이터


async def load_daily(market: str) -> list[dict]:
    sql = text(
        """
        select o.date, o.open, o.high, o.low, o.close,
               (select net_value from market_flow f where f.market=o.market and f.date=o.date and f.investor='외국인') as frgn,
               (select net_value from market_flow f where f.market=o.market and f.date=o.date and f.investor='기관계') as inst
        from index_ohlcv o where o.market=:m order by o.date
        """
    )
    async with async_session_factory() as s:
        rows = (await s.execute(sql, {"m": market})).all()
    return [
        {"date": r[0], "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]),
         "frgn": None if r[5] is None else float(r[5]), "inst": None if r[6] is None else float(r[6])}
        for r in rows
        if None not in (r[1], r[2], r[3], r[4])
    ]


def build_features(rows: list[dict]) -> list[dict]:
    """각 거래일(2번째부터)의 갭/레인지/수익률/다음날 지표. 단위는 모두 %."""
    out = []
    ranges = []
    for i, r in enumerate(rows):
        if i == 0:
            continue
        pc = rows[i - 1]["c"]
        rg = (r["h"] - r["l"]) / pc * 100
        f = {
            "date": r["date"], "gap": (r["o"] / pc - 1) * 100, "range": rg,
            "o2c": (r["c"] / r["o"] - 1) * 100, "c2c": (r["c"] / pc - 1) * 100,
            "prev_c2c": None if i < 2 else (pc / rows[i - 2]["c"] - 1) * 100,
            "rebound_from_low": (r["c"] - r["l"]) / pc * 100,  # 저가 대비 종가 반등폭(전일종가 대비 %)
            "pullback_from_high": (r["h"] - r["c"]) / pc * 100,
            "open_pos": (r["o"] - r["l"]) / (r["h"] - r["l"]) if r["h"] > r["l"] else float("nan"),  # 0=시가가 저가, 1=시가가 고가
            "avg_range": float(np.mean(ranges[-RANGE_WIN:])) if len(ranges) >= RANGE_WIN else None,
            "frgn": r["frgn"], "inst": r["inst"],
            "next_gap": None, "next_o2c": None, "next_c2c": None,
        }
        if i + 1 < len(rows):
            n = rows[i + 1]
            f["next_gap"] = (n["o"] / r["c"] - 1) * 100
            f["next_o2c"] = (n["c"] / n["o"] - 1) * 100
            f["next_c2c"] = (n["c"] / r["c"] - 1) * 100
        f["rng_ratio"] = None if f["avg_range"] is None else rg / f["avg_range"]
        ranges.append(rg)
        out.append(f)
    return out


# 정의: 이름 -> 판정 함수
DEFS = {
    "G1.5 |갭|>=1.5%": lambda f: abs(f["gap"]) >= 1.5,
    "G2.0 |갭|>=2.0%": lambda f: abs(f["gap"]) >= 2.0,
    "G3.0 |갭|>=3.0%": lambda f: abs(f["gap"]) >= 3.0,
    "R1.5 레인지>=1.5x": lambda f: f["rng_ratio"] is not None and f["rng_ratio"] >= 1.5,
    "R2.0 레인지>=2.0x": lambda f: f["rng_ratio"] is not None and f["rng_ratio"] >= 2.0,
    "R3.0 레인지>=3.0x": lambda f: f["rng_ratio"] is not None and f["rng_ratio"] >= 3.0,
    f"C 전일{PLUNGE}%이하 급락 다음날": lambda f: f["prev_c2c"] is not None and f["prev_c2c"] <= PLUNGE,
    f"C' 전일급락+갭다운(<=-0.5%)": lambda f: f["prev_c2c"] is not None and f["prev_c2c"] <= PLUNGE and f["gap"] <= -0.5,
}


def is_normal(f) -> bool:
    return f["avg_range"] is not None and not any(fn(f) for fn in DEFS.values())


# ---------------------------------------------------------------- 통계


def _clean(v):
    a = np.array([x for x in v if x is not None and not (isinstance(x, float) and math.isnan(x))], dtype=float)
    return a


def welch_t(a, b):
    if len(a) < 3 or len(b) < 3:
        return None
    va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
    if va + vb == 0:
        return None
    return (a.mean() - b.mean()) / math.sqrt(va + vb)


def boot_ci(a, b):
    """평균 차이(a-b)의 부트스트랩 95% CI."""
    if len(a) < 3 or len(b) < 3:
        return None
    da = rng.choice(a, (BOOT_N, len(a))).mean(axis=1)
    db = rng.choice(b, (BOOT_N, len(b))).mean(axis=1)
    d = da - db
    return float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def fmt(v, d=2):
    return "-" if v is None else f"{v:.{d}f}"


def verdict(a, b, ci=None):
    """판정: 표본<10 -> '표본 부족'; CI가 0을 포함하면 '차이 없음'; 아니면 '차이(미보정)'. ci를 넘기면 재추출 없이 사용."""
    if len(a) < 10:
        return "표본부족"
    ci = ci or boot_ci(a, b)
    if ci is None:
        return "표본부족"
    return "차이(미보정)" if (ci[0] > 0 or ci[1] < 0) else "차이없음"


# ---------------------------------------------------------------- 보고


def summarize(feats, label, normal, direction, n_tests):
    """한 정의 x 방향 그룹 vs 같은 방향 평소일: 시초->종가 등 비교 한 줄들을 출력."""
    sel = [f for f in feats if DEFS[label](f)]
    if direction == "down":
        sel = [f for f in sel if f["gap"] < 0]
        nor = [f for f in normal if f["gap"] < 0]
    else:
        sel = [f for f in sel if f["gap"] > 0]
        nor = [f for f in normal if f["gap"] > 0]
    row = {"n": len(sel), "n_normal": len(nor)}
    if not sel or not nor:
        return row, sel, nor
    o2c, o2c_n = _clean(f["o2c"] for f in sel), _clean(f["o2c"] for f in nor)
    row.update(
        o2c_mean=o2c.mean(), o2c_med=float(np.median(o2c)), o2c_sd=o2c.std(ddof=1) if len(o2c) > 1 else None,
        n_mean=o2c_n.mean(), n_med=float(np.median(o2c_n)), n_sd=o2c_n.std(ddof=1),
        up_pct=(o2c > 0).mean() * 100, n_up_pct=(o2c_n > 0).mean() * 100,
        t=welch_t(o2c, o2c_n), ci=boot_ci(o2c, o2c_n), verdict=None,
        rebound=np.mean(_clean(f["rebound_from_low"] for f in sel)), n_rebound=np.mean(_clean(f["rebound_from_low"] for f in nor)),
        pullback=np.mean(_clean(f["pullback_from_high"] for f in sel)), n_pullback=np.mean(_clean(f["pullback_from_high"] for f in nor)),
        open_pos=np.nanmean([f["open_pos"] for f in sel]), n_open_pos=np.nanmean([f["open_pos"] for f in nor]),
        rratio=np.mean(_clean(f["rng_ratio"] for f in sel)), n_rratio=np.mean(_clean(f["rng_ratio"] for f in nor)),
        ng=np.mean(_clean(f["next_gap"] for f in sel)), n_ng=np.mean(_clean(f["next_gap"] for f in nor)),
        nr=np.mean(_clean(f["next_o2c"] for f in sel)), n_nr=np.mean(_clean(f["next_o2c"] for f in nor)),
    )
    nr_a, nr_b = _clean(f["next_o2c"] for f in sel), _clean(f["next_o2c"] for f in nor)
    row["verdict"] = verdict(o2c, o2c_n, row["ci"])
    row["nr_t"] = welch_t(nr_a, nr_b)
    row["nr_ci"] = boot_ci(nr_a, nr_b)
    row["nr_verdict"] = verdict(nr_a, nr_b, row["nr_ci"])
    return row, sel, nor


def flow_section(mname, sel_all, label):
    """외국인/기관 순매수 방향이 갭 방향과 같은지 + 그 후(다음날) 연속성. 갭 !=0인 날만."""
    print(f"  [{label}] 수급-갭 방향 (당일 장마감 확정치 = 시초엔 미지; 다음날 수익률 sign-adjusted = 갭방향 x 값)")
    for inv, key in (("외국인", "frgn"), ("기관계", "inst")):
        same, opp = [], []
        for f in sel_all:
            if f[key] is None or f["gap"] == 0 or f["next_o2c"] is None:
                continue
            (same if (f[key] > 0) == (f["gap"] > 0) else opp).append(f)
        tot = len(same) + len(opp)
        if tot == 0:
            continue
        # 당일 갭 방향 대비 당일 시초->종가 방향(sign-adjusted)과 다음날 시초->종가(sign-adjusted)
        def sa(g, k):
            return _clean([math.copysign(1, f["gap"]) * f[k] for f in g])
        print(
            f"    {inv}: 갭과 같은방향 {len(same)}/{tot} ({len(same)/tot*100:.0f}%) | "
            f"당일 o2c(갭방향+) 같은 {fmt(sa(same,'o2c').mean() if same else None)} / 반대 {fmt(sa(opp,'o2c').mean() if opp else None)} | "
            f"다음날 o2c(갭방향+) 같은 {fmt(sa(same,'next_o2c').mean() if same else None)} / 반대 {fmt(sa(opp,'next_o2c').mean() if opp else None)} "
            f"[t={fmt(welch_t(sa(same,'next_o2c'), sa(opp,'next_o2c')))}, {verdict(sa(same,'next_o2c'), sa(opp,'next_o2c'))}]"
        )


async def analyze(verbose_flow=True):
    data = {}
    for m in MARKETS:
        rows = await load_daily(m)
        data[m] = build_features(rows)
        d0, d1 = rows[0]["date"], rows[-1]["date"]
        print(f"[{m}] 일봉 {len(rows)}행 {d0}~{d1} (수급 결합 {sum(1 for r in rows if r['frgn'] is not None)}행)")
    n_tests = len(DEFS) * 2 * len(MARKETS) * 2  # 정의 x 방향 x 시장 x (당일 o2c, 다음날 o2c)
    print(f"\n다중비교: 정의 {len(DEFS)} x 방향 2 x 시장 {len(MARKETS)} x 지표 2 = {n_tests}회 -> Bonferroni 유의수준 0.05/{n_tests}={0.05/n_tests:.5f}"
          f" (|t| 약 {3.6:.1f} 이상이어야 보정 후 유의에 근접)")

    # 1) 정의별 일수
    print("\n=== 1) 정의별 해당 일수 (연도별 / 최근 60거래일) ===")
    for m in MARKETS:
        feats = data[m]
        valid = [f for f in feats if f["avg_range"] is not None]
        years = sorted({f["date"].year for f in valid})
        print(f"\n[{m}] 분석대상 {len(valid)}일 (평균레인지 산정 가능일), 평소일 {sum(is_normal(f) for f in valid)}일")
        print(f"{'정의':<28}{'전체':>6}{'%':>6}" + "".join(f"{y:>6}" for y in years) + f"{'최근60':>8}{'갭다운':>7}{'갭업':>6}")
        recent = valid[-60:]
        for k, fn in DEFS.items():
            sel = [f for f in valid if fn(f)]
            byy = defaultdict(int)
            for f in sel:
                byy[f["date"].year] += 1
            print(
                f"{k:<28}{len(sel):>6}{len(sel)/len(valid)*100:>6.1f}" + "".join(f"{byy[y]:>6}" for y in years)
                + f"{sum(fn(f) for f in recent):>8}{sum(f['gap']<0 for f in sel):>7}{sum(f['gap']>0 for f in sel):>6}"
            )
        # 레인지/갭 분포
        g = _clean(abs(f["gap"]) for f in valid)
        r = _clean(f["range"] for f in valid)
        print(f"  |갭| 분위수 50/90/95/99: {', '.join(fmt(np.percentile(g,p)) for p in (50,90,95,99))}% | 레인지 분위수 50/90/95/99: {', '.join(fmt(np.percentile(r,p)) for p in (50,90,95,99))}%")
        rg = _clean(f["range"] for f in recent)
        print(f"  최근60일 레인지 평균 {fmt(rg.mean())}% vs 전체 평균 {fmt(r.mean())}% (레인지 자체가 국면에 따라 커졌는지 확인)")

    # 2) 비교
    print("\n=== 2) 해당일 vs 평소일 (같은 갭 방향끼리). 값은 %. o2c=시가->종가 ===")
    for m in MARKETS:
        feats = [f for f in data[m] if f["avg_range"] is not None]
        normal = [f for f in feats if is_normal(f)]
        print(f"\n[{m}] 평소일 n={len(normal)}")
        for direction in ("down", "up"):
            nor = [f for f in normal if (f["gap"] < 0) == (direction == "down")]
            print(f"  -- 갭{'다운' if direction=='down' else '업'} (평소일 n={len(nor)}: o2c 평균 {fmt(np.mean([f['o2c'] for f in nor]))} 중앙 {fmt(np.median([f['o2c'] for f in nor]))} 표준편차 {fmt(np.std([f['o2c'] for f in nor], ddof=1))})")
            print(f"  {'정의':<26}{'n':>4}{'o2c평균':>8}{'중앙':>7}{'sd':>6}{'상승%':>6}{'t':>6}{'95%CI(평균차)':>16}{'판정':>10}"
                  f"{'반등':>6}{'(평소)':>7}{'레인지x':>8}{'(평소)':>7}{'시가위치':>8}{'(평소)':>7}")
            for label in DEFS:
                row, _, _ = summarize(feats, label, normal, direction, n_tests)
                if "o2c_mean" not in row:
                    print(f"  {label:<26}{row['n']:>4}  (표본 없음)")
                    continue
                ci = row["ci"]
                print(
                    f"  {label:<26}{row['n']:>4}{row['o2c_mean']:>8.2f}{row['o2c_med']:>7.2f}{fmt(row['o2c_sd']):>6}{row['up_pct']:>6.0f}"
                    f"{fmt(row['t']):>6}{('['+fmt(ci[0])+','+fmt(ci[1])+']' if ci else '-'):>16}{row['verdict']:>10}"
                    f"{row['rebound']:>6.2f}{row['n_rebound']:>7.2f}{row['rratio']:>8.2f}{row['n_rratio']:>7.2f}{row['open_pos']:>8.2f}{row['n_open_pos']:>7.2f}"
                )
            print(f"  {'정의(다음날 연속성)':<26}{'n':>4}{'다음갭':>8}{'(평소)':>7}{'다음o2c':>8}{'(평소)':>7}{'t':>6}{'95%CI(평균차)':>16}{'판정':>10}")
            for label in DEFS:
                row, _, _ = summarize(feats, label, normal, direction, n_tests)
                if "nr" not in row:
                    continue
                ci = row["nr_ci"]
                print(
                    f"  {label:<26}{row['n']:>4}{row['ng']:>8.2f}{row['n_ng']:>7.2f}{row['nr']:>8.2f}{row['n_nr']:>7.2f}"
                    f"{fmt(row['nr_t']):>6}{('['+fmt(ci[0])+','+fmt(ci[1])+']' if ci else '-'):>16}{row['nr_verdict']:>10}"
                )

    # 수급
    if verbose_flow:
        print("\n=== 2-b) 외국인/기관 순매수 vs 갭 방향 (market_flow, 백만원 단위 부호만 사용) ===")
        for m in MARKETS:
            feats = [f for f in data[m] if f["avg_range"] is not None]
            print(f"\n[{m}]")
            flow_section(m, [f for f in feats if is_normal(f)], "평소일")
            for label in ("G1.5 |갭|>=1.5%", "G2.0 |갭|>=2.0%", "R2.0 레인지>=2.0x"):
                flow_section(m, [f for f in feats if DEFS[label](f)], label)
    return data


# ---------------------------------------------------------------- 5) ETF 건수


async def etf_counts(data):
    from app.collectors.auto_trader import TARGET_CODE
    from app.quant.nq_trail_backtest import (
        add_ts, fixed_giveback, precompute_entry_flags, run_giveback_backtest, vol_giveback,
    )
    from scripts.compare_trail_modes import load_bars

    bars = await load_bars(TARGET_CODE, 400)
    if not bars:
        print("\n5) 1분봉 없음")
        return
    days = sorted({b["date"] for b in bars})
    print(f"\n=== 5) {TARGET_CODE} 1분봉 구간 {days[0]}~{days[-1]} ({len(days)}거래일): 지수 고변동일 vs 평소일 트레일 거래 '건수'만 (결론 없음) ===")
    kospi = {f["date"].isoformat(): f for f in data["kospi"]}
    hv_defs = ["G1.5 |갭|>=1.5%", "R1.5 레인지>=1.5x"]

    def cls(d):
        f = kospi.get(d)
        if f is None or f["avg_range"] is None:
            return "미분류"
        if any(DEFS[k](f) for k in hv_defs):
            return "고변동(G1.5 or R1.5)"
        return "평소/기타"

    cnt = defaultdict(int)
    for d in days:
        cnt[cls(d)] += 1
    print("  일수:", dict(cnt))
    full = add_ts(bars)
    flags = precompute_entry_flags(full)
    for name, fn in (("baseline 0.5", fixed_giveback(0.5)), ("vol k=4 N=60", vol_giveback(4, 60)), ("fixed 1.0", fixed_giveback(1.0))):
        tr = run_giveback_backtest(full, flags, fn)
        c = defaultdict(int)
        for t in tr:
            c[cls(t["entry_date"])] += 1
        print(f"  {name:<14} 거래 {len(tr)}건 진입일 기준:", dict(c))
    print("  -> 고변동일 거래 건수가 수 건 수준이면 성과 비교 불가. 이번 연구는 건수 보고까지만.")


async def main_async(no_etf: bool) -> None:
    data = await analyze()
    if not no_etf:
        await etf_counts(data)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--no-etf", action="store_true")
    a = p.parse_args()
    asyncio.run(main_async(a.no_etf))


if __name__ == "__main__":
    main()
