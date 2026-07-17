"""WTI/Brent daily close (PLAN.md §3 — 유가).

1차 소스는 yfinance(`CL=F`/`BZ=F`)이고, 실패 시(429 등 — yfinance는 2024~2025에 반복적으로
rate limit 사태가 있었음) FRED의 무료·무인증 CSV 엔드포인트(`DCOILWTICO`/`DCOILBRENTEU`)로
자동 폴백한다. 반환되는 각 행에 ``source``(``yfinance`` 또는 ``fred``)를 기록해 어느
소스에서 왔는지 macro_series.source 컬럼에 남길 수 있게 한다.
"""

from __future__ import annotations

import datetime as dt
import logging
import math

import requests
import yfinance as yf

logger = logging.getLogger(__name__)

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

SYMBOLS = {
    "wti": {"yfinance": "CL=F", "fred": "DCOILWTICO"},
    "brent": {"yfinance": "BZ=F", "fred": "DCOILBRENTEU"},
}


class CommoditiesError(Exception):
    """Raised when both yfinance and the FRED fallback fail to return data."""


def _fetch_yfinance(symbol: str, start: dt.date, end: dt.date) -> list[dict]:
    ticker = yf.Ticker(symbol)
    # yfinance's `end` is exclusive, so add a day to include the requested end date.
    df = ticker.history(
        start=start.isoformat(),
        end=(end + dt.timedelta(days=1)).isoformat(),
        auto_adjust=False,
    )
    if df.empty:
        raise CommoditiesError(f"yfinance returned no rows for {symbol}")

    out: list[dict] = []
    for idx, row in df.iterrows():
        close = row.get("Close")
        if close is None or (isinstance(close, float) and math.isnan(close)):
            continue
        out.append({"date": idx.date(), "value": float(close)})
    return out


def _fetch_fred(series_id: str, start: dt.date, end: dt.date, timeout: int = 15) -> list[dict]:
    resp = requests.get(
        FRED_CSV_URL,
        params={"id": series_id, "cosd": start.isoformat(), "coed": end.isoformat()},
        timeout=timeout,
    )
    resp.raise_for_status()

    lines = resp.text.splitlines()
    if not lines or "date" not in lines[0].lower():
        raise CommoditiesError(f"unexpected FRED CSV response for {series_id}: {resp.text[:200]!r}")

    out: list[dict] = []
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) != 2:
            continue
        date_str, value_str = parts
        value_str = value_str.strip()
        if not value_str or value_str == ".":
            continue  # FRED leaves the value blank on non-trading days
        try:
            value = float(value_str)
        except ValueError:
            continue
        out.append({"date": dt.date.fromisoformat(date_str), "value": value})

    if not out:
        raise CommoditiesError(f"FRED returned no usable rows for {series_id}")
    return out


def fetch_oil_series(series: str, start: dt.date, end: dt.date) -> list[dict]:
    """Fetch WTI/Brent daily close for [start, end], yfinance first then FRED fallback.

    Returns rows sorted ascending: ``[{"date", "value", "source"}, ...]``.
    """
    if series not in SYMBOLS:
        raise ValueError(f"unknown oil series {series!r}, expected one of {sorted(SYMBOLS)}")

    symbols = SYMBOLS[series]

    try:
        rows = _fetch_yfinance(symbols["yfinance"], start, end)
        for row in rows:
            row["source"] = "yfinance"
        rows.sort(key=lambda r: r["date"])
        return rows
    except Exception as e:  # yfinance raises assorted errors (HTTP 429, curl_cffi, ...)
        logger.warning(
            "yfinance 조회 실패(%s, %s) — FRED CSV로 폴백합니다", series, e
        )

    rows = _fetch_fred(symbols["fred"], start, end)
    for row in rows:
        row["source"] = "fred"
    rows.sort(key=lambda r: r["date"])
    return rows
