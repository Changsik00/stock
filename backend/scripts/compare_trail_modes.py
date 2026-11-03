"""트레일 모드 비교 일회성 스크립트 — 1분봉 백테스트(자동매매 대상 종목).

`StockIntradayBar`를 읽어(라우터 `/auto-trade/intraday-backtest`와 동일한 방식)
ratchet(현재 라이브 규칙)과 profit_ratio/profit_grace/profit_grace_vol 변형들을
같은 봉 데이터로 돌려 `_summarize` 값을 표로 출력한다. 읽기 전용.

Usage (Docker):
    docker exec stock-backend-1 python -m scripts.compare_trail_modes
    docker exec stock-backend-1 python -m scripts.compare_trail_modes --days 60
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
from app.models import StockIntradayBar  # noqa: E402
from app.quant.auto_trade_intraday_backtest import run_intraday_backtest  # noqa: E402

CASES: list[tuple[str, dict]] = [
    ("ratchet(0.5)", {"trail_mode": "ratchet"}),
    ("profit_ratio", {"trail_mode": "profit_ratio"}),
    ("profit_grace g=1", {"trail_mode": "profit_grace", "grace_bars": 1}),
    ("profit_grace g=2", {"trail_mode": "profit_grace", "grace_bars": 2}),
    ("profit_grace g=3", {"trail_mode": "profit_grace", "grace_bars": 3}),
    ("profit_grace g=5", {"trail_mode": "profit_grace", "grace_bars": 5}),
    ("profit_grace_vol g=3", {"trail_mode": "profit_grace_vol", "grace_bars": 3}),
]


async def load_bars(code: str, days: int) -> list[dict]:
    since = dt.date.today() - dt.timedelta(days=days)
    stmt = (
        select(StockIntradayBar)
        .where(StockIntradayBar.code == code, StockIntradayBar.date >= since)
        .order_by(StockIntradayBar.date, StockIntradayBar.time)
    )
    async with async_session_factory() as session:
        rows = (await session.execute(stmt)).scalars().all()
    return [
        {"date": r.date.isoformat(), "time": r.time, "close": r.close, "volume": r.volume}
        for r in rows
        if r.close is not None and r.volume is not None
    ]


def _f(v) -> str:
    return "-" if v is None else f"{v:.2f}"


async def run(code: str, days: int) -> None:
    bars = await load_bars(code, days)
    n_days = len({b["date"] for b in bars})
    print(f"code={code} days<={days} bars={len(bars)} trading_days={n_days}")
    header = f"{'mode':<22}{'n':>4}{'win%':>8}{'avgW%':>8}{'avgL%':>8}{'cum%':>9}{'best%':>8}{'worst%':>8}"
    print(header)
    print("-" * len(header))
    for name, kwargs in CASES:
        s = run_intraday_backtest(bars, **kwargs)["summary"]
        print(
            f"{name:<22}{s['n']:>4}{_f(s['win_rate_pct']):>8}{_f(s['avg_win_pct']):>8}"
            f"{_f(s['avg_loss_pct']):>8}{_f(s['cumulative_return_pct']):>9}"
            f"{_f(s['best_trade_pct']):>8}{_f(s['worst_trade_pct']):>8}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", default=TARGET_CODE)
    parser.add_argument("--days", type=int, default=180)
    args = parser.parse_args()
    asyncio.run(run(args.code, args.days))


if __name__ == "__main__":
    main()
