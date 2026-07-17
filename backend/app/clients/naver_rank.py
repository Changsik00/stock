"""네이버 증권 투자자별 순매수 상위 종목 — sise_deal_rank_iframe.naver 파싱 (PLAN.md §4.5).

소스: ``https://finance.naver.com/sise/sise_deal_rank_iframe.naver``
``?sosok={01=코스피,02=코스닥}&investor_gubun={9000=외국인,1000=기관}&type={buy,sell}``

실호출 확인(2026-07-18, Playwright 없이 curl/requests로 충분히 검증됨):

- 서버렌더 HTML, EUC-KR. 응답 헤더에 ``Content-Type: text/html;charset=EUC-KR``가
  명시돼 있어 ``requests``가 자동으로 ``response.encoding``을 잡는다 — naver_index.py의
  fchart 엔드포인트와 달리 수동 디코딩이 필요 없다(``resp.text``만으로 정상 UTF-8
  문자열).
- **날짜 파라미터를 받지 않는다**: ``date=``/``day=``/``sdate=``/``gubun=`` 등을 시도했지만
  전부 무시되고 항상 최근 2거래일 고정 응답이 온다. 응답 안에
  ``<div class="sise_guide_date">YY.MM.DD</div>`` 블록이 2개 들어 있고(오래된 날짜가
  먼저, 최근 날짜가 나중) 각 블록 아래 표에 상위 20종목이 있다. 즉 **임의 과거 날짜
  조회는 이 페이지로 불가능**하다 — scripts/backfill_flow_rank.py 참고.
- ``sosok``(코스피/코스닥)별로 완전히 분리된 랭킹이다. "코스피+코스닥 전체" 통합 탭은
  존재하지 않는다.
- 표 상단 안내 문구가 "(단위:천주, 백만원)"이다: 두 번째 td(수량)는 천주, 세 번째
  td(금액 — 우리가 저장하는 net_value)는 백만원. 콤마 구분 정수, buy 랭킹에서는 관측된
  범위 내 음수 없음.
- 종목코드는 숫자 6자리가 대부분이지만 레버리지/인버스 ETN류는 영숫자 혼합 코드도
  나온다(예: ``0195S0`` = TIGER SK하이닉스단일종목레버리지) — 코드 정규식은
  ``[0-9A-Za-z]+``.
- 상위 종목 개수는 페이지당 **20개 고정**(50개 아님) — PLAN.md가 "가능하면 50"을
  희망했지만 소스가 20개까지만 제공한다.
- **type=sell 실호출 확인(2026-07-18)**: 표 헤더가 "종목명/수량/금액/당일거래량"
  4열이고(buy도 동일한 4열 — 마지막 "당일거래량"은 그 종목의 시장 전체 거래량이라
  투자자 순매수/매도와 무관해 이 클라이언트는 파싱하지 않는다), **수량·금액 두 열
  모두 마이너스 부호가 붙어 온다**(예: 대덕전자 수량 -243, 금액 -33,418 — "그
  투자자가 243천주를 순매도해서 -33,418백만원어치 팔았다"는 뜻). 이 모듈은 소스
  값을 그대로(부호 포함) 반환한다 — buy/sell 두 값을 **양수 크기 + side 컬럼**으로
  통일해 저장하는 정규화는 호출자(collectors/flow_rank.py)의 책임이다(PLAN.md §6
  3.5-2b "매도 금액을 음수가 아니라 양수+side로 통일" 결정 — 부호와 방향 컬럼을
  동시에 두면 어느 쪽이 진실인지 헷갈리는 걸 피하기 위함).
"""

from __future__ import annotations

import datetime as dt
import re

import requests

from .naver_etf import parse_won_string_to_million

IFRAME_URL = "https://finance.naver.com/sise/sise_deal_rank_iframe.naver"

# 개별 종목의 당일 거래대금·시가총액 — flow_rank.turnover(회전율 = 거래대금/시가총액)
# 계산용. ETF는 clients/naver_etf.fetch_etf_list()가 이미 이 값들을 벌크로 주므로
# (amount_million/aum_million) 이 엔드포인트를 쓰지 않는다 — 개별주 전용.
# 실호출 확인(2026-07-18, 005930 기준): totalInfos 배열에 accumulatedTradingValue
# ("11조 4,016억")·marketValue("1,490조 8,010억") 키가 한글 단위 문자열로 온다.
# ETF에도 같은 엔드포인트가 응답하지만 marketValue가 없다(펀드라 시총 대신 NAV만
# 제공) — 그래서 ETF는 여기서 다루지 않는다.
STOCK_INTEGRATION_URL = "https://m.stock.naver.com/api/stock/{code}/integration"

# is_etf 태깅용 — stocks.is_etf에 의존하지 않고(다른 배치가 동시에 stocks를 적재
# 중이라 PLAN.md §4.5 지시에 따라 의존 금지) 독립적으로 조회한다. EUC-KR JSON이지만
# requests가 Content-Type 헤더의 charset을 그대로 읽어 .json()에서 자동 디코딩된다
# (실측 확인, 2026-07-18).
ETF_LIST_URL = "https://finance.naver.com/api/sise/etfItemList.nhn"

