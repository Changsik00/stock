"""네이버 증권 업종별/테마별 시세 목록/상세 — sise_group.naver·sise_group_detail.naver
파싱 (PLAN.md §4.6 3.6-3 트리맵).

소스: ``https://finance.naver.com/sise/sise_group.naver?type={upjong,theme}``
(목록) + ``https://finance.naver.com/sise/sise_group_detail.naver?type=..&no=..``
(그룹 상세, 구성 종목별 거래대금 합산용).

실호출 확인(2026-07-18, requests로 충분히 검증됨, Playwright 불필요):

목록 페이지(``sise_group.naver``):

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
  없다** — 그룹 레벨 거래대금은 상세 페이지 구성 종목을 합산해서 얻는다(아래).
- 등락률은 ``<span class="tah p11 red01">+8.27%</span>``(상승, 빨강) /
  ``<span class="tah p11 nv01">-5.79%</span>``(하락, 남색) 형태로 부호가 텍스트에
  이미 포함돼 있어 CSS 클래스(red01/nv01)를 볼 필요 없이 텍스트만 파싱하면 된다.
  0.00%(``nil01`` 클래스로 추정, 실측 표본에는 없었음)도 같은 정규식으로 잡힌다
  (``[+-]?`` — 부호 없는 ``0.00%``도 허용).
- 그룹 상세 링크(``sise_group_detail.naver?type=upjong&no=332``)의 ``no``는
  ``fetch_group_value``가 상세 페이지를 조회할 때 필요해 ``fetch_group_snapshot``
  반환값에 포함한다(이전 버전은 group_snapshot PK에 no가 필요 없어 버렸지만, 이제
  값 조회에 no 자체가 입력이라 반드시 있어야 한다).

상세 페이지(``sise_group_detail.naver``, 구성 종목 리스트):

- 컬럼은 **종목명, (테마 타입일 때만) 테마 편입 사유, 현재가, 전일비, 등락률,
  매수호가, 매도호가, 거래량, 거래대금, 전일거래량, 토론** 순 — "테마 편입 사유"
  칸은 ``class="number"``가 아니라서 거래대금 등 숫자 컬럼 파싱 인덱스에는 영향
  없다(업종/테마 두 타입 모두 숫자 컬럼 순서 동일: 현재가=0, 전일비=1, 등락률=2,
  매수호가=3, 매도호가=4, 거래량=5, **거래대금=6**, 전일거래량=7).
- **거래대금 컬럼은 이미 백만원 단위**다 — 실측(2026-07-18) 검증: 하이딥
  (가격 1,068원 × 거래량 124,044주 ≈ 132.5백만원 근사치)의 거래대금 컬럼값이
  ``122``로, 가격×거래량 근사(장중 체결가가 종가와 달라 정확히 일치하진 않음)와
  자릿수가 일치 — FlowRankTable 등 기존 관례(값 컬럼은 백만원)와 같다.
- **시가총액 컬럼은 상세 페이지에도 없다** — market_sum은 여전히 이 클라이언트가
  채우지 못해 호출자가 None으로 둔다.
- **페이징 없음**(실측 최대 171개 구성 종목까지 확인, ``<div class="paging">``
  마크업 없음 — 목록 페이지와 동일하게 그룹당 종목 수와 무관하게 한 페이지).
- 거래정지 등으로 특정 종목의 숫자 컬럼이 비어 있거나 파싱 실패하면 그 종목의
  기여분을 0으로 두고 합산을 계속한다(그룹 전체를 실패시키지 않는다) — 구성
  종목이 하나도 파싱되지 않을 때만 ``NaverGroupError``를 던진다(빈 그룹/오류
  페이지로 간주).
"""

from __future__ import annotations

import re

import requests

LIST_URL = "https://finance.naver.com/sise/sise_group.naver"
DETAIL_URL = "https://finance.naver.com/sise/sise_group_detail.naver"

