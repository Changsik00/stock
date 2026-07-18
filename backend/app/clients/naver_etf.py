"""네이버 ETF 목록·상세(구성종목/순유입) 클라이언트 (PLAN.md §4.5/§6 Phase 3.5-1).

두 개의 무키 네이버 엔드포인트를 감싼다:

1. ``GET https://finance.naver.com/api/sise/etfItemList.nhn``
   전체 ETF 목록(2026-07-18 실확인 1,146종목) — 종목코드/이름/NAV/거래량/거래대금/
   시가총액/탭분류(etfTabCode). 응답은 ``Content-Type: text/plain;charset=EUC-KR``라
   ``requests``의 기본 utf-8 디코딩을 쓰면 깨진다 — 반드시 ``resp.content``를
   ``euc-kr``로 직접 디코딩해야 한다(``resp.encoding = "euc-kr"`` 후 ``resp.json()``도
   동일하게 동작하지만 이 모듈은 명시적으로 바이트를 디코딩한다).

2. ``GET https://m.stock.naver.com/api/stock/{code}/etfAnalysis``
   ETF 상세 — top10 구성종목·비중, 순유입 스냅샷(cumulativeNetInflowList), NAV,
   시가총액(marketValue), 국가/자산 포트폴리오 비중 등. User-Agent가 없어도 200이
   오긴 하지만(2026-07-18 실확인) 우회 차단 리스크를 줄이기 위해 항상 지정한다.

## 단위 실측 결과 (2026-07-18, KODEX 200 069500 기준)

- ``etfItemList`` 의 ``amonut``(원문 오타, "거래대금")은 **백만 원** 단위다.
  검증: ``quant(거래량) * nowVal(종가) / 1e6`` ≈ ``amonut`` (KODEX 200:
  17,769,408주 * 109,000원 / 1e6 = 1,937,265 ≈ amonut=1,938,809, VWAP과 종가
  차이만큼만 오차). ``stock_ohlcv.value``와 동일 관례로 그대로 저장 가능.
- ``etfItemList`` 의 ``marketSum``("시가총액")은 **억 원** 단위다(백만 원이
  아님). 검증: ``etfAnalysis.marketValue`` 문자열("24조 3,779억")을 원 단위로
  환산 후 백만 원으로 나누면 24,377,900이고, 이는 ``marketSum(243,779) * 100``과
  정확히 일치한다. 그래서 ``fetch_etf_list``가 반환하는 ``aum_million`` 필드는
  ``marketSum * 100``으로 미리 변환해 백만 원 단위로 통일한다(§5.2 "금액은 백만
  원" 관례 준수).
- ``etfAnalysis``의 금액 필드(``marketValue``/``totalNav``/``cumulativeNetInflowList``의
  각 기간 값)는 모두 "24조 3,779억", "375억", "-72.9억", "6.79억", "-"(데이터 없음)
  같은 한글 단위 문자열이다 — ``parse_won_string_to_million``으로 파싱한다.

## cumulativeNetInflowList 실제 구조 (2026-07-18 실호출, 종목 30여 개 표본 조사)

이름과 달리 **리스트가 아니라 dict 하나**다:

```json
{
  "referenceDate": "2026.07.15",
  "cumulativeNetInflow1d": "703억",
  "cumulativeNetInflow1w": "8,684억",
  "cumulativeNetInflow1m": "7,240억",
  "cumulativeNetInflow3m": "-7,321억",
  "cumulativeNetInflow6m": "3조 909억",
  "cumulativeNetInflowYtd": "2조 5,105억",
  "cumulativeNetInflow1y": "4조 1,357억"
}
```

즉 **하루치 스냅샷 하나**가 "1일/1주/1개월/3개월/6개월/YTD/1년" 누적 순유입을
``referenceDate`` 기준으로 얹어 주는 형태다 — 과거 여러 날짜의 시계열을 한 번에
주는 게 아니다. 필드 이름의 "List"는 "(기간별 누적치들의) 목록"이라는 뜻이지
"일별 리스트"가 아니다.

**일별화 방법**: 다행히 ``cumulativeNetInflow1d`` 자체가 이미 "referenceDate
하루의 순유입"이다(1주일치 diff를 만들 필요가 없다 — 1w/1m/... 은 다일 누적이라
일별로 diff할 수 없지만, 1d는 그 자체로 1일 값). 그래서 이 클라이언트는
**차분(diff) 로직 없이 ``cumulativeNetInflow1d``를 그날의 net_inflow로 직접
사용**한다. 매일 배치를 돌려 하루씩 적재하면 자연히 일별 시계열이 쌓인다.
과거로의 소급(backfill)은 이 스냅샷만으로는 불가능하다 — referenceDate는 항상
"오늘"이고 지난 날짜의 1d 값을 다시 조회할 방법이 없다(scripts/backfill_etf.py
참고: net_inflow 백필은 이 클라이언트를 매일 재실행하며 쌓는 것 외에는 방법이
없어, 최초 실행일 하루치만 적재된다).

주의: ``cumulativeNetInflow1d``가 종목별로 종종 ``"-"``(데이터 없음)로 오기도
한다(2026-07-18 표본: 30개 중 4개) — 이 경우 net_inflow는 NULL로 남긴다.
"""

