"""네이버 모바일 증권 거래대금 상위 종목 — 거래량 상위(QUANT_TOP) 벌크 랭킹 API를
전 종목 순회해 거래대금(백만원) 내림차순으로 재정렬한 "거래대금 상위" (PLAN.md §4.6
3.6-1) — "돈이 모이는 곳".

## 소스 실확정 경과 (2026-07-18 실호출)

PLAN.md가 후보로 제시한 두 갈래를 모두 실측했다:

1. **PC ``finance.naver.com/sise/sise_quant.naver?sosok={0=코스피,1=코스닥}``**
   (주의: 이 페이지의 sosok은 naver_rank.py의 sise_deal_rank_iframe과 값이
   반대다 — 0=코스피/1=코스닥. 페이지 안 탭(``<div class="tab_smeun">``)으로
   실측 확인). 메뉴 라벨이 "거래상위"라 거래대금 정렬로 오해하기 쉽지만, 실제
   행 순서를 파싱해 검증한 결과 **거래량(체결 주식수) 내림차순**이었다(거래대금
   컬럼 값은 순서와 무관하게 들쭉날쭉). 컬럼 선택 체크박스(항목선택)는 "어떤
   컬럼을 보여줄지"만 바꿀 뿐 정렬 기준을 바꾸는 기능이 없다(테이블 헤더가
   `<th>` 평문이고 정렬 링크가 없음 — 실제 HTML 확인).
2. **모바일 ``m.stock.naver.com/api/stocks/{sortType}/{KOSPI|KOSDAQ}``**
   (Next.js 프로덕션 JS 청크 ``pages/domestic/home/capitalization/total-*.js``를
   받아 ``E.pY`` enum을 역참조해 유효 sortType 슬러그를 확정했다 — 브루트포스
   추측이 아니라 실제 클라이언트 코드에서 읽음): ``marketValue``(시가총액),
   ``up``/``down``(상승/하락), ``quantTop``(거래상위 — PC와 동일 소스, 거래량
   정렬), ``priceTop``, ``searchTop``, ``newStock``, ``management``,
   ``high52week``, ``low52week``. **"거래대금(금액) 내림차순" 전용 sortType은
   존재하지 않는다** — PC든 모바일이든 네이버가 무료로 제공하는 건 "거래량 상위"
   뿐이다.

## 채택한 접근: 거래량 순위 API를 "전 종목 순회 + 로컬 재정렬" 용도로 재사용

거래량 상위(quantTop) API가 마침 각 종목의 ``accumulatedTradingValueRaw``(거래대금)와
``marketValueRaw``(시가총액/AUM)까지 필드로 함께 준다(정렬 기준이 거래량일 뿐,
거래대금 값 자체는 정상 포함). 그리고 이 API는 ``pageSize``(최대 100, 그 이상은
400 에러 — 실측)와 ``page``를 지원해 **시장 전체 종목을 완주할 수 있다**(1차
호출로 ``totalCount``를 받아 필요한 페이지 수를 계산). 코스피 실측 totalCount
2,478개(25페이지), 코스닥 1,821개(19페이지) — ETF/ETN/코넥스 등이 섞여 있어
상장 종목 수보다 크다.

**왜 상위 N페이지만 받지 않고 전량을 받는가**: 거래량 상위 100개 안에서 거래대금
내림차순으로 재정렬해 봤더니(코스피 실측), 진짜 거래대금 상위 50위 안에 들어야
할 종목이 거래량 순위로는 99위까지 밀려나 있는 경우가 있었다(예: SK하이닉스는
거래량 41위인데 거래대금은 1위, 삼성전자우는 거래량 48위인데 거래대금은 13위).
즉 거래량 상위 100~300 정도의 "안전 마진"을 임의로 잡아도 어느 날은 고가·저회전
종목이 그 밖으로 빠질 위험이 있다 — 근사치가 아니라 정확한 순위를 보장하려면
전량 순회가 유일한 방법이다. 시장당 19~25회(총 ~44회) 호출에 0.5초 간격을
둬도 30초 남짓이라 배치 실행 시간상 무리 없다(collectors/flow_rank.py도
회전율 조회로 최대 ~100회 호출).

## 단위·필드 실측 결과 (2026-07-18, 005930 삼성전자 기준)

- ``accumulatedTradingValueRaw``(문자열 정수, 원 단위) ÷ 1,000,000 = 백만 원.
  실측: "6838413000000" ÷ 1e6 = 6,838,413 — 화면용 ``accumulatedTradingValue``
  필드("6,838,413")와 정확히 일치(그 필드가 이미 백만원 콤마 문자열이라 이 모듈은
  Raw 필드를 직접 정수로 파싱해 관례를 통일한다).
- ``marketValueRaw``(문자열 정수, 원 단위) ÷ 1,000,000 = 백만 원. ETF에도 항상
  채워진다(펀드 AUM) — 실측 KODEX 200(069500): marketValueRaw=24,377,850,000,000
  ÷ 1e6 = 24,377,850백만원 = 24.38조원, naver_etf.py 문서의 같은 종목 AUM
  실측치(24조 3,779억)와 일치. **이 필드 덕분에 flow_rank.py처럼 종목별
  integration API를 추가로 부를 필요가 없다** — turnover(회전율) =
  accumulatedTradingValueRaw ÷ marketValueRaw × 100 을 이 API 응답 하나로 전
  종목에 대해 계산할 수 있다(PLAN.md가 예상한 "개별주는 flow_rank 방식(integration
  API) 재사용, 호출 수 고려해 상위 50개만" 제약이 이 소스에서는 필요 없어진
  것 — collectors/value_rank.py에서 전량에 대해 turnover를 채운다).
- ``fluctuationsRatio``: 부호 포함 문자열("-8.77", "13.64" — 양수는 접두 부호
  없음) → ``float()``로 바로 파싱 가능(PC 소스처럼 별도 상승/하락 CSS 클래스와
  조합할 필요 없음).
- ``stockEndType``: "stock"/"etf"/"etn" 등 소스 자체 분류 필드가 있지만, 이
  모듈은 PLAN.md 지시(및 collectors/flow_rank.py와의 일관성)에 따라 이 필드를
  신뢰하지 않고 clients/naver_rank.fetch_etf_codes()(etfItemList 코드셋 대조)를
  그대로 재사용해 is_etf를 태깅한다(호출자 책임 — 이 클라이언트는 stockEndType을
  raw 필드로만 반환).
- ``localTradedAt``: 종목별 마지막 체결 시각("2026-07-16T16:10:19+09:00") —
  거래일 자체를 얻을 수 있는 유일한 필드라, 이 모듈은 응답의 첫 종목 값에서
  날짜만 뽑아 그 페이지 묶음의 거래일로 쓴다(호출자가 조합).
- ``pageSize``는 100 초과 시 HTTP 400(실측: 120부터 400) — 그래서 이 모듈은
  100 초과 요청을 거부한다.
- **날짜 파라미터 없음**: naver_rank.py의 sise_deal_rank_iframe과 마찬가지로
  이 API도 "현재/가장 최근 거래일" 스냅샷만 준다 — 과거 날짜 백필 불가
  (scripts/backfill_value_rank.py 참고).
"""

