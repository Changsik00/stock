"""코스피/코스닥 등락 종목수(breadth) — m.stock.naver.com 지수 통합 API 파싱
(PLAN.md §3.5/§4.6 3.6-2).

## 소스 변경 이력 (2026-09-14) — 기존 소스 404/302로 전면 교체

기존 채택 소스였던 ``finance.naver.com/sise/sise_index.naver?code=KOSPI``가 이제
302로 ``https://stock.naver.com/domestic/index/KOSPI/price``(Next.js 클라이언트
렌더 SPA)로 리다이렉트된다 — 서버 HTML에 더 이상 등락 종목수 `<li>` 블록이 없고
JS 번들만 온다(``requests``는 기본으로 redirect를 따라가므로 조용히 SPA 셸 HTML을
반환받아 파싱이 전부 실패, ``missing fields`` 에러로 관측됨. 2026-09-14 실측).

대체 소스 실확정: 그 SPA가 그리는 화면 자체가 쓰는 API인
``https://m.stock.naver.com/api/index/{KOSPI|KOSDAQ}/integration``(모바일 지수
상세, 인증/키 불필요, naver_rank.py의 stock/{code}/integration과 동일 계열)의
응답에 ``upDownStockInfo`` 객체로 다섯 값이 그대로 있다::

    {"upperCount": "0", "riseCount": "179", "steadyCount": "47",
     "fallCount": "686", "lowerCount": "0"}

실측값(2026-09-14 장중, KOSPI): 상한 0 / 상승 179 / 보합 47 / 하락 686 / 하한 0.
KOSDAQ도 동일 키로 확인됨. 이 API 역시 "지금" 값만 주고 날짜 쿼리를 지원하지
않는다(구 소스와 동일한 제약 — scripts/backfill_breadth.py 참고, 과거 소급 불가).

## 이전 소스 정보 (2026-07-18, 폐기됨)

과거에는 sise_index.naver의 서버렌더 HTML 안 접근성용 ``<span class="blind">``
라벨을 정규식으로 파싱했다. 위 리다이렉트로 더 이상 유효하지 않다.
"""

from __future__ import annotations

import requests

INDEX_URL = "https://m.stock.naver.com/api/index/{code}/integration"

MARKET_CODE = {"kospi": "KOSPI", "kosdaq": "KOSDAQ"}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# upDownStockInfo의 키 -> breadth dict 키.
_COUNT_FIELDS = {
    "upperCount": "limit_up",
    "riseCount": "adv",
    "steadyCount": "flat",
    "fallCount": "dec",
    "lowerCount": "limit_down",
}


class NaverBreadthError(Exception):
    """Raised when the integration API response is missing one or more count fields."""


def fetch_breadth(market: str, timeout: int = 15) -> dict:
    """market(kospi/kosdaq)의 현재(장중이면 실시간, 장마감 후면 그날 확정치) 등락
    종목수를 반환한다.

    Returns ``{"adv": int, "dec": int, "flat": int, "limit_up": int, "limit_down": int}``.
    다섯 필드 중 하나라도 파싱에 실패하면 NaverBreadthError를 던진다(부분 값으로
    조용히 반환하지 않음 — 등락 종목수는 합계 sanity check이 의미 있으려면 다섯
    필드가 모두 있어야 한다).
    """
    code = MARKET_CODE.get(market)
    if code is None:
        raise ValueError(f"unknown market {market!r}, expected one of {sorted(MARKET_CODE)}")

    resp = requests.get(
        INDEX_URL.format(code=code),
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    resp.raise_for_status()

    try:
        data = resp.json()
    except ValueError as e:
        raise NaverBreadthError(
            f"non-JSON response for market={market}: {e}; response head: {resp.text[:200]!r}"
        ) from e

    up_down = data.get("upDownStockInfo") or {}

    result: dict[str, int] = {}
    missing: list[str] = []
    for src_key, out_key in _COUNT_FIELDS.items():
        raw = up_down.get(src_key)
        if raw is None:
            missing.append(src_key)
            continue
        result[out_key] = int(str(raw).replace(",", ""))

    if missing:
        raise NaverBreadthError(
            f"missing fields {missing} for market={market}; response head: {resp.text[:200]!r}"
        )

    return result
