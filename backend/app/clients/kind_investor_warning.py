"""투자주의/투자경고/투자위험종목 지정 — KRX 공시채널 KIND(kind.krx.co.kr,
PLAN.md §5.39).

## 조사 배경

§5.36(서킷브레이커/사이드카)이 이미 커버하는 "지수" 단위 위험 경보와 달리,
KRX는 "종목" 단위로도 3단계(투자주의->투자경고->투자위험) 공식 경보 제도를
운영한다. 구현 전 이 프로젝트 house rule(§5.30/§5.32와 동일)대로 실호출로
소스부터 확정한다.

## 실측 경과 (2026-07-30)

### 1. 키움 REST API — 조사 결과: 해당 TR 없음

GitHub 참고 저장소(``younghwan91/kiwoom-rest-api``, §5.30/§5.32와 동일하게
이번에도 전체 클론해 조사)의 ``domestic/`` 전체(stock_info/market/ranking/
sector/etf/elw/theme/slb/credit_order/foreign_institution/short_selling/
condition_search, 16개 파일)를 "관리종목/투자유의/투자경고/투자위험/단기과열/
정리매매" 키워드로 grep한 결과 매칭 0건 — 가장 근접한 것은
``stock_info.py``의 ``ka10054``(변동성완화장치발동종목요청, VI)뿐이고 이는
완전히 다른 제도(개별 호가 급변 시 2분 단일가 전환)다. 이미 이 프로젝트가
호출 중인 ``ka10001``(종목기본정보, PLAN.md §5.39 조사 중 실호출 재확인)의
응답 필드(46개 전부 나열해 확인)에도 시가총액/PER/신용비율 등 재무 정보만
있고 관리종목·경고·과열 여부를 나타내는 상태 플래그가 전혀 없다. **결론:
키움 쪽엔 이 데이터 소스가 없다**(§5.30이 "키움에 선물 도메인 자체가 없다"고
결론 내린 것과 같은 성격의 확정적 부재).

### 2. data.krx.co.kr — 조사 결과: 로그인 필요(§7 기존 발견과 동일한 장벽)

메인 메뉴 트리(``data.krx.co.kr/contents/MDC/MAIN/main/index.cmd``)에
"단기과열종목 현황"(menuId=MDC02021001)/"단기과열종목 지정 내역(개별종목)"
(MDC02021002) 메뉴가 존재하지만, 실제로 그 URL
(``/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC02021001``)을 GET하면
바로 로그인 페이지로 리다이렉트된다(``alert('로그인 또는 회원가입이
필요합니다.')``, 실측 확인) — §7이 이미 기록한 일반 통계 포털 로그인 장벽과
동일하다. §5.32(공매도)가 찾은 것 같은 "화면 전용 무인증 서브포털"
(``/comm/srt/srtLoader/...`` 같은 별도 경로)이 이 메뉴들에는 없었다(메뉴
트리 전체를 다시 grep해도 "투자주의/경고/위험", "단기과열" 관련 별도
``srtLoader`` 류 경로 없음). **결론: data.krx.co.kr 쪽은 이번에도 막힘.**

### 3. KRX KIND(kind.krx.co.kr) — 채택. 투자주의/경고/위험 3단계 전용 화면 발견

data.krx.co.kr이 막혀 있어 KRX 생태계의 다른 공식 사이트를 추가로 뒤진 결과,
**한국거래소가 직접 운영하는 기업공시채널 KIND(kind.krx.co.kr)에 이 3단계
제도 전용 조회 화면이 무인증으로 존재**한다:

    GET https://kind.krx.co.kr/investwarn/investattentwarnrisky.do?method=investattentwarnriskyMain

페이지 안의 검색폼(``id="searchForm"``)이 jQuery ``ajaxSubmit``으로 같은
URL에 POST해 결과 HTML 조각을 받아온다(``fnSearch()`` 함수, 실측 페이지
소스에서 그대로 확인) — 로그인/세션/쿠키 전혀 불필요, **콜드 POST 하나로
200 + 실데이터**(§5.32 KRX 공매도 서브포털과 같은 성격의 발견, 다만 이번엔
data.krx.co.kr이 아니라 KIND라는 별개 KRX 공식 사이트).

파라미터(폼 hidden input 그대로)::

    POST https://kind.krx.co.kr/investwarn/investattentwarnrisky.do
    method=investattentwarnriskySub
    forward=invstcautnisu_sub(주의) | invstwarnisu_sub(경고) | invstriskisu_sub(위험)
    menuIndex=1(주의) | 2(경고) | 3(위험)
    marketType=""(전체) | "1"(유가증권) | "2"(코스닥) | "6"(코넥스)
    searchCorpName=""(비우면 전체 종목 대상 — 이 클라이언트가 쓰는 방식)
    startDate=YYYY-MM-DD, endDate=YYYY-MM-DD (조회기간, 최대 3년 제한 실측 확인
    — JS ``fnSearch()``의 "조회기간은 3년 이내만 가능합니다" 검증과 일치)
    currentPageSize=15|30|50|100, pageIndex=1..N
    orderMode/orderStat(정렬, 생략 시 기본 정렬 — 이 클라이언트는 지정하지 않음)

**실측 확인 — 회사명 검색(``searchCorpName``)은 이 클라이언트에서 안 쓴다**:
폼은 회사명 자동완성(``AKC`` iframe)이 서버에 별도로 ``repIsuSrtCd``(내부
법인코드)를 채워 넣은 뒤에야 검색이 되는 구조라, ``searchCorpName``만 채우고
``repIsuSrtCd``를 비운 채 직접 POST하면 빈 응답(0바이트)이 온다(실측
확인) — 그 자동완성 팝업 흐름 전체를 재현하는 건 불필요하게 취약해진다.
대신 **``searchCorpName``을 비워 "전체 종목" 검색으로 항상 호출**하고,
받은 목록에서 이 프로젝트의 ``stocks.name``과 문자열이 정확히 일치하는
종목만 코드로 매핑한다(``collectors/investor_warning.py`` 참고) — 이미
전체 목록을 한 번에 받아오므로 종목별로 여러 번 호출할 필요도 없다.

**tier별로 응답 테이블 스키마가 다르다(중요, 실측 확인)**:

- **투자경고(2)/투자위험(3)**: 컬럼이 "번호,종목명,공시일,지정일,해제일" 5개.
  해제일이 아직 없으면(현재도 지정 유지 중) 그 셀 텍스트가 정확히 ``"-"``
  (실측: 2026-07-30 기준 비비안/모나미/원풍물산/에넥스/아이에이가 투자경고
  해제일 "-"로 실제로 현재 지정 중임을 확인). 이 tier들은 "지정 -> (한동안
  유지) -> 해제"라는 **기간을 갖는 상태**다.
- **투자주의(1)**: 컬럼이 "번호,종목명,유형,공시일,지정일" 5개로 **해제일
  컬럼 자체가 없다**(실측 확인, HTML ``<table summary="...">`` 속성이 tier별로
  다름). "유형" 컬럼엔 "종가급변" 같은 사유가 온다. 이는 실제 제도 자체가
  경고/위험과 다르기 때문이다 — 투자주의종목은 "그날 하루" 통보 개념이라
  해제라는 개념이 없다(다음 날 조건에 안 걸리면 자동으로 목록에서 빠질
  뿐). 이 클라이언트는 이 구조 차이를 그대로 반환값에 반영한다(경고/위험은
  ``released_date``, 주의는 ``warning_type``이 채워지고 반대쪽은 항상
  ``None`` — 개념이 없는 필드를 억지로 채우지 않는다, §5 "정직한 표시" 원칙).

**시장 구분 아이콘 매핑(실측)**: ``<img alt='유가증권'>``=KOSPI,
``alt='코스닥'>``=KOSDAQ, ``alt='코넥스'``=KONEX로 매핑한다(§5.32
``krx_short_selling.py``와 마찬가지로 소스가 이미 구분해 주는 값을 그대로
정규화만 한다).

**조회기간 스코프 판단(실측 데이터 기반)**: 2026-01-01~07-30(7개월) 구간에서
투자경고 407건 중 실제로 "해제일 = -"(현재 지정 유지 중)인 건 전부 지정일이
최근 2주 이내였다(가장 오래된 것도 2026-07-15) — 즉 경고/위험 지정은
실무적으로 몇 주 안에 해제되거나 격상된다. 반면 투자주의는 최근 1년간
4,699건(하루 평균 13건 안팅)으로 압도적으로 많지만 "그날 하루"짜리라 오늘 하루
분량만 있으면 충분하다. 이 근거로 수집기(``collectors/investor_warning.py``)는
경고/위험은 90일, 주의는 10일 lookback만 쓴다(전체 이력을 다 받을 필요가
없다 — 관찰 목적은 "지금 지정돼 있는가"이지 과거 전수 조사가 아니다).

**페이지네이션**: 응답 HTML 끝의 ``전체 <em>N</em>건 : <strong>P</strong>/T``
로 총 건수/현재 페이지/총 페이지를 파싱한다. ``currentPageSize=100``으로
호출하면 위 lookback 범위 안에서는 보통 1~5페이지 안에 끝난다(실측: 경고
7개월 407건=5페이지, 위험 18개월 72건=1페이지).
"""

