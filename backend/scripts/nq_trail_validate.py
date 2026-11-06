"""NQ 보조 / 변동성 비례 / 고정 격자 트레일 반납폭 검증 (PLAN.md §5.91, 읽기 전용).

가설: 트레일 중 NQ(나스닥선물) 5분봉 상승 추세면 반납폭을 넓히면 조기 청산이 준다.
대조: 단순히 변동성 대비 0.5%가 좁은 것일 수도 있다 -> 고정 격자 / 변동성 비례와 비교.

- 진입·손절·트레일 전환은 모두 기존 백테스트와 동일 규칙으로 고정(`quant/nq_trail_backtest.py`).
- NQ가 있는 구간(2026-08-28~)만 사용(전 변형 동일 구간). NQ는 `intraday_sample`
  series_key='nq_futures'(섀도 적재기가 쌓은 것)에서 읽는다.
- 구간: 전체 / 시간순 전반 / 후반(거래일 반분, 각각 독립 백테스트 — 웜업·포지션 이월 없음).
- 파라미터 선택 프로토콜: 전반에서 최고인 파라미터를 후반에서 확인(out-of-sample),
  전체에서 최고인 파라미터의 전반/후반도 함께 표기.
- 플라시보: NQ 변화율을 5거래일(캘린더 7일) 전 값으로 대체 — 정보 없이도 같은 효과면 NQ 신호가 아님.

Usage:
    docker exec stock-backend-1 python -m scripts.nq_trail_validate
"""

import argparse
import asyncio
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.collectors.auto_trader import TARGET_CODE  # noqa: E402
from app.db import async_session_factory  # noqa: E402
from app.models import IntradaySample  # noqa: E402
from app.quant.nq_shadow import NQ_SERIES_KEY, NqIndex  # noqa: E402
from app.quant.nq_trail_backtest import (  # noqa: E402
    add_ts,
    fixed_giveback,
    nq_giveback,
    precompute_entry_flags,
    run_giveback_backtest,
    trade_metrics,
    vol_giveback,
)
from scripts.compare_trail_modes import load_bars  # noqa: E402

NQ_START = "2026-08-28"  # 08-27은 NQ 이력이 13:05 KST부터라 장 초반 누락
FIXED = [0.3, 0.5, 0.8, 1.0, 1.2]
VOL_K = [3, 4, 6, 8]
VOL_N = [20, 60]


def _cases(nq: NqIndex):
    c = [("BASE ratchet 0.5", "base", fixed_giveback(0.5))]
    for g in FIXED:
        if g != 0.5:
            c.append((f"fixed {g}", "fixed", fixed_giveback(g)))
    for k in VOL_K:
        for n in VOL_N:
            c.append((f"vol k={k} N={n}", "vol", vol_giveback(k, n)))
    for m in (15, 30):
        for w in (0.8, 1.0):
            c.append((f"NQ{m}m>0 -> {w}", "nq", nq_giveback(nq, m, 0.5, w)))
            c.append((f"placebo NQ{m}m(-7d)>0 -> {w}", "placebo", nq_giveback(nq, m, 0.5, w, lag_days=7)))
    return c


