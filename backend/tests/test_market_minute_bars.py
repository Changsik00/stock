"""지수/ETF 1분봉 적재기 단위테스트 (PLAN.md §5.93) — 파서는 순수, upsert는 롤백 트랜잭션."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.market_minute_bars import _upsert, parse_all_days, to_rows
from app.db import engine
from app.models import IndexIntradayBar


def _row(tm: str, o: str, c: str, vol: str = "10"):
    return {"cntr_tm": tm, "open_pric": o, "high_pric": c, "low_pric": o, "cur_prc": c, "trde_qty": vol}


def test_parse_all_days_multi_day_and_scale():
    data = {
        "inds_min_pole_qry": [
            _row("20261106090100", "+655400", "-655500"),
            _row("20261106090000", "+655388", "+655400"),
            _row("20261105153000", "-650000", "-650100"),
            _row("20261106090000", "+655388", "+655400"),  # 중복
        ]
    }
    bars = parse_all_days(data, "ka20005")
    keys = sorted((b["date"], b["time"]) for b in bars)
    assert keys == [("20261105", "1530"), ("20261106", "0900"), ("20261106", "0901")]
    first = next(b for b in bars if (b["date"], b["time"]) == ("20261106", "0900"))
    assert first["open"] == 6553.88  # ÷100 보정


def test_parse_all_days_empty_and_etf_int():
    assert parse_all_days({}, "ka10080") == []
    bars = parse_all_days({"stk_min_pole_chart_qry": [_row("20261106090000", "+100", "-101")]}, "ka10080")
    assert bars[0]["close"] == 101 and isinstance(bars[0]["close"], int)


def test_to_rows_date_conversion():
    rows = to_rows("001", [{"date": "20261106", "time": "0900", "open": 1.0, "volume": 3}, {"date": None}])
    assert len(rows) == 1 and rows[0]["date"] == dt.date(2026, 11, 6)


@pytest.mark.asyncio
async def test_upsert_idempotent_rolled_back():
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            session = AsyncSession(bind=conn, expire_on_commit=False)
            rows = to_rows("TST", [
                {"date": "20261106", "time": "0900", "open": 1.5, "high": 2.0, "low": 1.0, "close": 1.8, "volume": 5},
                {"date": "20261106", "time": "0901", "open": 1.8, "high": 2.0, "low": 1.7, "close": 1.9, "volume": 6},
            ])
            await _upsert(session, IndexIntradayBar, rows)
            rows[0]["close"] = 9.99
            await _upsert(session, IndexIntradayBar, rows)  # 재실행: 중복 없이 갱신
            cnt = (await session.execute(select(func.count()).select_from(IndexIntradayBar).where(IndexIntradayBar.code == "TST"))).scalar_one()
            close = (await session.execute(select(IndexIntradayBar.close).where(IndexIntradayBar.code == "TST", IndexIntradayBar.time == "0900"))).scalar_one()
            assert cnt == 2 and float(close) == 9.99
        finally:
            await trans.rollback()