from __future__ import annotations

import datetime as dt
import html
import logging
import re

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://kind.krx.co.kr"
ENDPOINT = f"{BASE_URL}/investwarn/investattentwarnrisky.do"
REFERER = f"{ENDPOINT}?method=investattentwarnriskyMain"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

TIER_CAUTION = "caution"  # 투자주의
TIER_WARNING = "warning"  # 투자경고
TIER_RISK = "risk"  # 투자위험

# tier -> (menuIndex, forward) — 모듈 docstring "파라미터" 절 실측값 그대로.
_TIER_PARAMS = {
    TIER_CAUTION: {"menuIndex": "1", "forward": "invstcautnisu_sub"},
    TIER_WARNING: {"menuIndex": "2", "forward": "invstwarnisu_sub"},
    TIER_RISK: {"menuIndex": "3", "forward": "invstriskisu_sub"},
}

_MARKET_LABEL = {"유가증권": "KOSPI", "코스닥": "KOSDAQ", "코넥스": "KONEX"}

_TOTAL_PAGES_RE = re.compile(r"전체\s*<em>[\d,]+</em>건\s*:\s*<strong>(\d+)</strong>/(\d+)")

# 종목명 셀 공통 부분: <img src=... alt='시장'> ... <a ... title='종목명'>이름</a>
# (뒤에 관리종목 등 추가 배지 img가 붙을 수 있어 </td>까지 non-greedy로 넘어간다).
# <img> 태그 안에서 alt 속성 앞에 src/class 등이 먼저 나오므로 `<img[^>]*alt=`로
# 그 앞부분을 건너뛴다(실측 HTML: `<img src='...' class='vmiddle legend' alt='...'>`).
_NAME_CELL = (
    r"<img[^>]*alt='(?P<market>[^']+)'[^>]*>\s*<a[^>]*title='(?P<name>[^']*)'[^>]*>.*?</td>"
)

