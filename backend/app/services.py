"""Builds daily time series from the KRX Open API's single-day snapshot endpoints."""

import logging
from datetime import date, timedelta

from .krx_client import KRXClient

logger = logging.getLogger("krx")

# idx/{market}_dd_trd returns one row per index in the KOSPI/KOSDAQ "series"
# (코스피, 코스피 200, 코스피 100, ... / 코스닥, 코스닥 150, ...). We want the
# single headline index for each market.
INDEX_NAME = {
    "kospi": "코스피",
    "kosdaq": "코스닥",
}

# 코스피 200 선물 최근월물(가장 거래량이 큰 근월물)을 대표 선물 시세로 사용.
FUTURES_PRODUCT_NAME = "코스피 200 선물"

MAX_LOOKBACK_DAYS = 550


def _trading_days_back(n_days: int):
    """Yield up to MAX_LOOKBACK_DAYS calendar weekdays, most recent first."""
    d = date.today()
    count = 0
    while count < MAX_LOOKBACK_DAYS:
        d -= timedelta(days=1)
        if d.weekday() < 5:  # Mon-Fri; KRX holidays are simply skipped as empty days
            count += 1
            yield d


def get_index_series(client: KRXClient, market: str, days: int) -> list[dict]:
    endpoint = f"{market}_dd_trd"
    target_name = INDEX_NAME[market]
    out = []
    for d in _trading_days_back(days):
        bas_dd = d.strftime("%Y%m%d")
        rows = client.daily("idx", endpoint, bas_dd)
        row = next((r for r in rows if r.get("IDX_NM") == target_name), None)
        if row is None:
            if rows:
                logger.warning(
                    "no row named %r on %s; names seen: %s",
                    target_name,
                    bas_dd,
                    sorted({r.get("IDX_NM") for r in rows}),
                )
            continue
        out.append(
            {
                "date": bas_dd,
                "close": float(row.get("CLSPRC_IDX", 0) or 0),
                "changeRate": float(row.get("FLUC_RT", 0) or 0),
                "volume": int(float(row.get("ACC_TRDVOL", 0) or 0)),
                "value": int(float(row.get("ACC_TRDVAL", 0) or 0)),
            }
        )
        if len(out) >= days:
            break
    out.reverse()
    return out


def get_futures_series(client: KRXClient, days: int) -> list[dict]:
    out = []
    for d in _trading_days_back(days):
        bas_dd = d.strftime("%Y%m%d")
        rows = client.daily("drv", "fut_bydd_trd", bas_dd)
        candidates = [
            r
            for r in rows
            if (r.get("PROD_NM") or r.get("ISU_NM") or "").startswith(FUTURES_PRODUCT_NAME)
        ]
        if not candidates:
            continue
        # 최근월물 = 해당일 거래량이 가장 큰 종목
        row = max(candidates, key=lambda r: float(r.get("ACC_TRDVOL", 0) or 0))
        out.append(
            {
                "date": bas_dd,
                "close": float(row.get("TDD_CLSPRC", 0) or 0),
                "changeRate": float(row.get("FLUC_RT", 0) or 0),
                "volume": int(float(row.get("ACC_TRDVOL", 0) or 0)),
                "value": int(float(row.get("ACC_TRDVAL", 0) or 0)),
                "contract": row.get("ISU_NM"),
            }
        )
        if len(out) >= days:
            break
    out.reverse()
    return out