from __future__ import annotations

import datetime as dt
import time

import requests

RANK_URL = "https://m.stock.naver.com/api/stocks/quantTop/{market}"

# 모바일 sortType 슬러그가 쓰는 시장 코드 — naver_rank.py의 sosok(01/02)과는
# 완전히 다른 네임스페이스(문자열 KOSPI/KOSDAQ 그대로)라 별도로 정의한다.
MARKET_CODE = {"kospi": "KOSPI", "kosdaq": "KOSDAQ"}

MAX_PAGE_SIZE = 100

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class NaverValueRankError(Exception):
    """Raised when the quantTop response is empty/unparsable."""


def _parse_raw_int(s: str | None) -> int | None:
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _parse_float(s: str | None) -> float | None:
    if s is None:
        return None
    s = s.strip()
    if not s or s == "N/A":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fetch_quant_top_page(market: str, page: int, page_size: int = MAX_PAGE_SIZE, timeout: int = 15) -> dict:
    """quantTop(거래량 상위) API 1페이지를 그대로(raw) 반환한다.

    ``page_size``는 100을 넘길 수 없다(실측: 초과 시 HTTP 400). ``page``가
    전체 페이지 수를 넘어가면 에러 없이 ``stocks``가 빈 리스트로 온다(실측) —
    호출자가 이를 순회 종료 조건으로 쓸 수 있다.
    """
    if page_size > MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be <= {MAX_PAGE_SIZE} (naver returns HTTP 400 above it)")
    market_code = MARKET_CODE.get(market)
    if market_code is None:
        raise ValueError(f"unknown market {market!r}, expected one of {sorted(MARKET_CODE)}")

    resp = requests.get(
        RANK_URL.format(market=market_code),
        params={"page": page, "pageSize": page_size},
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_all(
    market: str, page_size: int = MAX_PAGE_SIZE, timeout: int = 15, sleep_seconds: float = 0.0
) -> dict:
    """``market``(kospi/kosdaq)의 전 종목을 거래량 상위 API로 완주해, 거래대금
    (백만 원) 내림차순으로 재정렬한 rows를 반환한다(모듈 docstring "채택한 접근"
    참고 — 전량 순회라야 정확한 거래대금 순위를 보장할 수 있다).

    ``sleep_seconds``: 페이지 호출 사이에 넣을 대기 시간(초, 기본 0 — 호출자가
    안 넘기면 지연 없이 순회). collectors/value_rank.py는 다른 네이버 호출과
    동일한 정책(0.5초)을 넘겨 서버 부담/차단을 피한다. 이 함수는
    ``asyncio.to_thread``로 감싸 호출되는 걸 전제로 ``time.sleep``(블로킹)을
    그대로 쓴다(collectors/flow_rank.py와 동일 패턴).

    Returns ``{"date": dt.date | None, "rows": [...]}`` — rows는 이미 value_million
    내림차순 정렬됨. 각 row: ``{"code", "name", "value_million"(거래대금),
    "market_value_million"(시가총액/AUM), "change_rate"(등락률 %), "stock_end_type"}``.
    ``date``는 응답에 담긴 첫 종목의 ``localTradedAt`` 날짜부(part) — 종목마다
    날짜가 갈리는 경우는 관측되지 않았지만(동일 장 마감 스냅샷), 혹시 갈리면
    가장 흔한(mode) 날짜를 쓴다.
    """
    first = fetch_quant_top_page(market, page=1, page_size=page_size, timeout=timeout)
    total_count = first.get("totalCount") or 0
    if not first.get("stocks"):
        raise NaverValueRankError(f"quantTop({market}) page 1 returned no stocks (totalCount={total_count})")

    all_stocks: list[dict] = list(first["stocks"])
    total_pages = -(-total_count // page_size) if total_count else 1
    for page in range(2, total_pages + 1):
        if sleep_seconds:
            time.sleep(sleep_seconds)
        data = fetch_quant_top_page(market, page=page, page_size=page_size, timeout=timeout)
        stocks = data.get("stocks") or []
        if not stocks:
            break
        all_stocks.extend(stocks)

    date_counts: dict[dt.date, int] = {}
    rows: list[dict] = []
    for s in all_stocks:
        code = s.get("itemCode")
        if not code:
            continue
        value_million = _parse_raw_int(s.get("accumulatedTradingValueRaw"))
        value_million = None if value_million is None else round(value_million / 1_000_000)
        market_value_million = _parse_raw_int(s.get("marketValueRaw"))
        market_value_million = None if market_value_million is None else round(market_value_million / 1_000_000)

        traded_at = s.get("localTradedAt")
        if traded_at:
            try:
                d = dt.datetime.fromisoformat(traded_at).date()
                date_counts[d] = date_counts.get(d, 0) + 1
            except ValueError:
                pass

        rows.append(
            {
                "code": code,
                "name": s.get("stockName"),
                "value_million": value_million,
                "market_value_million": market_value_million,
                "change_rate": _parse_float(s.get("fluctuationsRatio")),
                "stock_end_type": s.get("stockEndType"),
            }
        )

    rows.sort(key=lambda r: r["value_million"] if r["value_million"] is not None else -1, reverse=True)

    date = max(date_counts, key=lambda d: date_counts[d]) if date_counts else None
    return {"date": date, "rows": rows}
