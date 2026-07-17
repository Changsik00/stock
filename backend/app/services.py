"""Builds daily time series.

``get_index_series``/``get_futures_series`` below are the *original* KRX Open API
implementation — kept as-is (unused by the router since 2026-07) because
krx_client.py itself must stay per PLAN.md, and this code documents how they were
built. The KRX Open API dataset approval is currently rejected (403), so
``routers/markets.py`` no longer calls these; it reads ``index_ohlcv`` in the DB
instead via ``get_market_series_from_db`` (populated by collectors/ohlcv.py —
yfinance/네이버, see PLAN.md §5.4/§7).
"""

import logging
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .krx_client import KRXClient
from .models import IndexOhlcv

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


# 라우터 market 경로 파라미터(kospi/kosdaq/futures) -> index_ohlcv.market 값
# (models.py: kospi/kosdaq/k200_futures).
DB_MARKET = {"kospi": "kospi", "kosdaq": "kosdaq", "futures": "k200_futures"}


async def get_market_series_from_db(
    session: AsyncSession, market: str, days: int
) -> list[dict]:
    """market(kospi/kosdaq/futures)의 최근 `days` 거래일 지수 일봉을 DB에서 조회.

    PLAN.md §5.4 "DB 캐싱 우선" — 외부 API를 직접 호출하지 않고 collectors/ohlcv.py가
    미리 적재해 둔 index_ohlcv만 읽는다. 데이터가 없으면 빈 리스트(에러 아님).

    응답 형태는 위 get_index_series/get_futures_series(KRX 기반, 현재는 미사용)와
    동일하게 맞춘다 — 프런트가 그대로 동작하도록: date는 "YYYYMMDD" 문자열,
    changeRate는 index_ohlcv에 컬럼이 없어(KRX가 주던 FLUC_RT 대신) 하루 더 가져와
    전일 종가 대비로 계산한다. value(거래대금)는 현재 소스(yfinance/네이버)가
    제공하지 않아 항상 0이다(§7 리스크 참고).

    open/high/low는 index_ohlcv에 그대로 있어 함께 내려준다 (프런트 CandleChart용,
    2026-07-17 추가 — 기존 필드는 그대로 두는 additive 변경).
    """
    db_market = DB_MARKET.get(market)
    if db_market is None:
        raise ValueError(f"unknown market {market!r}, expected one of {sorted(DB_MARKET)}")

    # changeRate 계산용 버퍼로 하루치를 더 가져온다 — 나중에 맨 앞 한 건을 잘라낸다.
    stmt = (
        select(IndexOhlcv)
        .where(IndexOhlcv.market == db_market)
        .order_by(IndexOhlcv.date.desc())
        .limit(days + 1)
    )
    rows = list(reversed((await session.execute(stmt)).scalars().all()))

    out: list[dict] = []
    prev_close: float | None = None
    for r in rows:
        close = float(r.close) if r.close is not None else None
        change_rate = 0.0
        if prev_close is not None and close is not None:
            change_rate = (close - prev_close) / prev_close * 100
        out.append(
            {
                "date": r.date.strftime("%Y%m%d"),
                "open": float(r.open) if r.open is not None else None,
                "high": float(r.high) if r.high is not None else None,
                "low": float(r.low) if r.low is not None else None,
                "close": close,
                "changeRate": round(change_rate, 4),
                "volume": int(r.volume) if r.volume is not None else 0,
                "value": int(r.value) if r.value is not None else 0,
            }
        )
        if close is not None:
            prev_close = close

    return out[-days:] if len(out) > days else out