def _split(bars, k):
    days = sorted({b["date"] for b in bars})
    out = []
    for j in range(k):
        ds = set(days[j * len(days) // k : (j + 1) * len(days) // k])
        out.append([b for b in bars if b["date"] in ds])
    return out


def _daily_t(trades_a, trades_b):
    """청산일별 수익 합의 쌍대 차이(a-b)의 t통계(일 단위 표본, 독립 가정의 거친 지표)."""
    import statistics
    from collections import defaultdict

    da, db = defaultdict(float), defaultdict(float)
    for t in trades_a:
        da[t["exit_date"]] += t["pct_return"]
    for t in trades_b:
        db[t["exit_date"]] += t["pct_return"]
    diffs = [da[d] - db[d] for d in set(da) | set(db)]
    if len(diffs) < 3 or statistics.stdev(diffs) == 0:
        return None
    return statistics.mean(diffs) / (statistics.stdev(diffs) / len(diffs) ** 0.5)


def _f(v, d=2):
    return "-" if v is None else f"{v:.{d}f}"


async def main_async(code: str) -> None:
    bars = [b for b in await load_bars(code, 400) if b["date"] >= NQ_START]
    async with async_session_factory() as s:
        rows = (
            await s.execute(
                select(IntradaySample.time, IntradaySample.value)
                .where(IntradaySample.series_key == NQ_SERIES_KEY)
                .order_by(IntradaySample.time)
            )
        ).all()
    nq = NqIndex([(t, float(v)) for t, v in rows])
    print(f"code={code} 1m bars={len(bars)} days={len({b['date'] for b in bars})} (NQ 구간 {NQ_START}~만 사용) NQ 5m bars={len(rows)}")

    full = add_ts(bars)
    h1, h2 = _split(bars, 2)
    segs = {"full": full, "H1": add_ts(h1), "H2": add_ts(h2)}
    for k, v in segs.items():
        ds = sorted({b["date"] for b in v})
        print(f"  seg {k}: days={len(ds)} {ds[0]}..{ds[-1]} bars={len(v)}")
    flags = {k: precompute_entry_flags(v) for k, v in segs.items()}

    cases = _cases(nq)
    R = {}
    TR = {}
    for name, fam, fn in cases:
        for sk, sb in segs.items():
            tr = run_giveback_backtest(sb, flags[sk], fn)
            TR[(name, sk)] = tr
            R[(name, sk)] = trade_metrics(tr, sb)

    print("\n=== 변형별 성과 (cum%=거래수익 단순합) ===")
    hdr = f"{'case':<30}{'seg':<5}{'n':>4}{'win%':>7}{'cum%':>8}{'avg%':>7}{'hold분':>8}{'MDD%p':>7}{'조기청산%':>10}"
    print(hdr)
    for name, _, _ in cases:
        for sk in segs:
            m = R[(name, sk)]
            print(
                f"{name:<30}{sk:<5}{m['n']:>4}{_f(m['win_rate_pct'], 1):>7}{_f(m['cumulative_return_pct']):>8}"
                f"{_f(m['avg_trade_pct'], 3):>7}{_f(m['avg_hold_min'], 0):>8}{_f(m['max_drawdown_pct']):>7}{_f(m['early_exit_pct'], 0):>10}"
            )

    print("\n=== 일관성: 구간별 cum% (baseline 대비 차이 Δ) ===")
    print(f"{'case':<30}{'full':>8}{'Δfull':>8}{'H1':>8}{'ΔH1':>8}{'H2':>8}{'ΔH2':>8}")
    base = {sk: R[("BASE ratchet 0.5", sk)]["cumulative_return_pct"] for sk in segs}
    for name, _, _ in cases:
        v = {sk: R[(name, sk)]["cumulative_return_pct"] for sk in segs}
        print(f"{name:<30}" + "".join(f"{v[sk]:>8.2f}{v[sk]-base[sk]:>+8.2f}" for sk in segs))

    print("\n=== 전체 구간 baseline 대비 청산일별 쌍대 t통계 (|t|<2면 유의하지 않음; 다중비교 미보정) ===")
    for name, _, _ in cases[1:]:
        t = _daily_t(TR[(name, "full")], TR[("BASE ratchet 0.5", "full")])
        print(f"{name:<30} t={_f(t)}")

    # 가설 직접 검정: baseline 트레일 청산 시점의 NQ 15m 부호별 청산 후 30분 반등(>=0.5%) 비율
    fb = segs["full"]
    ex = [t for t in TR[("BASE ratchet 0.5", "full")] if t["exit_reason"] == "exit_trail"]
    grp = {"NQ15m>0": [0, 0], "NQ15m<=0": [0, 0], "NQ없음": [0, 0]}
    for t in ex:
        i = t["exit_idx"]
        ch = nq.change_pct(fb[i]["ts"], 15)
        key = "NQ없음" if ch is None else ("NQ15m>0" if ch > 0 else "NQ15m<=0")
        fut = [float(b["close"]) for b in fb[i + 1 : i + 31] if b["date"] == fb[i]["date"]]
        grp[key][0] += 1
        grp[key][1] += bool(fut) and max(fut) >= t["exit_price"] * 1.005
    print("\n=== baseline 트레일 청산 시점 NQ 15m 부호별 '청산 후 30분 내 +0.5% 이상 추가 상승' 비율 ===")
    for k, (n, e) in grp.items():
        print(f"{k:<10} n={n:>3} 반등 {e:>3} ({(e / n * 100 if n else 0):.0f}%)")

    print("\n=== 선택 프로토콜: 가족별 H1 최고 -> H2 확인 / full 최고 -> H1,H2 ===")
    for fam in ("fixed", "vol", "nq", "placebo"):
        members = [n for n, f, _ in cases if f == fam or (fam == "fixed" and f == "base")]
        cum = lambda n, sk: R[(n, sk)]["cumulative_return_pct"]
        b1 = max(members, key=lambda n: cum(n, "H1"))
        bf = max(members, key=lambda n: cum(n, "full"))
        print(
            f"[{fam}] H1 best={b1}: H1 {cum(b1,'H1'):.2f} (base {base['H1']:.2f}) -> H2 {cum(b1,'H2'):.2f} (base {base['H2']:.2f}, Δ{cum(b1,'H2')-base['H2']:+.2f})"
        )
        print(
            f"[{fam}] full best={bf}: full {cum(bf,'full'):.2f} (base {base['full']:.2f}) | H1 {cum(bf,'H1'):.2f} H2 {cum(bf,'H2'):.2f}"
        )

    # 거래 단위 불확실성: 전체 구간 baseline 대비 거래별 수익 표준오차(참고용)
    import statistics

    tr = run_giveback_backtest(segs["full"], flags["full"], fixed_giveback(0.5))
    r = [t["pct_return"] for t in tr]
    if len(r) > 2:
        se = statistics.stdev(r) / len(r) ** 0.5
        print(f"\n참고: baseline 거래 {len(r)}건 평균 {statistics.mean(r):.3f}% 표준오차 {se:.3f}%p (누적합 SE ≈ {se*len(r):.2f}%p) — 변형 간 차이가 이보다 작으면 차이 없음으로 본다")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--code", default=TARGET_CODE)
    a = p.parse_args()
    asyncio.run(main_async(a.code))


if __name__ == "__main__":
    main()
