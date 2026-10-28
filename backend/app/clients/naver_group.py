"""네이버 증권 업종별/테마별 시세 목록/상세 — m.stock.naver.com JSON API
(PLAN.md §4.6 3.6-3 트리맵).

## 소스 변경 이력 (2026-10-28) — 기존 HTML 소스 302로 전면 교체

기존 소스였던 ``finance.naver.com/sise/sise_group.naver``·``sise_group_detail.naver``가
이제 302로 ``stock.naver.com/market/stock/kr/industry[/{no}]``(Next.js 클라이언트
렌더 SPA)로 리다이렉트된다. ``requests``는 redirect를 따라가 SPA 셸 HTML을 조용히
받아 파싱이 전부 실패(``no rows parsed``)했다(naver_breadth.py와 같은 증상).

대체 소스: 그 SPA가 쓰는 모바일 JSON API(인증 불필요).

- 목록: ``https://m.stock.naver.com/api/stocks/{industry,theme}?page=N&pageSize=100``
  → ``{"groups": [{"no", "name", "changeRate": "9.43", ...}], "totalCount": N}``.
  **pageSize 상한 100**(초과 시 400) — 업종 79개는 1페이지, 테마 ~264개는 3페이지.
- 상세: ``https://m.stock.naver.com/api/stocks/{industry,theme}/{no}?page=N&pageSize=100``
  → ``{"stocks": [{"itemCode", "stockName", "fluctuationsRatio": "11.33",
  "accumulatedTradingValue": "4,338", ...}], "totalCount": N}``. 거래대금은 구 소스와
  같은 **백만원 단위**(가격×거래량 근사로 실측 검증). 시가총액(``marketValue``,
  억원 단위)은 여기서도 쓰지 않는다 — market_sum은 호출자가 None으로 둔다.
- 존재하지 않는 테마 no는 200 + 빈 ``stocks``로 온다(→ NaverGroupError).
"""

from __future__ import annotations

import requests

LIST_URL = "https://m.stock.naver.com/api/stocks/{group_type}"
DETAIL_URL = "https://m.stock.naver.com/api/stocks/{group_type}/{no}"

GROUP_TYPES = ("upjong", "theme")
# 내부 group_type -> API 경로 조각(네이버는 업종을 industry라 부른다).
_API_TYPE = {"upjong": "industry", "theme": "theme"}

PAGE_SIZE = 100  # API 상한
_MAX_PAGES = 20  # 무한 루프 방어(구성 종목 최대 수천 개 가정)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class NaverGroupError(Exception):
    """Raised when the group list/detail API yields zero parsable rows."""


def _check_type(group_type: str) -> None:
    if group_type not in GROUP_TYPES:
        raise ValueError(f"unknown group_type {group_type!r}, expected one of {GROUP_TYPES}")


def _get_json(url: str, page: int, timeout: int) -> dict:
    resp = requests.get(
        url,
        params={"page": page, "pageSize": PAGE_SIZE},
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError as e:
        raise NaverGroupError(
            f"non-JSON response from {url}: {e}; response head: {resp.text[:200]!r}"
        ) from e


def _fetch_all(url: str, key: str, timeout: int) -> list[dict]:
    """``key``('groups'/'stocks') 배열을 totalCount를 채울 때까지 페이징해 모은다."""
    items: list[dict] = []
    for page in range(1, _MAX_PAGES + 1):
        data = _get_json(url, page, timeout)
        chunk = data.get(key) or []
        items.extend(chunk)
        if not chunk or len(items) >= int(data.get("totalCount") or 0):
            break
    return items


def _num(raw) -> float | None:
    try:
        return float(str(raw).replace(",", ""))
    except ValueError:
        return None


def fetch_group_snapshot(group_type: str, timeout: int = 15) -> list[dict]:
    """group_type('upjong'/'theme')의 전체 그룹 목록을 반환한다.

    Returns ``[{"name": str, "change_rate": float, "no": int}, ...]`` — 소스 등장
    순서 그대로(등락률 내림차순). ``no``는 ``fetch_group_value``에 넘겨 그룹
    상세(거래대금 합산)를 조회하는 데 쓴다. market_sum은 이 클라이언트가 주지
    않으므로 호출자(collectors/group_snapshot.py)가 None으로 채운다.
    """
    _check_type(group_type)
    url = LIST_URL.format(group_type=_API_TYPE[group_type])
    rows = []
    for g in _fetch_all(url, "groups", timeout):
        rate = _num(g.get("changeRate"))
        if rate is None or g.get("no") is None or not g.get("name"):
            continue
        rows.append({"name": g["name"], "change_rate": rate, "no": int(g["no"])})
    if not rows:
        raise NaverGroupError(f"no rows parsed for group_type={group_type}")
    return rows


def _fetch_stocks(group_type: str, no: int, timeout: int) -> list[dict]:
    _check_type(group_type)
    url = DETAIL_URL.format(group_type=_API_TYPE[group_type], no=no)
    stocks = _fetch_all(url, "stocks", timeout)
    if not stocks:
        raise NaverGroupError(f"no constituent rows parsed for group_type={group_type} no={no}")
    return stocks


def fetch_group_value(group_type: str, no: int, timeout: int = 15) -> int:
    """그룹 구성 종목의 거래대금을 전부 합산해 반환한다(백만원 단위). market_sum
    (시가총액)은 이 함수가 주지 않는다(호출자가 None으로 둔다).

    일부 종목의 거래대금 파싱이 실패해도(거래정지 등) 그 종목만 0으로 치고
    계속 합산한다 — 구성 종목이 하나도 없을 때만 NaverGroupError.
    """
    total = 0
    for s in _fetch_stocks(group_type, no, timeout):
        v = _num(s.get("accumulatedTradingValue"))
        if v is not None:
            total += int(v)
    return total


def fetch_group_constituents(
    group_type: str, no: int, limit: int = 10, timeout: int = 15
) -> list[dict]:
    """그룹 구성 종목 중 거래대금 상위 ``limit``개를 반환한다(PLAN.md §5.12
    "업종·테마 트리맵 클릭 → 대장 종목 TOP10").

    Returns ``[{"code": str, "name": str, "change_rate": float, "value": int}, ...]``
    — 거래대금(백만원) 내림차순. 코드/이름/등락률/거래대금 중 하나라도 파싱에
    실패한 종목은 건너뛰고, 하나도 남지 않으면 NaverGroupError.
    """
    constituents = []
    for s in _fetch_stocks(group_type, no, timeout):
        rate = _num(s.get("fluctuationsRatio"))
        value = _num(s.get("accumulatedTradingValue"))
        if not s.get("itemCode") or not s.get("stockName") or rate is None or value is None:
            continue
        constituents.append(
            {
                "code": s["itemCode"],
                "name": s["stockName"],
                "change_rate": rate,
                "value": int(value),
            }
        )
    if not constituents:
        raise NaverGroupError(
            f"no constituent details parsed for group_type={group_type} no={no}"
        )

    constituents.sort(key=lambda c: c["value"], reverse=True)
    return constituents[:limit]
