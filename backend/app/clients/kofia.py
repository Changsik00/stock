"""KOFIA freesis(금융투자협회 종합통계) POST 파싱 — 예탁금·신용융자·대차잔고 (PLAN.md §3.5).

freesis.kofia.or.kr는 eXBuilder6/Cleopatra RIA 프레임워크 기반 SPA라 데이터 요청이
런타임에 동적으로 생성되고, 화면에 표시되는 정적 HTML/JS만으로는 실제 그리드 데이터
엔드포인트를 찾을 수 없다. Playwright 헤드리스 브라우저로 각 통계 화면(FreeSIS.do?
serviceId=...)을 로드해 실제 XHR을 캡처해서 확인했다.

실제 데이터 엔드포인트: ``POST /meta/getMetaDataList.do``
(``/meta/getSrvData.do``는 컬럼 정의 등 메타데이터만 반환하는 별개 엔드포인트이며
행 데이터는 없다 — 이전 시도가 실패한 지점).

요청 바디: ``{"dmSearch": {"tmpV40": "1000000", "tmpV41": "1", "tmpV1": "D",
"tmpV45": "<시작일 YYYYMMDD>", "tmpV46": "<종료일 YYYYMMDD>", "OBJ_NM": "<serviceId>BO"}}``
- tmpV1 = "D"(일간) — 이 클라이언트는 일별 시계열만 사용한다.
- tmpV45/tmpV46 = 조회 기간 시작/종료일. 3년 범위를 한 번에 요청해도(865건 실측)
  페이징 없이 전부 반환된다.
- 대차거래추이(STATSCU0100000140BO)는 종목 필터 ``tmpV72``가 추가로 필요하다.
  빈 문자열로 두면 개별 종목이 아닌 "전체"(시장 전체 합계) 행을 반환한다.
- **세션/쿠키 불필요** — main.do를 먼저 방문하지 않고 콜드 POST를 던져도 200으로
  정상 응답한다 (httpx로 실측 확인).

응답 바디: ``{"unit": "", "ds1": [ {"TMPV1": "<YYYYMMDD>", "TMPV2": ..., ...}, ... ]}``
- 날짜가 아닌 요약 행("합계"/"평균")이 섞여 나올 수 있다(대차거래추이에서 실측) —
  ``TMPV1``이 8자리 숫자 문자열인 행만 취한다.
- 값 단위는 메타데이터(``/meta/getSrvData.do`` 응답의 ``BASIC_UNIT``)가
  ``T2050^06``(공통코드 T2050의 06 = "백만")이라 밝히고 있고, 실측 값 규모
  (예: 투자자예탁금 1억 근처)도 이와 일치한다 — **단위: 백만원**(대차거래추이의
  주수 컬럼만 예외로 단위 없는 원주(株)).

서비스 ID -> 시리즈 매핑(PLAN.md §5.2 macro_series.series):
- ``investor_deposit``: STATSCU0100000060(증시자금추이) TMPV2 —
  투자자예탁금(장내파생상품 거래예수금 제외), 백만원
- ``credit_loan_kospi``/``credit_loan_kosdaq``: STATSCU0100000070(신용공여 잔고 추이)
  TMPV3(신용거래융자·유가증권)/TMPV4(신용거래융자·코스닥), 백만원
- ``lending_balance``: STATSCU0100000140(대차거래추이, tmpV72="") TMPV6 —
  대차잔고 금액("전체" 시장 합계, 종목별/시장별 분리 옵션 없음), 백만원
"""

from __future__ import annotations

import datetime as dt
import logging
import re

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://freesis.kofia.or.kr"
DATA_ENDPOINT = f"{BASE_URL}/meta/getMetaDataList.do"

_DATE_RE = re.compile(r"^\d{8}$")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_HEADERS = {
    "Content-Type": "application/json;charset=UTF-8",
    "User-Agent": USER_AGENT,
    "Referer": f"{BASE_URL}/stat/FreeSIS.do",
}

# serviceId -> (OBJ_NM, extra dmSearch fields)
_SERVICE_IDS = {
    "investor_deposit": "STATSCU0100000060",
    "credit_loan": "STATSCU0100000070",
    "lending_balance": "STATSCU0100000140",
}


class KofiaError(Exception):
    """Raised when freesis returns a malformed/unexpected payload."""


def _parse_date(raw: str) -> dt.date | None:
    if not isinstance(raw, str) or not _DATE_RE.match(raw):
        return None
    return dt.datetime.strptime(raw, "%Y%m%d").date()


