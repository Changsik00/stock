"""USD/KRW 환율 — m.stock.naver.com 무키 API, 실패 시 FRED CSV로 자동 폴백
(PLAN.md §3 — 환율).

## 소스 실확정 경과 (2026-07-18)

``GET https://m.stock.naver.com/front-api/marketIndex/prices``
(``category=exchange&reutersCode=FX_USDKRW&page=N&pageSize=M``)를 실호출로 확인했다:

- User-Agent 헤더가 없으면 차단 가능성이 있어(다른 네이버 클라이언트와 동일 관례)
  항상 지정한다 — ``naver_etf.py``/``naver_breadth.py``와 동일한 UA 문자열.
- 응답은 ``{"isSuccess": bool, "detailCode": str, "message": str, "result": [...]}``
  — ``result``가 곧 행 배열이다(추가 중첩 없음). 각 행의 관심 필드는
  ``localTradedAt``("YYYY-MM-DD" 문자열)과 ``closePrice``(콤마 포함 문자열,
  예: ``"1,490.00"``).
- ``pageSize``는 **최대 60**이다 — 60 초과 시 200 응답이되 ``result``가 리스트가
  아니라 에러 메시지 문자열로 온다(``"getExchangeClosingPrices.pageSize: must be
  less than or equal to 60"``). 10으로도 동작하지만 호출 수를 줄이려면 60을 쓴다.
- 페이지는 최신 순으로 **연속적**이다: page=1이 가장 최근 N건, page=2는 그 바로
  다음(더 과거) N건 — 겹치거나 비지 않는다(실측: page=1 pageSize=60의 마지막
  날짜가 2026-04-20이면 page=2의 첫 날짜는 2026-04-17, 즉 그 다음 거래일).
  주말/휴장일은 애초에 배열에 없다(거래일만 옴).
- 조회 가능한 과거 범위를 넘어서는 page 번호를 요청하면 에러 없이
  ``{"result": []}``(빈 리스트)가 온다 — 이것이 페이징 종료 조건이다.

## 폴백

Naver 쪽이 네트워크 오류/파싱 실패/요청 범위 전체에 대해 빈 결과를 반환하면
FRED의 무료·무인증 CSV 엔드포인트(``DEXKOUS`` — 미 연준이 산출하는 원/달러
동일 시계열)로 자동 폴백한다. CSV 파싱은 ``commodities._fetch_fred``를 그대로
재사용한다(중복 구현하지 않음 — ``commodities.py``의 ``fetch_oil_series``와
동일한 try/except 폴백 패턴).
"""

from __future__ import annotations

import datetime as dt
import logging

import requests

from . import commodities

logger = logging.getLogger(__name__)

PRICES_URL = "https://m.stock.naver.com/front-api/marketIndex/prices"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

REUTERS_CODE = "FX_USDKRW"
PAGE_SIZE = 60
MAX_PAGES = 500

FRED_SERIES_ID = "DEXKOUS"


class NaverFxError(Exception):
    """Raised when the naver marketIndex/prices response is unparsable/unexpected."""


def fetch_usdkrw_naver(start: dt.date, end: dt.date, timeout: int = 15) -> list[dict]:
    """m.stock.naver.com에서 USD/KRW 종가를 [start, end] 구간(양끝 포함)만큼
    페이징해서 가져온다.

    Returns rows sorted ascending: ``[{"date": dt.date, "value": float}, ...]``.
    페이지는 최신 -> 과거 순으로 순회하며, 현재 페이지의 가장 오래된 행이
    ``start``보다 과거이면(그 페이지까지는 포함해서) 순회를 멈춘다. 빈
    ``result``를 받아도(조회 가능 범위를 넘어섰다는 뜻) 멈춘다. 예상치 못한
    응답 형태(``result``가 리스트가 아님 등)를 만나면 NaverFxError를 던진다.
    """
    rows_by_date: dict[dt.date, float] = {}
    page = 1
    hit_page_cap = False

    while True:
        if page > MAX_PAGES:
            hit_page_cap = True
            logger.warning(
                "naver_fx: MAX_PAGES(%d) 도달 — 순회를 강제 종료합니다(start=%s, end=%s)",
                MAX_PAGES,
                start,
                end,
            )
            break

        resp = requests.get(
            PRICES_URL,
            params={
                "category": "exchange",
                "reutersCode": REUTERS_CODE,
                "page": page,
                "pageSize": PAGE_SIZE,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        result = data.get("result")
        if result == []:
            # 조회 가능 범위를 넘어선 page 번호 — 정상적인 페이징 종료 조건.
            break
        if not isinstance(result, list):
            raise NaverFxError(
                f"unexpected result shape at page={page}: {data!r}"[:300]
            )

        page_dates: list[dt.date] = []
        for row in result:
            raw_date = row.get("localTradedAt")
            raw_close = row.get("closePrice")
            if not raw_date or raw_close in (None, ""):
                continue
            try:
                row_date = dt.date.fromisoformat(raw_date)
                value = float(str(raw_close).replace(",", ""))
            except ValueError:
                continue
            page_dates.append(row_date)
            if start <= row_date <= end:
                rows_by_date[row_date] = value

        if not page_dates:
            # 파싱 가능한 행이 하나도 없는 비정상 페이지 — 무한 루프 방지 위해 종료.
            break

        oldest_in_page = min(page_dates)
        if oldest_in_page < start:
            break

        page += 1

    if not rows_by_date and not hit_page_cap:
        raise NaverFxError(f"no usable rows found for [{start}, {end}]")

    return [{"date": d, "value": v} for d, v in sorted(rows_by_date.items())]


def fetch_usdkrw(start: dt.date, end: dt.date) -> list[dict]:
    """USD/KRW 일별 시계열 — naver 우선, 실패 시 FRED(DEXKOUS)로 자동 폴백.

    Returns rows sorted ascending: ``[{"date", "value", "source"}, ...]``.
    """
    try:
        rows = fetch_usdkrw_naver(start, end)
        for row in rows:
            row["source"] = "naver"
        rows.sort(key=lambda r: r["date"])
        return rows
    except Exception as e:  # naver는 비공식 API라 응답 형태가 언제든 바뀔 수 있음
        logger.warning("naver 환율 조회 실패(%s) — FRED CSV로 폴백합니다", e)

    rows = commodities._fetch_fred(FRED_SERIES_ID, start, end)
    for row in rows:
        row["source"] = "fred"
    rows.sort(key=lambda r: r["date"])
    return rows
