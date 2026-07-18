"""코스피/코스닥/코스피200선물 일봉 — 네이버 증권 fchart(siseJson) API (PLAN.md §3/§5.4).

KRX Open API(data-dbg.krx.co.kr)가 403(서비스 승인 미비)으로 막혀 있어(2026-07),
지수 일봉 소스를 이 모듈로 교체했다. ``fchart.stock.naver.com/siseJson.naver``는
Open API 발급 이전부터 흔히 스크레이핑에 쓰이던 네이버의 구(舊) 차트 API로, 인증/키
없이 심볼 문자열 하나로 지수·선물 모두 조회된다(Playwright로 m.stock.naver.com의
지수/선물 상세 화면을 열어 실제 XHR을 캡처해 확인 — 2026-07-17).

지원 심볼(SYMBOLS): kospi/kosdaq/k200_futures/kospi200. k200_futures는 코스피 200 선물
근월물(연결선물, 코드 ``FUT``)이며, KOSPI200 **지수**가 아니라 실제 선물 종가다.
kospi200은 그 반대로 KOSPI200 **현물지수** 자체다(코드 ``KPI200``, PLAN.md §4.5-3
실호출 검증 — 2026-07-16 종가 1080.36). 선물종가(k200_futures) − 현물지수(kospi200) =
베이시스 계산에 쓰인다(routers/basis.py).

응답은 JSON이 아니라 홑따옴표/겹따옴표가 섞인 JS 배열 리터럴 텍스트라서
정규식으로 행을 추출한다(예: ``["20260716", 6960.5, 6995.93, 6730.87, 6820.6,
424280, 0.0]`` = 날짜/시가/고가/저가/종가/거래량/외국인소진율). 거래대금(원화
금액)은 이 API에 없어 반환 행에 포함하지 않는다 — 호출 측(collectors/ohlcv.py)이
``value``를 채우지 못하면 NULL로 둔다.

**개별 종목도 지원** (PLAN.md §6 Phase 3.7-2 실호출 검증, 2026-07-19):
``symbol`` 파라미터에 지수 이름 대신 종목코드(예: ``005930``)를 그대로 넣으면 그
종목의 일봉이 동일한 형식으로 돌아온다 — 인증/키 불필요. ``fetch_stock_series``가
이 경로를 쓴다(``fetch_index_series``와 파싱 로직을 공유, symbol 매핑만 다름).
"""

from __future__ import annotations

import datetime as dt
import re

import requests

FCHART_URL = "https://fchart.stock.naver.com/siseJson.naver"

SYMBOLS = {
    "kospi": "KOSPI",
    "kosdaq": "KOSDAQ",
    "k200_futures": "FUT",
    "kospi200": "KPI200",
}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_ROW_RE = re.compile(
    r'\["(?P<date>\d{8})",\s*'
    r"(?P<open>[\d.]+),\s*"
    r"(?P<high>[\d.]+),\s*"
    r"(?P<low>[\d.]+),\s*"
    r"(?P<close>[\d.]+),\s*"
    r"(?P<volume>\d+),"
)


class NaverIndexError(Exception):
    """Raised when the fchart response is empty or unparsable."""


def _fetch_series_by_symbol(
    symbol: str, start: dt.date, end: dt.date, timeout: int, error_label: str
) -> list[dict]:
    """symbol(지수 심볼 또는 종목코드) 그대로 fchart를 호출하는 공통 구현.

    Returns ``[{"date": dt.date, "open": float, "high": float, "low": float,
    "close": float, "volume": int}, ...]`` (오름차순).
    """
    resp = requests.get(
        FCHART_URL,
        params={
            "symbol": symbol,
            "requestType": 1,
            "startTime": start.strftime("%Y%m%d"),
            "endTime": end.strftime("%Y%m%d"),
            "timeframe": "day",
        },
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    resp.raise_for_status()

    out: list[dict] = []
    for m in _ROW_RE.finditer(resp.text):
        d = m.group("date")
        out.append(
            {
                "date": dt.date(int(d[:4]), int(d[4:6]), int(d[6:8])),
                "open": float(m.group("open")),
                "high": float(m.group("high")),
                "low": float(m.group("low")),
                "close": float(m.group("close")),
                "volume": int(m.group("volume")),
            }
        )

    if not out:
        raise NaverIndexError(
            f"no rows parsed for {error_label} ({symbol}); response head: {resp.text[:200]!r}"
        )

    out.sort(key=lambda r: r["date"])
    return out


def fetch_index_series(market: str, start: dt.date, end: dt.date, timeout: int = 15) -> list[dict]:
    """market(kospi/kosdaq/k200_futures/kospi200)의 [start, end] 일봉을 오름차순으로 반환.

    Returns ``[{"date": dt.date, "open": float, "high": float, "low": float,
    "close": float, "volume": int}, ...]``.
    """
    symbol = SYMBOLS.get(market)
    if symbol is None:
        raise ValueError(f"unknown market {market!r}, expected one of {sorted(SYMBOLS)}")

    return _fetch_series_by_symbol(symbol, start, end, timeout, error_label=market)


def fetch_stock_series(code: str, start: dt.date, end: dt.date, timeout: int = 15) -> list[dict]:
    """종목코드(예: "005930")의 [start, end] 일봉을 오름차순으로 반환 — 개별 종목판
    fetch_index_series (모듈 docstring "개별 종목도 지원" 참고). 인증/키 불필요.

    Returns ``[{"date": dt.date, "open": float, "high": float, "low": float,
    "close": float, "volume": int}, ...]``. 빈 응답(존재하지 않는 코드 등)은
    NaverIndexError.
    """
    return _fetch_series_by_symbol(code, start, end, timeout, error_label=code)
