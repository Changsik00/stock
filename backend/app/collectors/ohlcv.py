"""코스피/코스닥/코스피200선물 지수 일봉 수집 -> index_ohlcv upsert (PLAN.md §5.2/§5.4).

KRX Open API(data-dbg.krx.co.kr)가 403(서비스 승인 미비, 2026-07 확인)이라 지수
일봉 소스를 이 배치로 교체했다:

1. **kospi/kosdaq**: yfinance(``^KS11``/``^KQ11``) 1차 — clients/commodities.py와
   동일한 이유로 이미 의존성이 있고 무료/무인증. 실패하면(429 등)
   clients/naver_index.py(네이버 fchart)로 폴백한다.
2. **k200_futures**: yfinance에 코스피200 선물 심볼이 없어 clients/naver_index.py만
   사용한다.

거래대금(value, 원화)은 두 소스 모두 제공하지 않아 NULL로 남는다 — 추후 키움 차트
TR로 교체되면 채워질 예정(PLAN.md §7 리스크 참고).

REGISTRY["ohlcv"]로 등록된다 (routers/admin.py가 이 모듈을 import해서 등록을
트리거함 — collectors/macro.py와 동일한 패턴).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math
import time

import yfinance as yf
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import naver_index
from ..models import IndexOhlcv
from .base import REGISTRY

logger = logging.getLogger(__name__)

# 매일 1건만 필요하지만 휴장일/배치 실패로 며칠 비었을 수 있어(collectors/macro.py와
# 동일한 이유) 넉넉히 최근 며칠을 함께 조회해 upsert한다(idempotent라 안전).
LOOKBACK_DAYS = 10

YFINANCE_TICKERS = {"kospi": "^KS11", "kosdaq": "^KQ11"}

NAVER_REQUEST_DELAY_SECONDS = 0.5

MARKETS = ("kospi", "kosdaq", "k200_futures")


def _to_float(v) -> float | None:
    if v is None:
        return None
    f = float(v)
    return None if math.isnan(f) else f


def _to_int(v) -> int | None:
    f = _to_float(v)
    return None if f is None else int(f)


def _fetch_yfinance(ticker: str, start: dt.date, end: dt.date) -> list[dict]:
    df = yf.Ticker(ticker).history(
        start=start.isoformat(),
        # yfinance's `end` is exclusive, so add a day to include the requested end date.
        end=(end + dt.timedelta(days=1)).isoformat(),
        auto_adjust=False,
    )
    if df.empty:
        raise ValueError(f"yfinance returned no rows for {ticker}")

    out: list[dict] = []
    for idx, row in df.iterrows():
        close = _to_float(row.get("Close"))
        if close is None:
            continue
        out.append(
            {
                "date": idx.date(),
                "open": _to_float(row.get("Open")),
                "high": _to_float(row.get("High")),
                "low": _to_float(row.get("Low")),
                "close": close,
                "volume": _to_int(row.get("Volume")),
            }
        )
    return out


def fetch_market_rows(market: str, start: dt.date, end: dt.date) -> list[dict]:
    """market(kospi/kosdaq/k200_futures)의 일봉을 [start, end]로 가져온다.

    Blocking(requests/yfinance) — 호출측(collect_ohlcv)이 asyncio.to_thread로 감싼다.
    """
    ticker = YFINANCE_TICKERS.get(market)
    if ticker is not None:
        try:
            rows = _fetch_yfinance(ticker, start, end)
            if rows:
                return rows
            logger.warning("yfinance %s(%s) returned 0 rows — 네이버로 폴백", market, ticker)
        except Exception as e:  # noqa: BLE001 - yfinance raises assorted errors (HTTP 429, curl_cffi, ...)
            logger.warning("yfinance 조회 실패(%s, %s) — 네이버로 폴백합니다", market, e)

    time.sleep(NAVER_REQUEST_DELAY_SECONDS)
    return naver_index.fetch_index_series(market, start, end)


async def _upsert_rows(session: AsyncSession, market: str, rows: list[dict]) -> int:
    count = 0
    for row in rows:
        stmt = pg_insert(IndexOhlcv).values(
            market=market,
            date=row["date"],
            open=row.get("open"),
            high=row.get("high"),
            low=row.get("low"),
            close=row.get("close"),
            volume=row.get("volume"),
            value=row.get("value"),  # 두 소스 모두 거래대금 미제공 -> 항상 NULL(§7 참고)
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[IndexOhlcv.market, IndexOhlcv.date],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "value": stmt.excluded.value,
            },
        )
        await session.execute(stmt)
        count += 1
    return count


async def collect_ohlcv(session: AsyncSession, target_date: dt.date) -> int:
    """kospi/kosdaq/k200_futures의 target_date 전후 일봉을 index_ohlcv에 upsert."""
    start = target_date - dt.timedelta(days=LOOKBACK_DAYS)
    total = 0
    for market in MARKETS:
        rows = await asyncio.to_thread(fetch_market_rows, market, start, target_date)
        total += await _upsert_rows(session, market, rows)
    return total


REGISTRY["ohlcv"] = collect_ohlcv