from __future__ import annotations

import datetime as dt
import re

import requests

LIST_URL = "https://finance.naver.com/api/sise/etfItemList.nhn"
ANALYSIS_URL = "https://m.stock.naver.com/api/stock/{code}/etfAnalysis"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# etfTabCode 의미 (2026-07-18, itemname 표본으로 역산):
#   1 = 국내 시가총액식(대표지수: KODEX 200/TIGER 200/코스닥150 등)
#   2 = 국내 업종/테마
#   3 = 국내 파생(레버리지/인버스)
#   4 = 해외주식
#   5 = 원자재
#   6 = 채권
#   7 = 혼합자산/TDF/머니마켓
#
# 유니버스 원칙 (2026-07-18 개정 — "이름으로 배제하지 않는다. 보유 종목이 말하게 한다"):
#
# - tab 1/2/3/7을 전부 후보로 삼는다. 과거에는 tab (1,2) + 이름에 레버리지/인버스가
#   없는 것만 골랐는데, 실측 결과 이 휴리스틱이 정반대로 동작했다:
#   "KODEX SK하이닉스단일종목레버리지"(0193T0, tab2)는 이름과 달리 **실물 주식
#   (000660)을 90.58% 보유**하는데 이름 필터로 제외돼 있었고(거래대금 전체 1위,
#   4.2조 원인데도!), KODEX 레버리지(122630)는 tab3이라 탭 필터에서 빠졌지만
#   실제로는 삼성전자 18.46% 등 실물 바스켓을 상당 부분 보유한다.
# - tab 7(혼합)에는 'RISE 삼성전자SK하이닉스채권혼합50'처럼 국내 주식을 실보유하는
#   채권혼합형이 있어 포함한다. 해외혼합/TDF는 top10에 국내 주식코드가 없어
#   etf_holdings 적재 단계에서 자연 탈락한다(parse_top10_holdings가 코드 없는 행을
#   버리므로 — 별도 이름 필터 불필요).
# - tab 4(해외주식)/5(원자재)/6(채권)은 국내 주식 실보유 가능성이 사실상 없어 후보
#   자체에서 제외(요청 수를 아끼기 위한 것일 뿐, 포함해도 자연 탈락한다).
# - 인버스/선물형(tab3 일부)은 top10이 선물·현금뿐이라 보유 주식이 없고, look-through
#   기여가 0이 되는 게 **정상 동작**이다(KODEX 인버스 실측: 원화현금/선물만).
#   이들의 자금 유입 자체는 추후 '파생형 ETF 자금' 지표로 별도 표시(PLAN.md §6 3.5-4).
DOMESTIC_EQUITY_TABS = (1, 2, 3, 7)


class NaverEtfError(Exception):
    """Raised when a naver ETF response is empty/unparsable."""


def _decode_euckr_json(resp: requests.Response) -> dict:
    """etfItemList.nhn은 EUC-KR 텍스트라서 requests 기본 디코딩(utf-8 추정)이 깨진다."""
    import json

    return json.loads(resp.content.decode("euc-kr", errors="strict"))


