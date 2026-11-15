"""flow_rank 백필 / 검증 — stock_flow(개별주) + 키움 ka10059(ETF) 기반.

배경: 네이버 sise_deal_rank_iframe이 2026-09-17부터 410이라 flow_rank가 09-15 이후
비었다. collectors/flow_rank.py가 새 소스로 날짜별 랭킹을 만들 수 있으므로, 이
스크립트는 stock_flow가 있고 flow_rank가 비어 있는 거래일을 채운다.

Usage:
    # 기본: stock_flow는 있고 flow_rank는 없는 모든 거래일 백필
    python -m scripts.backfill_flow_rank
    # 기간 지정
    python -m scripts.backfill_flow_rank --start 2026-09-16 --end 2026-11-13
    # 저장하지 않는 비교 모드(기존 네이버 적재분과 대조, DB 불변)
    python -m scripts.backfill_flow_rank --compare 2026-09-10,2026-09-11,2026-09-14,2026-09-15
"""

import argparse
import asyncio
import datetime as dt
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_flow_rank")

from sqlalchemy import select  # noqa: E402

from app.collectors import flow_rank as fr  # noqa: E402
from app.db import async_session_factory  # noqa: E402
from app.models import FlowRank, StockFlow  # noqa: E402


async def _target_dates(session, start, end) -> list[dt.date]:
    q = select(StockFlow.date).distinct()
    if start:
        q = q.where(StockFlow.date >= start)
    if end:
        q = q.where(StockFlow.date <= end)
    flow_dates = set((await session.execute(q)).scalars().all())
    have = set((await session.execute(select(FlowRank.date).distinct())).scalars().all())
    return sorted(flow_dates - have)


async def backfill(start, end) -> None:
    async with async_session_factory() as session:
        dates = await _target_dates(session, start, end)
        logger.info("백필 대상 %d일: %s ~ %s", len(dates), dates[:1], dates[-1:])
        if not dates:
            return
        total, notes = await fr.collect_flow_rank_dates(session, dates)
        await session.commit()
        for n in notes:
            logger.info(n)
        logger.info("완료: %d행 적재", total)


async def compare(dates: list[dt.date], top: int = 20) -> None:
    """저장하지 않고 새 방식 결과와 DB의 기존 행을 비교해 출력한다."""
    async with async_session_factory() as session:
        etf_hist, names, live_aum = await fr.prepare_etf_data(session, max(dates))
        for d in dates:
            ranks = await fr.compute_rankings(session, d, etf_hist, names, live_aum)
            print(f"=== {d} ===")
            for (investor, side), rows in sorted(ranks.items()):
                old = (
                    await session.execute(
                        select(FlowRank)
                        .where(
                            FlowRank.date == d,
                            FlowRank.investor == investor,
                            FlowRank.side == side,
                            FlowRank.rank <= top,
                        )
                        .order_by(FlowRank.rank)
                    )
                ).scalars().all()
                new_top = {r["code"]: r["net_value"] for r in rows[:top]}
                old_map = {o.code: o.net_value for o in old}
                common = set(new_top) & set(old_map)
                diffs = [
                    abs(new_top[c] - old_map[c]) / max(old_map[c], 1) * 100 for c in common
                ]
                med = sorted(diffs)[len(diffs) // 2] if diffs else float("nan")
                mx = max(diffs) if diffs else float("nan")
                print(
                    f"{investor}/{side}: 상위{top} 코드 일치 {len(common)}/{len(old_map)} "
                    f"| 금액차 중앙값 {med:.2f}% 최대 {mx:.2f}%"
                )
                missing = [(o.rank, o.code, o.name, o.net_value) for o in old if o.code not in new_top]
                if missing:
                    print("   기존에만 있음:", missing)
            # ETF 단위 점검 — KODEX 레버리지
            for (investor, side), rows in sorted(ranks.items()):
                for i, r in enumerate(rows, 1):
                    if r["code"] == "122630":
                        old = (
                            await session.execute(
                                select(FlowRank.net_value).where(
                                    FlowRank.date == d,
                                    FlowRank.investor == investor,
                                    FlowRank.side == side,
                                    FlowRank.code == "122630",
                                )
                            )
                        ).scalar_one_or_none()
                        print(f"   122630 {investor}/{side}: 신 rank{i} {r['net_value']} / 구 {old}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=dt.date.fromisoformat)
    p.add_argument("--end", type=dt.date.fromisoformat)
    p.add_argument("--compare", help="쉼표 구분 날짜 — 저장 없이 기존 행과 비교")
    a = p.parse_args()
    if a.compare:
        asyncio.run(compare([dt.date.fromisoformat(x) for x in a.compare.split(",")]))
    else:
        asyncio.run(backfill(a.start, a.end))


if __name__ == "__main__":
    main()
