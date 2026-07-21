"""전일 미국장 4대 지수(S&P500/나스닥/다우/필라델피아반도체SOX) 일별 종가
(PLAN.md §5.8 — 전일 미국장 + SOX).

yfinance 1차, 실패 시(429 등) FRED CSV로 자동 폴백한다 — ``commodities.py``의
``fetch_oil_series``/``naver_fx.py``의 ``fetch_usdkrw``와 동일한 패턴이라 파싱
로직(``commodities._fetch_yfinance``/``commodities._fetch_fred``)을 그대로
재사용하고 여기서는 중복 구현하지 않는다.

**실측(2026-07-22)**: yfinance로 S&P500(``^GSPC``)·나스닥종합(``^IXIC``)·다우
(``^DJI``)·필라델피아반도체지수(``^SOX``) 전부 정상 조회 확인(전일 07-21 종가까지
정상 수신, rate limit 없음).

FRED 무료 대체 시리즈가 있는 건 S&P500(``SP500``)·나스닥(``NASDAQCOM``)뿐이고,
다우·SOX는 FRED에 무료 시리즈가 없다(``SYMBOLS[...]["fred"] is None``) — 이
경우 yfinance가 실패하면 폴백 없이 그대로 예외를 전파한다(§7 리스크 표 참고).
"""

from __future__ import annotations

import datetime as dt
import logging

from . import commodities

logger = logging.getLogger(__name__)

SYMBOLS = {
    "us_sp500": {"yfinance": "^GSPC", "fred": "SP500"},
    "us_nasdaq": {"yfinance": "^IXIC", "fred": "NASDAQCOM"},
    "us_dow": {"yfinance": "^DJI", "fred": None},
    "us_sox": {"yfinance": "^SOX", "fred": None},
}


class UsIndicesError(Exception):
    """Raised when yfinance fails and no FRED fallback exists for the series (dow/sox)."""


def fetch_us_index_series(series: str, start: dt.date, end: dt.date) -> list[dict]:
    """Fetch a US index daily close for [start, end], yfinance first then FRED fallback.

    ``series``는 ``SYMBOLS``의 키(``us_sp500``/``us_nasdaq``/``us_dow``/``us_sox``)여야
    한다. 다우·SOX는 FRED 대체 시리즈가 없어(``fred`` 값이 None) yfinance 실패 시
    폴백을 건너뛰고 그 예외를 그대로 전파한다.

    Returns rows sorted ascending: ``[{"date", "value", "source"}, ...]``.
    """
    if series not in SYMBOLS:
        raise ValueError(f"unknown us index series {series!r}, expected one of {sorted(SYMBOLS)}")

    symbols = SYMBOLS[series]

    try:
        rows = commodities._fetch_yfinance(symbols["yfinance"], start, end)
        for row in rows:
            row["source"] = "yfinance"
        rows.sort(key=lambda r: r["date"])
        return rows
    except Exception as e:  # yfinance raises assorted errors (HTTP 429, curl_cffi, ...)
        if symbols["fred"] is None:
            logger.warning(
                "yfinance 조회 실패(%s, %s) — FRED 대체 시리즈가 없어 그대로 전파합니다", series, e
            )
            raise
        logger.warning(
            "yfinance 조회 실패(%s, %s) — FRED CSV로 폴백합니다", series, e
        )

    rows = commodities._fetch_fred(symbols["fred"], start, end)
    for row in rows:
        row["source"] = "fred"
    rows.sort(key=lambda r: r["date"])
    return rows
