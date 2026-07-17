"""네이버 증권 업종별/테마별 시세 목록 — sise_group.naver 파싱 (PLAN.md §4.6 3.6-3 트리맵).

소스: ``https://finance.naver.com/sise/sise_group.naver?type={upjong,theme}``

실호출 확인(2026-07-18, requests로 충분히 검증됨, Playwright 불필요):

- 서버렌더 HTML, 헤더가 ``Content-Type: text/html; charset=euc-kr``를 명시해
  ``requests``가 자동으로 인코딩을 잡는다(``resp.text``만으로 정상 문자열 —
  naver_rank.py의 sise_deal_rank_iframe과 동일 패턴, naver_etf.py의
  etfItemList.nhn처럼 수동 디코딩이 필요 없다).
- **type=upjong(업종) 79개, type=theme(테마) 266개 전부 한 페이지**에 나온다 —
  페이징 없음(``<div class="paging">`` 류 마크업 자체가 이 페이지에 없음, 실측
  확인). PLAN.md가 "266개가 여러 페이지면 페이징 처리"를 지시했지만 실제로는
  불필요했다.
- 각 그룹 행에 있는 컬럼은 **그룹명 + 등락률(%) + 등락 종목수(전체/상승/보합/하락)
  + 등락그래프(막대 %)** 뿐이다. **거래대금·시가총액 컬럼은 이 목록 페이지에
  없다** — GroupSnapshot.value/market_sum은 이 클라이언트가 채우지 못하고
  호출자가 None으로 둔다(모델이 애초에 NULL 허용으로 설계된 이유).
  sise_group_detail.naver(그룹 상세, 구성종목 리스트)에는 종목별 거래량/거래대금
  컬럼이 있지만 **그룹 합계(전체 종목 합산)는 상세 페이지에도 없다** — 그룹
  레벨 거래대금을 얻으려면 구성종목 전부를 합산해야 하는데, 이는 이번 작업
  범위(그룹 레벨만) 밖이라 하지 않는다(PLAN.md 작업 지시 "이번엔 그룹 레벨만,
  상세는 추후" 참고).
- 등락률은 ``<span class="tah p11 red01">+8.27%</span>``(상승, 빨강) /
  ``<span class="tah p11 nv01">-5.79%</span>``(하락, 남색) 형태로 부호가 텍스트에
  이미 포함돼 있어 CSS 클래스(red01/nv01)를 볼 필요 없이 텍스트만 파싱하면 된다.
  0.00%(``nil01`` 클래스로 추정, 실측 표본에는 없었음)도 같은 정규식으로 잡힌다
  (``[+-]?`` — 부호 없는 ``0.00%``도 허용).
- 그룹 상세 링크(``sise_group_detail.naver?type=upjong&no=332``)의 ``no``는 이
  클라이언트가 굳이 저장하지 않는다(group_snapshot PK가 (date, group_type, name)
  이라 no가 필요 없음) — 파싱 편의상 정규식에만 남겨둔다.
"""

from __future__ import annotations

import re

import requests

LIST_URL = "https://finance.naver.com/sise/sise_group.naver"

GROUP_TYPES = ("upjong", "theme")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 그룹명 + 등락률만 뽑는다 — 거래대금/시총 컬럼은 소스에 없음(모듈 docstring 참고).
_ROW_RE = re.compile(
    r'<a href="/sise/sise_group_detail\.naver\?type=(?:upjong|theme)&no=\d+">'
    r"(?P<name>[^<]*)</a></td>\s*"
    r'<td class="number">\s*<span class="tah p11 \w+">\s*'
    r"(?P<rate>[+-]?[\d.]+)%\s*</span>\s*</td>",
    re.DOTALL,
)


class NaverGroupError(Exception):
    """Raised when the sise_group list page yields zero parsable rows."""


def fetch_group_snapshot(group_type: str, timeout: int = 15) -> list[dict]:
    """group_type('upjong'/'theme')의 전체 그룹 목록을 한 번에 반환한다(페이징 없음,
    모듈 docstring 참고).

    Returns ``[{"name": str, "change_rate": float}, ...]`` — 소스 등장 순서 그대로
    (등락률 내림차순, 상승 그룹 먼저). value/market_sum은 이 클라이언트가 주지
    않으므로 호출자(collectors/group_snapshot.py)가 None으로 채운다.
    """
    if group_type not in GROUP_TYPES:
        raise ValueError(f"unknown group_type {group_type!r}, expected one of {GROUP_TYPES}")

    resp = requests.get(
        LIST_URL,
        params={"type": group_type},
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    resp.raise_for_status()
    text = resp.text

    rows = [
        {"name": m.group("name"), "change_rate": float(m.group("rate"))}
        for m in _ROW_RE.finditer(text)
    ]
    if not rows:
        raise NaverGroupError(
            f"no rows parsed for group_type={group_type}; response head: {text[:200]!r}"
        )
    return rows