# 투자경고/투자위험: 종목명 뒤에 공시일/지정일/해제일(전부 class="txc") 3개.
_ROW_RE_DATED = re.compile(
    r"<td[^>]*>" + _NAME_CELL
    + r'\s*<td class="txc">(?P<notice>[^<]*)</td>'
    + r'\s*<td class="txc">(?P<designated>[^<]*)</td>'
    + r'\s*<td class="txc">(?P<released>[^<]*)</td>',
    re.S,
)

# 투자주의: 종목명 뒤에 유형(class 없는 plain <td>), 공시일, 지정일 — 해제일 없음.
_ROW_RE_CAUTION = re.compile(
    r"<td[^>]*>" + _NAME_CELL
    + r"\s*<td>(?P<warn_type>[^<]*)</td>"
    + r'\s*<td class="txc">(?P<notice>[^<]*)</td>'
    + r'\s*<td class="txc">(?P<designated>[^<]*)</td>',
    re.S,
)


class KindInvestorWarningError(Exception):
    """Raised when the KIND investwarn endpoint returns an unparsable payload."""


def _parse_date(raw: str | None) -> dt.date | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text or text == "-":
        return None
    try:
        return dt.datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        logger.warning("kind_investor_warning: 날짜 파싱 실패, None 처리: %r", raw)
        return None