def _post(
    client: httpx.Client,
    obj_nm: str,
    start: dt.date,
    end: dt.date,
    extra: dict | None = None,
    timeout: int = 20,
) -> list[dict]:
    dm_search = {
        "tmpV40": "1000000",
        "tmpV41": "1",
        "tmpV1": "D",
        "tmpV45": start.strftime("%Y%m%d"),
        "tmpV46": end.strftime("%Y%m%d"),
        "OBJ_NM": obj_nm,
    }
    if extra:
        dm_search.update(extra)

    resp = client.post(DATA_ENDPOINT, json={"dmSearch": dm_search}, headers=_HEADERS, timeout=timeout)
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError as e:
        raise KofiaError(f"non-JSON response for {obj_nm}: {resp.text[:200]!r}") from e

    rows = data.get("ds1")
    if rows is None:
        raise KofiaError(f"unexpected KOFIA response shape for {obj_nm}: {list(data.keys())}")
    return rows


def fetch_investor_deposit(
    client: httpx.Client, start: dt.date, end: dt.date
) -> list[dict]:
    """증시자금추이(STATSCU0100000060) -> investor_deposit 일별 시계열.

    Returns ``[{"date": dt.date, "value": float}, ...]`` (단위: 백만원).
    """
    rows = _post(client, f"{_SERVICE_IDS['investor_deposit']}BO", start, end)
    out: list[dict] = []
    for row in rows:
        row_date = _parse_date(row.get("TMPV1"))
        if row_date is None:
            continue
        value = row.get("TMPV2")
        if value is None:
            continue
        out.append({"date": row_date, "value": float(value)})
    out.sort(key=lambda r: r["date"])
    return out


def fetch_credit_loan(
    client: httpx.Client, start: dt.date, end: dt.date
) -> dict[str, list[dict]]:
    """신용공여 잔고 추이(STATSCU0100000070) -> credit_loan_kospi/credit_loan_kosdaq.

    Returns ``{"credit_loan_kospi": [...], "credit_loan_kosdaq": [...]}``
    (단위: 백만원, 신용거래융자 기준 — 신용거래대주는 제외).
    """
    rows = _post(client, f"{_SERVICE_IDS['credit_loan']}BO", start, end)
    kospi: list[dict] = []
    kosdaq: list[dict] = []
    for row in rows:
        row_date = _parse_date(row.get("TMPV1"))
        if row_date is None:
            continue
        kospi_val = row.get("TMPV3")
        kosdaq_val = row.get("TMPV4")
        if kospi_val is not None:
            kospi.append({"date": row_date, "value": float(kospi_val)})
        if kosdaq_val is not None:
            kosdaq.append({"date": row_date, "value": float(kosdaq_val)})
    kospi.sort(key=lambda r: r["date"])
    kosdaq.sort(key=lambda r: r["date"])
    return {"credit_loan_kospi": kospi, "credit_loan_kosdaq": kosdaq}


def fetch_lending_balance(
    client: httpx.Client, start: dt.date, end: dt.date
) -> list[dict]:
    """대차거래추이(STATSCU0100000140, 종목필터 미지정="전체") -> lending_balance.

    Returns ``[{"date": dt.date, "value": float}, ...]`` (단위: 백만원, 잔고 금액).
    """
    rows = _post(
        client,
        f"{_SERVICE_IDS['lending_balance']}BO",
        start,
        end,
        extra={"tmpV72": ""},
    )
    out: list[dict] = []
    for row in rows:
        row_date = _parse_date(row.get("TMPV1"))
        if row_date is None:
            continue  # "합계"/"평균" 요약 행 제외
        value = row.get("TMPV6")
        if value is None:
            continue
        out.append({"date": row_date, "value": float(value)})
    out.sort(key=lambda r: r["date"])
    return out


def fetch_all(start: dt.date, end: dt.date, delay: float = 0.8) -> dict[str, list[dict]]:
    """세 통계를 순서대로 조회 (요청 간 delay초 대기, freesis 과도 요청 방지).

    Returns a dict keyed by macro_series series name:
    ``investor_deposit``, ``credit_loan_kospi``, ``credit_loan_kosdaq``, ``lending_balance``.
    """
    import time

    with httpx.Client() as client:
        result: dict[str, list[dict]] = {}
        result["investor_deposit"] = fetch_investor_deposit(client, start, end)
        time.sleep(delay)
        result.update(fetch_credit_loan(client, start, end))
        time.sleep(delay)
        result["lending_balance"] = fetch_lending_balance(client, start, end)
        return result
