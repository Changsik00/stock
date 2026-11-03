"""profit_ratio 격자 + 기간 분할 검증 스크립트(읽기 전용, 일회성).

- profit_ratio: min_giveback ∈ {0.2,0.3,0.4,0.5} x giveback_ratio ∈ {0.2..0.5} 전 조합
- ratchet: 고정 giveback ∈ {0.3,0.4,0.5,0.6,0.8}
- 구간: 전체 / 2분할 / 3분할(거래일 경계, 시간순). 각 구간은 **독립 백테스트**
  (구간 시작 시 포지션 없음·지표 웜업 없음; 구간 경계에 걸친 포지션은 이월되지
  않고 이동평균/거래량 신호도 구간 첫 봉부터 다시 쌓인다).
- 백테스트 모듈은 수정하지 않고 multiprocessing(fork)으로 케이스x구간을 병렬 실행.

Usage:
    docker exec stock-backend-1 python -m scripts.trail_grid_validate --days 180
"""

import argparse
import asyncio
import itertools
import multiprocessing as mp
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.collectors.auto_trader import TARGET_CODE  # noqa: E402
from app.quant.auto_trade_intraday_backtest import run_intraday_backtest  # noqa: E402
from scripts.compare_trail_modes import load_bars  # noqa: E402

MINS = [0.2, 0.3, 0.4, 0.5]
RATIOS = [0.2, 0.25, 0.3, 0.35, 0.4, 0.5]
RATCHETS = [0.3, 0.4, 0.5, 0.6, 0.8]

_SEGS: dict[str, list[dict]] = {}


def _cases() -> list[tuple[str, dict]]:
    out = [(f"ratchet({g})", {"trail_mode": "ratchet", "ratchet_giveback_pct": g}) for g in RATCHETS]
    for m, r in itertools.product(MINS, RATIOS):
        out.append(
            (f"pr(min={m},r={r})", {"trail_mode": "profit_ratio", "min_giveback_pct": m, "giveback_ratio": r})
        )
    return out


def _work(args):
    name, kw, seg = args
    s = run_intraday_backtest(_SEGS[seg], **kw)["summary"]
    return name, seg, s


def _split(bars: list[dict], k: int) -> list[list[dict]]:
    days = sorted({b["date"] for b in bars})
    chunks = [days[i * len(days) // k : (i + 1) * len(days) // k] for i in range(k)]
    return [[b for b in bars if b["date"] in set(c)] for c in chunks]


def _g(v, d=2):
    return "-" if v is None else f"{v:.{d}f}"


async def main_async(code: str, days: int) -> None:
    bars = await load_bars(code, days)
    _SEGS["full"] = bars
    for k in (2, 3):
        for j, part in enumerate(_split(bars, k)):
            _SEGS[f"s{k}_{j + 1}"] = part
    nd = len({b["date"] for b in bars})
    print(f"code={code} bars={len(bars)} trading_days={nd}")
    for key, v in _SEGS.items():
        ds = sorted({b["date"] for b in v})
        print(f"  seg {key}: days={len(ds)} {ds[0]}..{ds[-1]}")

    cases = _cases()
    tasks = [(n, kw, seg) for n, kw in cases for seg in _SEGS]
    ctx = mp.get_context("fork")
    with ctx.Pool(min(16, mp.cpu_count())) as pool:
        res = pool.map(_work, tasks, chunksize=1)
    R: dict[tuple[str, str], dict] = {(n, seg): s for n, seg, s in res}
    names = [n for n, _ in cases]

    def cum(n, seg):
        return R[(n, seg)]["cumulative_return_pct"] or 0.0

    print("\n=== FULL grid (sorted by cum%) ===")
    print(f"{'case':<22}{'n':>4}{'win%':>8}{'cum%':>9}{'worst%':>9}")
    for n in sorted(names, key=lambda x: -cum(x, "full")):
        s = R[(n, "full")]
        print(f"{n:<22}{s['n']:>4}{_g(s['win_rate_pct']):>8}{_g(s['cumulative_return_pct']):>9}{_g(s['worst_trade_pct']):>9}")

    print("\n=== profit_ratio matrix (full cum%), rows=min, cols=ratio ===")
    print(f"{'':<8}" + "".join(f"{r:>9}" for r in RATIOS))
    for m in MINS:
        print(f"{m:<8}" + "".join(f"{cum(f'pr(min={m},r={r})', 'full'):>9.2f}" for r in RATIOS))

    print("\n=== per-segment cum% (ratchet rows, then pr sorted by full) ===")
    segkeys = list(_SEGS)
    print(f"{'case':<22}" + "".join(f"{k:>9}" for k in segkeys))
    for n in [x for x in names if x.startswith("ratchet")] + sorted(
        [x for x in names if x.startswith("pr")], key=lambda x: -cum(x, "full")
    ):
        print(f"{n:<22}" + "".join(f"{cum(n, k):>9.2f}" for k in segkeys))

    base = "ratchet(0.5)"
    best_ratchet = max((x for x in names if x.startswith("ratchet")), key=lambda x: cum(x, "full"))
    prs = [x for x in names if x.startswith("pr")]
    print(f"\n=== robustness (baseline {base}; best tuned ratchet {best_ratchet}) ===")
    for seg in segkeys:
        b = cum(base, seg)
        bt = max(cum(x, seg) for x in names if x.startswith("ratchet"))
        beat = sum(cum(x, seg) > b for x in prs)
        beat_t = sum(cum(x, seg) > bt for x in prs)
        print(f"{seg:<7} ratchet0.5={b:7.2f} bestRatchet={bt:7.2f} | pr beats r0.5: {beat}/{len(prs)}  beats bestRatchet: {beat_t}/{len(prs)}")
    top = sorted(prs, key=lambda x: -cum(x, "full"))[:6]
    for k in (2, 3):
        segs = [s for s in segkeys if s.startswith(f"s{k}_")]
        ok = [all(cum(x, s) > cum(base, s) for s in segs) for x in top]
        print(f"top6 pr beat ratchet0.5 in ALL {k}-split segments: {sum(ok)}/6")
    allseg = [all(cum(x, s) > cum(base, s) for s in segkeys if s != "full") for x in prs]
    print(f"pr combos beating ratchet0.5 in ALL 5 split segments: {sum(allseg)}/{len(prs)}")

    # 인접 평활도: 각 격자점과 4-이웃 평균의 차 / 격자 전체 표준편차
    import statistics

    vals = {(m, r): cum(f"pr(min={m},r={r})", "full") for m in MINS for r in RATIOS}
    sd = statistics.pstdev(vals.values())
    diffs = []
    for (m, r), v in vals.items():
        mi, ri = MINS.index(m), RATIOS.index(r)
        nb = [vals[(MINS[a], RATIOS[b])] for a, b in ((mi - 1, ri), (mi + 1, ri), (mi, ri - 1), (mi, ri + 1)) if 0 <= a < len(MINS) and 0 <= b < len(RATIOS)]
        diffs.append(abs(v - sum(nb) / len(nb)))
    print(f"\ngrid mean={statistics.mean(vals.values()):.2f} sd={sd:.2f} max={max(vals.values()):.2f} min={min(vals.values()):.2f}")
    print(f"mean |point - neighbor avg| = {statistics.mean(diffs):.2f}, max = {max(diffs):.2f}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--code", default=TARGET_CODE)
    p.add_argument("--days", type=int, default=180)
    a = p.parse_args()
    asyncio.run(main_async(a.code, a.days))


if __name__ == "__main__":
    main()