def _fetch_page(
    tier: str,
    start: dt.date,
    end: dt.date,
    page_index: int,
    page_size: int,
    timeout: int,
) -> str:
    params = _TIER_PARAMS[tier]
    resp = requests.post(
        ENDPOINT,
        data={
            "method": "investattentwarnriskySub",
            "forward": params["forward"],
            "menuIndex": params["menuIndex"],
            "marketType": "",
            "searchCodeType": "",
            "searchCorpName": "",
            "repIsuSrtCd": "",
            "currentPageSize": str(page_size),
            "pageIndex": str(page_index),
            "orderMode": "",
            "orderStat": "",
            "startDate": start.strftime("%Y-%m-%d"),
            "endDate": end.strftime("%Y-%m-%d"),
        },
        headers={"User-Agent": USER_AGENT, "Referer": REFERER},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.text


def fetch_designations(
    tier: str,
    start: dt.date,
    end: dt.date,
    page_size: int = 100,
    max_pages: int = 10,
    timeout: int = 15,
) -> list[dict]:
    """``tier``(caution/warning/risk)의 [start, end] 구간 지정 이력을 전체
    종목 대상으로 조회한다(모듈 docstring "회사명 검색은 안 쓴다" 절 참고).

    Returns a list of::

        {"tier": str, "market": str|None, "raw_name": str,
         "warning_type": str|None,       # caution 전용, 그 외 tier는 항상 None
         "notice_date": date|None, "designated_date": date|None,
         "released_date": date|None}     # warning/risk 전용, caution은 항상 None

    최대 ``max_pages``페이지까지 이어 받는다(모듈 docstring "페이지네이션" 절
    — lookback을 짧게 잡아 두면 보통 이 한도 전에 끝난다). 응답 스키마가
    tier별로 달라(모듈 docstring "tier별로 응답 테이블 스키마가 다르다" 절)
    캐주얼한 파서 하나로 못 돌려 tier에 따라 정규식을 나눈다.
    """
    if tier not in _TIER_PARAMS:
        raise ValueError(f"unknown tier {tier!r}, expected one of {sorted(_TIER_PARAMS)}")

    row_re = _ROW_RE_CAUTION if tier == TIER_CAUTION else _ROW_RE_DATED

    out: list[dict] = []
    page = 1
    while page <= max_pages:
        page_html = _fetch_page(tier, start, end, page, page_size, timeout)
        if not page_html.strip():
            # 빈 응답 — 조회 구간에 데이터가 아예 없는 정상 케이스(예: 위험종목이
            # 하나도 없는 기간)와 진짜 실패를 구분할 방법이 없어(모듈 docstring
            # "회사명 검색" 절의 0바이트 실패 사례와 동일한 모양) 첫 페이지부터
            # 비면 그대로 빈 리스트를 반환한다(예외로 죽이지 않음 — "지정된
            # 종목이 없음"은 정상 상태다).
            break

        matches = list(row_re.finditer(page_html))
        if not matches and page == 1:
            # 페이지는 왔는데 행 패턴이 하나도 안 잡히면 스키마가 바뀌었다는
            # 신호일 수 있어 조용히 넘어가지 않고 에러로 알린다.
            if "전체 <em>0</em>건" in page_html or "조회된 내용이 없습니다" in page_html:
                break
            raise KindInvestorWarningError(
                f"unexpected KIND investwarn response shape for tier={tier}: {page_html[:300]!r}"
            )

        for m in matches:
            g = m.groupdict()
            # KIND HTML은 종목명/유형에 "&amp;"처럼 HTML 엔티티를 그대로 이스케이프해
            # 내보낸다(실측: "형지I&C" -> "형지I&amp;C") — 이 프로젝트의 stocks.name은
            # 이스케이프되지 않은 원문("형지I&C")이라 unescape하지 않으면 이름 매칭이
            # 조용히 실패한다(collectors/investor_warning.py의 name -> code 매핑 근거).
            raw_name = html.unescape(g["name"]).strip()
            market_raw = html.unescape(g["market"]).strip()
            warn_type = g.get("warn_type")
            warn_type = html.unescape(warn_type).strip() if warn_type else None

            row = {
                "tier": tier,
                "market": _MARKET_LABEL.get(market_raw, market_raw),
                "raw_name": raw_name,
                "warning_type": (warn_type or None) if tier == TIER_CAUTION else None,
                "notice_date": _parse_date(g.get("notice")),
                "designated_date": _parse_date(g.get("designated")),
                "released_date": _parse_date(g.get("released")) if tier != TIER_CAUTION else None,
            }
            out.append(row)

        total_match = _TOTAL_PAGES_RE.search(page_html)
        total_pages = int(total_match.group(2)) if total_match else page
        if page >= total_pages:
            break
        page += 1

    return out