def fetch_etf_list(timeout: int = 15) -> list[dict]:
    """전체 ETF 목록을 반환한다.

    Returns a list of::

        {
            "code": str, "name": str, "tab_code": int,
            "nav": float | None, "now_value": int | None,
            "quant": int | None,               # 거래량(주)
            "amount_million": int | None,       # 거래대금, 백만 원 (amonut 그대로)
            "aum_million": int | None,          # 시가총액, 백만 원 (marketSum * 100)
        }
    """
    resp = requests.get(LIST_URL, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    data = _decode_euckr_json(resp)

    items = (data.get("result") or {}).get("etfItemList") or []
    if not items:
        raise NaverEtfError(f"etfItemList empty; response head: {resp.text[:200]!r}")

    out = []
    for it in items:
        market_sum = it.get("marketSum")
        out.append(
            {
                "code": it.get("itemcode"),
                "name": it.get("itemname"),
                "tab_code": it.get("etfTabCode"),
                "nav": it.get("nav"),
                "now_value": it.get("nowVal"),
                "quant": it.get("quant"),
                "amount_million": it.get("amonut"),
                "aum_million": None if market_sum is None else market_sum * 100,
            }
        )
    return out


def select_domestic_equity_targets(items: list[dict], top_n: int = 300) -> list[dict]:
    """국내 주식 보유 가능성이 있는 ETF의 거래대금 상위 ``top_n``개를 고른다.

    조건: etfTabCode in (1, 2, 3, 7) — 국내 시총식/업종테마/국내파생/혼합 — 전부.
    이름 기반 제외(레버리지/인버스 등)는 **하지 않는다**: 실제 국내 주식을 보유하는지는
    etfAnalysis top10 파싱(parse_top10_holdings)에서 주식코드 유무로 판정되고, 보유
    주식이 없는 인버스/선물형은 etf_holdings에 행이 안 생겨 자연 탈락한다
    (위 DOMESTIC_EQUITY_TABS 주석의 유니버스 원칙 참고).

    top_n 기본 300: 2026-07-18 실측 기준 tab 1/2/3 합계가 464개(+tab7 115개)라
    전량 수집은 과하고, 거래대금 300위 언저리는 일 거래대금 ~6억 원 수준까지
    내려가 look-through 기여가 무시할 만하다.
    """
    candidates = [
        it for it in items if it.get("tab_code") in DOMESTIC_EQUITY_TABS and it.get("name")
    ]
    candidates.sort(key=lambda it: it.get("amount_million") or 0, reverse=True)
    return candidates[:top_n]


_JO_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*조")
_EOK_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*억")


def parse_won_string_to_million(s: str | None) -> int | None:
    """"24조 3,779억", "375억", "-72.9억", "6.79억", "-", "0" -> 백만 원(int) | None.

    네이버 etfAnalysis의 금액 필드는 원화를 조/억 단위 한글 문자열로 표현한다.
    "-"는 데이터 없음(None)이다. 조/억 단위가 전혀 없는 순수 숫자(예: "0")는
    그대로 0으로 취급한다(관측된 유일한 무단위 값은 "0"이었다 — §모듈독스트링).
    """
    if s is None:
        return None
    s = s.strip()
    if not s or s == "-":
        return None

    negative = s.startswith("-")
    body = s[1:] if negative else s

    jo_match = _JO_RE.search(body)
    eok_match = _EOK_RE.search(body)

    if jo_match is None and eok_match is None:
        # 단위 접미사가 없는 경우 — 관측된 유일한 케이스는 "0"이다. 그 외
        # 파싱 불가한 값은 조용히 삼키지 않고 None을 반환해 상위에서 로깅하게 한다.
        try:
            eok_value = float(body.replace(",", ""))
        except ValueError:
            return None
    else:
        jo_value = float(jo_match.group(1).replace(",", "")) if jo_match else 0.0
        eok_value = float(eok_match.group(1).replace(",", "")) if eok_match else 0.0
        eok_value += jo_value * 10000  # 1조 = 10,000억

    million = eok_value * 100  # 1억 = 100백만원
    result = round(million)
    return -result if negative else result


def _parse_weight(s: str | None) -> float | None:
    if s is None:
        return None
    s = s.strip().rstrip("%")
    if not s or s == "-":
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _parse_shares(s: str | None) -> int | None:
    if s is None:
        return None
    s = s.strip()
    if not s or s == "-":
        return None
    try:
        return int(s.replace(",", ""))
    except ValueError:
        return None


def fetch_etf_analysis(code: str, timeout: int = 15) -> dict:
    """ETF 상세(``etfAnalysis``) raw JSON을 반환한다."""
    url = ANALYSIS_URL.format(code=code)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if not data or "itemCode" not in data:
        raise NaverEtfError(f"etfAnalysis({code}) unexpected response: {resp.text[:200]!r}")
    return data


def parse_top10_holdings(analysis: dict) -> list[dict]:
    """``etfTop10MajorConstituentAssets`` -> ``[{"stock_code","weight","shares"}, ...]``.

    **실제 상장 코드가 있고 비중이 파싱되는 행만** 남긴다. 이 규칙 하나로 자산 유형
    필터가 전부 해결된다 (2026-07-18 실측 근거):

    - 해외 주식: itemCode "" + etfWeight "-" (TIGER 미국S&P500) -> 제외
    - 선물: itemCode "" + etfWeight "-" ("2026-08 SK하이닉스개별선물") -> 제외
    - 원화현금/설정현금액: itemCode "" (weight는 있을 수 있음, 인버스형은 100.00%도
      관측) -> 제외
    - 국고채/통안채: itemCode "" (채권혼합형) -> 제외
    - 단일종목 레버리지의 실물 주식: itemCode 정상 + weight 정상 — KODEX SK하이닉스
      단일종목레버리지(0193T0)는 000660 90.58%, KODEX 삼성전자단일종목레버리지
      (0193W0)는 005930 92.72%, TIGER SK하이닉스단일종목레버리지(0195S0)는 000660
      80.16%로 잡힌다(나머지는 현금+개별선물). 100% 초과 weight는 주식 행에서는
      관측되지 않았다("설정현금액 100.00%" 행은 코드가 없어 어차피 제외).

    알려진 데이터 한계: 일부 채권혼합형(예: RISE 삼성전자SK하이닉스채권혼합50,
    0162Z0)은 주식 행에 itemCode는 있는데 etfWeight가 "-"로 온다 — 비중을 알 수
    없어 look-through 계산이 불가능하므로 그대로 제외된다(stockCount만으로 %를
    복원할 수 없음). 이런 ETF는 holdings가 비어 자연 탈락한다.

    주의: 파생형 ETF(KODEX 레버리지 등)는 top10에 **다른 ETF**(KODEX 200 20.86% 등)를
    보유하기도 한다. ETF도 상장 코드라 행이 남는데, 현 단계에서는 재귀 분해 없이
    그 ETF 코드로의 기여로 기록된다(한계 — PLAN.md §4.5).
    """
    rows = analysis.get("etfTop10MajorConstituentAssets") or []
    out = []
    for row in rows:
        stock_code = (row.get("itemCode") or "").strip()
        weight = _parse_weight(row.get("etfWeight"))
        if not stock_code or weight is None:
            continue
        out.append(
            {
                "stock_code": stock_code,
                "stock_name": row.get("itemName"),
                "weight": weight,
                "shares": _parse_shares(row.get("stockCount")),
            }
        )
    return out


def parse_net_inflow_snapshot(analysis: dict) -> dict:
    """``cumulativeNetInflowList`` -> ``{"reference_date": date | None, "net_inflow_1d_million": int | None}``.

    §모듈 독스트링 참고 — 리스트가 아니라 단일 스냅샷 dict이며, 이 함수가
    쓰는 건 그 중 "1d"(오늘 하루치) 필드 하나뿐이다. 나머지 기간(1w/1m/3m/6m/
    ytd/1y)은 다일 누적이라 diff 없이는 일별화할 수 없어 이 클라이언트에서는
    사용하지 않는다(호출측이 필요하면 raw dict를 별도로 보관할 수 있도록
    ``raw``로 함께 반환한다).
    """
    inflow = analysis.get("cumulativeNetInflowList") or {}
    ref_date_str = inflow.get("referenceDate")
    ref_date = None
    if ref_date_str:
        try:
            ref_date = dt.datetime.strptime(ref_date_str, "%Y.%m.%d").date()
        except ValueError:
            ref_date = None
    return {
        "reference_date": ref_date,
        "net_inflow_1d_million": parse_won_string_to_million(inflow.get("cumulativeNetInflow1d")),
        "raw": inflow,
    }


def parse_nav_aum(analysis: dict) -> dict:
    """``nav``(단가)와 ``marketValue``(시가총액 문자열)를 정규화해서 반환.

    aum은 etfItemList의 marketSum(정수, 억원)에서 이미 백만원으로 변환해 쓰는 게
    1차 경로(collectors/etf_master.py)지만, etfAnalysis만 갖고 있을 때를 위해
    marketValue 문자열 파싱도 제공한다.
    """
    return {
        "nav": analysis.get("nav"),
        "aum_million": parse_won_string_to_million(analysis.get("marketValue")),
    }


_DERIVATIVE_2X_RE = re.compile(r"2X|두배|2배", re.IGNORECASE)


def classify_derivative(name: str | None) -> int | None:
    """ETF 이름에서 파생형(레버리지/인버스) 여부와 **노출 배수**(부호 포함)를 분류한다
    (PLAN.md §4.5/§6 4.5-1). 순수 함수 — 네트워크/DB 접근 없음.

    반환값은 "이 ETF에 순유입된 1원이 실제로 몇 원어치의 방향성 노출을 만드는가"다:
    레버리지=+2, 인버스(1X)=-1, 인버스2X=-2, 비파생(일반 시총식/업종테마/채권 등)=None
    (게이지 계산에서 제외).

    ## 분류 규칙 (2026-07-19, 실제 적재된 stocks.is_etf=true 300개 이름 전수 확인)

    1. 이름에 "인버스"가 있으면: "2X"/"두배"/"2배"가 함께 있으면 -2, 아니면 -1.
    2. (1)이 아니고 이름에 "레버리지"가 있으면: **+2 고정**(아래 근거).
    3. 그 외에는 None(비파생 — 일반 지수/업종/채권/혼합형 등).

    검사 순서가 중요하다 — "인버스"를 먼저 검사해야 하는데, 실데이터 300개 중
    "레버리지"와 "인버스"가 한 이름에 동시에 등장하는 사례는 없었지만(상호 배타),
    향후 이름 규칙이 바뀌어도 인버스가 우선하도록 방어적으로 순서를 고정한다.

    ### "레버리지"는 왜 매수만 확인하고 바로 +2로 고정하는가

    실측 300개 중 "레버리지"가 들어간 이름 25개를 전수 확인한 결과 **"레버리지1X"나
    "레버리지"에 배수를 명시하지 않은 채 1배로 파는 상품이 하나도 없었다** — 국내
    시장 관행상 "레버리지"라는 이름 자체가 이미 "2배"를 뜻하는 고유명사처럼 쓰인다
    (KODEX 레버리지/TIGER 레버리지 등 최초 상품부터 전부 2배이고, 단일종목 레버리지
    ETF도 금융당국 규정상 배수 상한이 2배다). "2X"가 이름에 없다고 1배로 오분류하면
    KODEX 레버리지(122630) 같은 대표 상품이 전부 잘못 집계되므로, "레버리지"는
    명시적 배수 표기가 없어도 +2로 고정한다(인버스와 비대칭 취급 — 인버스는 실제로
    "인버스"(1X)와 "인버스2X" 두 계열이 공존하므로 명시 배수를 따른다).

    ### 실데이터 분류 결과 요약 (2026-07-19, stocks.is_etf=true 300개 전수, 스크립트로 검증)

    - 파생형(멀티플라이어 not None): 46개 — 레버리지 계열 35개(전부 +2, 단일종목
      레버리지 다수 포함), 인버스 계열 11개(1X 6개=-1, 2X 5개=-2)
    - 비파생: 254개
    - **애매 케이스 1건, 의도적으로 None**: "KODEX 200롱코스닥150숏선물"(360140) —
      코스피200 롱 + 코스닥150 숏을 동시에 보유하는 **시장중립(페어) 전략** 상품이다.
      단일 지수에 대한 방향성 베팅이 아니라 두 지수 간 스프레드에 베팅하는 상품이라
      "레버리지"도 "인버스"도 이 게이지가 측정하려는 "개인의 간접 선물 포지션"과
      의미가 다르다 — 이름에 "레버리지"/"인버스"가 전혀 없어 규칙 (1)(2)에 걸리지
      않고 자연히 None으로 떨어지므로 별도 분기 코드는 필요 없지만, 우연이 아니라
      의도된 결과임을 여기 기록해 둔다.
    - "TR"(Total Return, 예: "마이티 200TR")은 배당재투자 지수 추종일 뿐 레버리지/
      인버스가 아니다 — "2X"라는 문자열이 이름에 없어(단순 오검색 우려로 확인만
      해봤다) 애초에 규칙에 걸리지 않는다.
    - "합성"(스왑 기반 복제, 예: "RISE 2차전지TOP10인버스(합성)")은 복제 방식일 뿐
      방향성과 무관해 규칙에 영향 없음(합성이어도 "인버스"만 있으면 -1).
    - "선물"이라는 단어 자체는 분류에 쓰지 않는다 — 단일종목 레버리지/인버스도
      실제로는 선물로 구현되곤 하지만("1Q SK하이닉스선물단일종목레버리지" 등) 이름에
      "레버리지"/"인버스"가 함께 있어 규칙 (1)(2)로 이미 잡힌다. "선물"만 있고
      "레버리지"/"인버스"가 없는 유일한 관측 사례가 위 페어 전략 1건이었고, 이는
      의도대로 None 처리된다.
    """
    if not name:
        return None
    if "인버스" in name:
        return -2 if _DERIVATIVE_2X_RE.search(name) else -1
    if "레버리지" in name:
        return 2
    return None