GROUP_TYPES = ("upjong", "theme")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 그룹명 + 등락률 + 상세 페이지 no(그룹 상세 조회용)를 뽑는다 — 거래대금/시총
# 컬럼은 이 목록 페이지 소스에 없음(모듈 docstring 참고).
_ROW_RE = re.compile(
    r'<a href="/sise/sise_group_detail\.naver\?type=(?:upjong|theme)&no=(?P<no>\d+)">'
    r"(?P<name>[^<]*)</a></td>\s*"
    r'<td class="number">\s*<span class="tah p11 \w+">\s*'
    r"(?P<rate>[+-]?[\d.]+)%\s*</span>\s*</td>",
    re.DOTALL,
)

# 상세 페이지 구성 종목 한 행 전체 — onMouseOver 속성이 데이터 행에만 붙어 있어
# 구분자로 쓴다(합계/구분선 등 다른 <tr>은 이 속성이 없어 자동으로 제외됨).
_DETAIL_ROW_RE = re.compile(r'<tr onMouseOver="mouseOver\(this\)".*?</tr>', re.DOTALL)
# 한 행 안의 숫자 컬럼(class="number") 전부를 순서대로 뽑는다 — 인덱스는 모듈
# docstring의 상세 페이지 컬럼 순서 참고.
_NUMBER_TD_RE = re.compile(r'<td class="number"[^>]*>(.*?)</td>', re.DOTALL)
_NUMERIC_TOKEN_RE = re.compile(r"[\d,]+\.?\d*")
_VALUE_COLUMN_INDEX = 6  # 현재가,전일비,등락률,매수호가,매도호가,거래량,거래대금(6),전일거래량


class NaverGroupError(Exception):
    """Raised when the sise_group list/detail page yields zero parsable rows."""


def fetch_group_snapshot(group_type: str, timeout: int = 15) -> list[dict]:
    """group_type('upjong'/'theme')의 전체 그룹 목록을 한 번에 반환한다(페이징 없음,
    모듈 docstring 참고).

    Returns ``[{"name": str, "change_rate": float, "no": int}, ...]`` — 소스 등장
    순서 그대로(등락률 내림차순, 상승 그룹 먼저). ``no``는 ``fetch_group_value``에
    넘겨 그룹 상세(거래대금 합산)를 조회하는 데 쓴다. market_sum은 이 클라이언트가
    주지 않으므로 호출자(collectors/group_snapshot.py)가 None으로 채운다.
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
        {"name": m.group("name"), "change_rate": float(m.group("rate")), "no": int(m.group("no"))}
        for m in _ROW_RE.finditer(text)
    ]
    if not rows:
        raise NaverGroupError(
            f"no rows parsed for group_type={group_type}; response head: {text[:200]!r}"
        )
    return rows


def fetch_group_value(group_type: str, no: int, timeout: int = 15) -> int:
    """그룹 상세 페이지(``sise_group_detail.naver``)의 구성 종목 거래대금을 전부
    합산해 반환한다(백만원 단위). market_sum(시가총액)은 상세 페이지에도 컬럼이
    없어 이 함수가 주지 않는다(호출자가 여전히 None으로 둔다).

    구성 종목 중 일부의 거래대금 파싱이 실패해도(거래정지 등) 그 종목만 0으로
    치고 나머지는 계속 합산한다 — 구성 종목이 하나도 파싱되지 않을 때만
    NaverGroupError를 던진다(모듈 docstring 참고).
    """
    if group_type not in GROUP_TYPES:
        raise ValueError(f"unknown group_type {group_type!r}, expected one of {GROUP_TYPES}")

    resp = requests.get(
        DETAIL_URL,
        params={"type": group_type, "no": no},
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    resp.raise_for_status()
    text = resp.text

    detail_rows = _DETAIL_ROW_RE.findall(text)
    if not detail_rows:
        raise NaverGroupError(
            f"no constituent rows parsed for group_type={group_type} no={no}; "
            f"response head: {text[:200]!r}"
        )

    total = 0
    for row in detail_rows:
        number_cells = _NUMBER_TD_RE.findall(row)
        if len(number_cells) <= _VALUE_COLUMN_INDEX:
            continue
        tokens = _NUMERIC_TOKEN_RE.findall(number_cells[_VALUE_COLUMN_INDEX])
        if not tokens:
            continue
        total += int(tokens[-1].replace(",", ""))
    return total
