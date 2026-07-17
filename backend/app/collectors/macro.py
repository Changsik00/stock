"""환율(ECOS) + 유가(commodities: yfinance→FRED) + KOFIA(예탁금/신용융자/대차잔고)
수집 → macro_series upsert.

REGISTRY["macro"]로 등록된다 (routers/admin.py가 이 모듈을 import해서 등록을 트리거함).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import commodities, ecos, kofia
from ..models import MacroSeries
from .base import REGISTRY

logger = logging.getLogger(__name__)

# 매일 1건만 필요하지만, 휴장일/배치 실패로 며칠 비었을 수 있으므로 넉넉히 최근
# 며칠을 함께 조회해 upsert한다 (idempotent라 재실행해도 안전).
LOOKBACK_DAYS = 10

OIL_SERIES = ("wti", "brent")


async def upsert_series_rows(session: AsyncSession, rows: list[dict], series: str) -> int:
    """Upsert a list of {"date", "value", "source"} rows into macro_series[series]."""
    count = 0
    for row in rows:
        stmt = pg_insert(MacroSeries).values(
            series=series,
            date=row["date"],
            value=row["value"],
            source=row.get("source"),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[MacroSeries.series, MacroSeries.date],
            set_={"value": stmt.excluded.value, "source": stmt.excluded.source},
        )
        await session.execute(stmt)
        count += 1
    return count


def _fetch_kofia_investor_deposit(start: dt.date, end: dt.date) -> list[dict]:
    with httpx.Client() as client:
        return kofia.fetch_investor_deposit(client, start, end)


def _fetch_kofia_credit_loan(start: dt.date, end: dt.date) -> dict[str, list[dict]]:
    with httpx.Client() as client:
        return kofia.fetch_credit_loan(client, start, end)


def _fetch_kofia_lending_balance(start: dt.date, end: dt.date) -> list[dict]:
    with httpx.Client() as client:
        return kofia.fetch_lending_balance(client, start, end)


async def _collect_kofia(session: AsyncSession, start: dt.date, target_date: dt.date) -> int:
    """KOFIA freesis 시리즈 수집 — 비공식 통계 화면 파싱이라 사이트 개편 등으로
    깨질 수 있으므로, 여기서 발생한 예외는 개별적으로 흡수해 다른 매크로 수집
    (환율/유가)이나 kofia의 다른 시리즈를 막지 않는다."""
    total = 0

    try:
        rows = await asyncio.to_thread(_fetch_kofia_investor_deposit, start, target_date)
        for row in rows:
            row["source"] = "kofia"
        total += await upsert_series_rows(session, rows, "investor_deposit")
    except Exception as e:  # noqa: BLE001 - deliberately broad, see docstring
        logger.warning("kofia investor_deposit 수집 실패: %s", e)

    try:
        credit_rows = await asyncio.to_thread(_fetch_kofia_credit_loan, start, target_date)
        for series, rows in credit_rows.items():
            for row in rows:
                row["source"] = "kofia"
            total += await upsert_series_rows(session, rows, series)
    except Exception as e:  # noqa: BLE001
        logger.warning("kofia credit_loan 수집 실패: %s", e)

    try:
        lending_rows = await asyncio.to_thread(_fetch_kofia_lending_balance, start, target_date)
        for row in lending_rows:
            row["source"] = "kofia"
        total += await upsert_series_rows(session, lending_rows, "lending_balance")
    except Exception as e:  # noqa: BLE001
        logger.warning("kofia lending_balance 수집 실패: %s", e)

    return total


async def collect_macro(session: AsyncSession, target_date: dt.date) -> int:
    """Fetch USD/KRW (ECOS) + WTI/Brent (yfinance/FRED) + KOFIA(예탁금/신용융자/대차잔고)
    around target_date, upsert all."""
    start = target_date - dt.timedelta(days=LOOKBACK_DAYS)
    total = 0

    # Blocking network calls (requests/yfinance/httpx-sync) run in a thread so they
    # don't stall the event loop while the API server is also serving requests.
    usdkrw_rows = await asyncio.to_thread(ecos.fetch_usdkrw, start, target_date)
    for row in usdkrw_rows:
        row["source"] = "ecos"
    total += await upsert_series_rows(session, usdkrw_rows, "usdkrw")

    for series in OIL_SERIES:
        oil_rows = await asyncio.to_thread(commodities.fetch_oil_series, series, start, target_date)
        total += await upsert_series_rows(session, oil_rows, series)

    total += await _collect_kofia(session, start, target_date)

    return total


REGISTRY["macro"] = collect_macro
