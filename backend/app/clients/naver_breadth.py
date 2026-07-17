"""코스피/코스닥 등락 종목수(breadth) — finance.naver.com/sise/sise_index.naver 파싱
(PLAN.md §3.5/§4.6 3.6-2).

## 소스 실확정 경과 (2026-07-18)

후보 3개를 실호출로 비교했다:

1. **``finance.naver.com/sise/sise_index.naver?code=KOSPI``(PC 시장 페이지) — 채택.**
   서버렌더 HTML(EUC-KR, ``requests``가 Content-Type 헤더로 자동 디코딩 — naver_rank.py와
   동일 패턴, 수동 인코딩 불필요) 안에 접근성용 ``<span class="blind">`` 라벨이 그대로
   박혀 있어 정규식 하나로 5개 값을 전부 뽑는다::

       <li class="lst"><span class="blind">상한종목수</span><a href="/sise/sise_upper.naver"><span>6</span></a></li>
       <li class="lst2"><span class="blind">상승종목수</span><a href="/sise/sise_rise.naver"><span>384</span></a></li>
       <li class="lst3"><span class="blind">보합종목수</span><a href="/sise/sise_steady.naver"><span>40</span></a></li>
       <li class="lst4"><span class="blind">하락종목수</span><a href="/sise/sise_fall.naver"><span>488</span></a></li>
       <li class="lst5"><span class="blind">하한종목수</span><a href="/sise/sise_lower.naver"><span>0</span></a></li>

   실측값(2026-07-18 장중, code=KOSPI): 상한 6 / 상승 384 / 보합 40 / 하락 488 / 하한 0
   → 합계 918 (코스피 상장종목수 범위와 합치). code=KOSDAQ: 상한 11 / 상승 501 /
   보합 56 / 하락 1182 / 하한 1 → 합계 1751. 이 페이지는 장중에는 실시간 갱신되는
   현재가 페이지이고, 장마감 후에는 그 날의 확정 등락 수를 그대로 보여주므로
   **일별 확정치 수집과 장중 온디맨드 조회 양쪽에 동일 소스로 쓸 수 있다.**
   - **과거 날짜 조회 불가 확정**: ``date=YYYYMMDD`` 쿼리를 붙여봐도 무시되고
     항상 "지금" 값만 온다(sise_deal_rank_iframe.naver와 같은 제약, naver_rank.py
     참고) — 이 소스로는 backfill이 불가능하다(오늘 것만 매일 쌓아야 함).

2. ``m.stock.naver.com/api/index/KOSPI/...`` 계열 — 지수 상세 화면의 모바일 API는
   현재가/등락률/거래대금 위주고 등락 종목수 필드를 주는 엔드포인트를 찾지 못했다
   (etfAnalysis류처럼 종목별 API는 있어도 "시장 전체 등락수"에 대응하는 모바일 API가
   확인되지 않음). 미채택.

3. ``polling.finance.naver.com`` 실시간 폴링 API — 종목/지수 시세 스트리밍용으로,
   낱개 지수의 현재가·등락률만 주고 시장 전체 종목수 집계는 제공하지 않는다. 미채택.

결론: 후보 1(PC sise_index.naver)만으로 목적을 완전히 달성해 최후 폴백(전 종목 리스트
페이징 카운트)은 구현하지 않았다.
"""

from __future__ import annotations

import re

import requests

INDEX_URL = "https://finance.naver.com/sise/sise_index.naver"

MARKET_CODE = {"kospi": "KOSPI", "kosdaq": "KOSDAQ"}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 라벨 -> breadth dict 키. 소스 HTML의 다섯 <li> 순서(상한/상승/보합/하락/하한)와
# 무관하게 라벨 텍스트로 매칭한다(순서가 바뀌어도 안전).
_FIELD_LABELS = {
    "상한종목수": "limit_up",
    "상승종목수": "adv",
    "보합종목수": "flat",
    "하락종목수": "dec",
    "하한종목수": "limit_down",
}

_ROW_RE = {
    label: re.compile(re.escape(label) + r"</span><a[^>]*><span>([\d,]+)</span>")
    for label in _FIELD_LABELS
}


class NaverBreadthError(Exception):
    """Raised when the sise_index.naver response is missing one or more count fields."""


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
        INDEX_URL,
        params={"code": code},
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    resp.raise_for_status()
    text = resp.text

    result: dict[str, int] = {}
    missing: list[str] = []
    for label, key in _FIELD_LABELS.items():
        m = _ROW_RE[label].search(text)
        if m is None:
            missing.append(label)
            continue
        result[key] = int(m.group(1).replace(",", ""))

    if missing:
        raise NaverBreadthError(
            f"missing fields {missing} for market={market}; response head: {text[:200]!r}"
        )

    return result
