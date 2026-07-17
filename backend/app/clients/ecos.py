"""Bank of Korea ECOS StatisticSearch client (PLAN.md §3 — 환율).

USD/KRW 매매기준율: 통계표 `731Y001`, 주기 `D`(일), 항목 `0000001`.

NOTE: 발급받은 `ECOS_API_KEY`가 없으면 ECOS의 'sample' 키를 사용한다. sample 키는
실호출로 확인한 결과 **한 번의 호출에 최대 10건**까지만 반환한다 (그 이상을
요청하면 `ERROR-301 조회건수 값의 타입이 유효하지 않습니다`로 거부됨). 이 클라이언트는
sample 키 사용 시 요청 건수를 자동으로 10건으로 clamp해서 에러 없이 동작하게 한다.
.env에 ECOS_API_KEY를 설정하면 이 제한이 사라지고 기간 전체를 한 번에 조회할 수 있다.
"""

from __future__ import annotations

import datetime as dt
import logging

import requests

from ..config import get_settings

logger = logging.getLogger(__name__)

BASE_URL = "https://ecos.bok.or.kr/api/StatisticSearch"

# 환율(USD/KRW 매매기준율)
USDKRW_STAT_CODE = "731Y001"
USDKRW_CYCLE = "D"
USDKRW_ITEM_CODE = "0000001"

SAMPLE_KEY = "sample"
SAMPLE_KEY_MAX_ROWS = 10


class ECOSError(Exception):
    """Raised when ECOS returns a non-INFO error payload or a malformed response."""


def _api_key() -> str:
    key = get_settings().ecos_api_key
    return key if key else SAMPLE_KEY


def fetch_series(
    stat_code: str,
    cycle: str,
    item_code: str,
    start: dt.date,
    end: dt.date,
    max_rows: int = 10_000,
    timeout: int = 15,
) -> list[dict]:
    """Fetch one ECOS statistic series between start/end (inclusive), single call.

    Returns rows sorted ascending by date: ``[{"date": dt.date, "value": float}, ...]``.
    An empty result (ECOS `INFO-*` code, e.g. no data in range) returns ``[]``.
    """
    key = _api_key()
    end_idx = max_rows
    if key == SAMPLE_KEY:
        end_idx = min(end_idx, SAMPLE_KEY_MAX_ROWS)

    date_fmt = "%Y%m%d" if cycle == "D" else "%Y%m"
    url = "/".join(
        [
            BASE_URL,
            key,
            "json",
            "kr",
            "1",
            str(end_idx),
            stat_code,
            cycle,
            start.strftime(date_fmt),
            end.strftime(date_fmt),
            item_code,
        ]
    )

    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    if "RESULT" in data:
        result = data["RESULT"]
        code = str(result.get("CODE", ""))
        if code.startswith("INFO"):
            return []
        raise ECOSError(f"{code}: {result.get('MESSAGE', data)}")

    search = data.get("StatisticSearch")
    if search is None:
        raise ECOSError(f"unexpected ECOS response shape: {data}")

    rows = search.get("row", [])
    out: list[dict] = []
    for row in rows:
        raw_date = row.get("TIME")
        value_str = row.get("DATA_VALUE")
        if not raw_date or value_str in (None, ""):
            continue
        try:
            value = float(value_str)
        except ValueError:
            continue
        row_date = dt.datetime.strptime(raw_date, date_fmt).date()
        out.append({"date": row_date, "value": value})

    out.sort(key=lambda r: r["date"])

    if key == SAMPLE_KEY and len(out) >= SAMPLE_KEY_MAX_ROWS:
        logger.warning(
            "ECOS sample 키 사용 중 — 최대 %d건만 반환됨(%s). "
            ".env의 ECOS_API_KEY를 설정하면 전체 기간을 조회할 수 있습니다.",
            SAMPLE_KEY_MAX_ROWS,
            stat_code,
        )
    return out


def fetch_usdkrw(start: dt.date, end: dt.date) -> list[dict]:
    """USD/KRW 매매기준율 일별 시계열 (기간 조회 한 번)."""
    return fetch_series(USDKRW_STAT_CODE, USDKRW_CYCLE, USDKRW_ITEM_CODE, start, end)