# sosok: 01=코스피, 02=코스닥 (finance.naver.com/sise/sise_deal_rank.naver 페이지의
# 코스피/코스닥 탭 링크에서 확인).
MARKET_SOSOK = {"kospi": "01", "kosdaq": "02"}

# investor_gubun: 9000=외국인, 1000=기관 (같은 페이지의 "외국인매매"/"기관매매" 탭
# 링크에서 확인. 개인 탭은 없음 — 네이버 이 페이지는 외인/기관만 제공).
INVESTOR_GUBUN = {"foreign": "9000", "institution": "1000"}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_DATE_RE = re.compile(r'<div class="sise_guide_date">(\d{2})\.(\d{2})\.(\d{2})</div>')
_ROW_RE = re.compile(
    r"<a href=\"/item/main\.naver\?code=(?P<code>[0-9A-Za-z]+)\"[^>]*"
    r"title='(?P<name>[^']*)'>.*?</a>\s*</p></td>\s*"
    r'<td class="number">(?P<qty>-?[\d,]+)</td>\s*'
    r'<td class="number">(?P<amount>-?[\d,]+)</td>',
    re.DOTALL,
)


class NaverRankError(Exception):
    """Raised when the deal-rank iframe response has no parsable date blocks."""


def fetch_deal_rank(
    market: str, investor: str, type_: str = "buy", timeout: int = 15
) -> list[dict]:
    """market(kospi/kosdaq) x investor(foreign/institution)의 순매수 상위 20종목을
    네이버가 제공하는 최근 2거래일 분량 그대로 반환한다.

    Returns ``[{"date": dt.date, "rows": [{"code": str, "name": str, "net_value": int,
    "quantity": int}, ...]}, ...]`` — 날짜 오름차순(오래된 날짜 먼저), 각 rows는 순위
    순서(1위부터) 그대로. net_value 단위는 백만 원, quantity 단위는 천주 — 둘 다
    소스가 준 부호 그대로다(type="sell"이면 음수 — 모듈 docstring 참고, 양수 정규화는
    호출자 책임).
    """
    sosok = MARKET_SOSOK.get(market)
    if sosok is None:
        raise ValueError(f"unknown market {market!r}, expected one of {sorted(MARKET_SOSOK)}")
    gubun = INVESTOR_GUBUN.get(investor)
    if gubun is None:
        raise ValueError(f"unknown investor {investor!r}, expected one of {sorted(INVESTOR_GUBUN)}")

    resp = requests.get(
        IFRAME_URL,
        params={"sosok": sosok, "investor_gubun": gubun, "type": type_},
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    resp.raise_for_status()
    text = resp.text

    date_matches = list(_DATE_RE.finditer(text))
    if not date_matches:
        raise NaverRankError(
            f"no date blocks parsed for market={market} investor={investor}; "
            f"response head: {text[:200]!r}"
        )

    blocks: list[dict] = []
    for i, dm in enumerate(date_matches):
        start = dm.end()
        end = date_matches[i + 1].start() if i + 1 < len(date_matches) else len(text)
        segment = text[start:end]
        yy, mm, dd = dm.groups()
        block_date = dt.date(2000 + int(yy), int(mm), int(dd))

        rows = [
            {
                "code": rm.group("code"),
                "name": rm.group("name"),
                "net_value": int(rm.group("amount").replace(",", "")),
                "quantity": int(rm.group("qty").replace(",", "")),
            }
            for rm in _ROW_RE.finditer(segment)
        ]
        blocks.append({"date": block_date, "rows": rows})

    blocks.sort(key=lambda b: b["date"])
    return blocks


def fetch_etf_codes(timeout: int = 15) -> set[str]:
    """국내 상장 ETF 전종목의 itemcode 집합을 반환한다 (flow_rank.is_etf 태깅용)."""
    resp = requests.get(ETF_LIST_URL, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("result", {}).get("etfItemList", [])
    return {item["itemcode"] for item in items if item.get("itemcode")}


def fetch_stock_market_value(code: str, timeout: int = 15) -> dict:
    """개별 종목(ETF 아님) 1개의 당일 거래대금·시가총액을 반환한다 (flow_rank.turnover
    계산용, 모듈 docstring의 STOCK_INTEGRATION_URL 참고).

    Returns ``{"accumulated_trading_value_million": int | None,
    "market_value_million": int | None}`` — 둘 다 백만 원 단위(parse_won_string_to_million
    변환 후). ``totalInfos``에 해당 code가 없거나(ETF처럼 marketValue가 아예 없는
    경우 포함) 파싱 실패("-") 시 그 필드만 None — 예외를 던지지 않는다(수백 종목을
    순회하는 배치에서 종목 하나 실패로 전체가 죽지 않도록).
    """
    resp = requests.get(
        STOCK_INTEGRATION_URL.format(code=code),
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()

    values: dict[str, str] = {
        info.get("code"): info.get("value")
        for info in data.get("totalInfos") or []
        if info.get("code")
    }

    return {
        "accumulated_trading_value_million": parse_won_string_to_million(
            values.get("accumulatedTradingValue")
        ),
        "market_value_million": parse_won_string_to_million(values.get("marketValue")),
    }
