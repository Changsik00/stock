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

# 나스닥선물(NQ=F) 심볼 — 준실시간 인트라데이 전용(아래 fetch_nasdaq_futures_intraday).
NASDAQ_FUTURES_SYMBOL = "NQ=F"


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


def fetch_nasdaq_futures_intraday(bars: int = 50) -> list[dict]:
    """나스닥선물(``NQ=F``) 준실시간 5분봉 (PLAN.md §5.50-1/§5.50-5).

    위의 다른 함수들(``fetch_us_index_series``)은 전부 EOD(일별)라 그대로
    재사용할 수 없어, ``yfinance.Ticker("NQ=F").history(period="1d",
    interval="5m")``를 직접 호출하는 인트라데이 전용 함수를 별도로 둔다
    (2026-08-03 실측: 5분봉 정상 수신 확인 — CME Globex가 KST 주간에도 계속
    열려 있어 "지금"에 가까운 값을 준다, PLAN.md §5.50-1 참고).

    장중 나스닥선물은 FRED에 대체 시리즈가 없다 — ``fetch_us_index_series``와
    달리 폴백이 없고, yfinance 실패는 그대로 예외로 전파한다(호출측 라우터가
    502로 변환).

    Returns 오름차순(과거→최신) ``[{"time": iso8601, "close": float}, ...]``
    최근 ``bars``개(기본 50 — 5분봉 50개 ≈ 4시간). 빈 응답이면 빈 리스트.
    """
    # 지연 import — commodities.py의 동일 조치와 같은 이유(PLAN.md §5.58,
    # 모듈 레벨 `import yfinance`가 backend 기동 경로에 물려 매번 로드되는 문제).
    import yfinance as yf

    ticker = yf.Ticker(NASDAQ_FUTURES_SYMBOL)
    hist = ticker.history(period="1d", interval="5m")
    if hist.empty:
        return []

    out: list[dict] = []
    for ts, row in hist.iterrows():
        close = row.get("Close")
        if close is None or (isinstance(close, float) and close != close):  # NaN 체크
            continue
        out.append({"time": ts.isoformat(), "close": float(close)})

    return out[-bars:]
