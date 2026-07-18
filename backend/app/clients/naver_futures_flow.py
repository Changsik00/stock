"""K200 선물 투자자별(개인/외국인/기관) 일별 순매수 — m.stock.naver.com 모바일 API
(PLAN.md §4.5 4.5-2).

## 소스 실호출 경과 (2026-07-19)

시도 순서는 PLAN.md §4.5-2가 확정한 대로: ①네이버 실확정 → (실패 시) ②KRX 파생
통계 파싱. ①에서 바로 채택 가능한 소스를 찾아 ②는 시도하지 않았다.

**시도해서 버린 것들 (전부 코스피/코스닥 현물 전용, 선물 구분 없음)**:
- ``finance.naver.com/sise/sise_index.naver?code=FUT`` (PC 선물 지수 페이지) —
  차트 이미지·시세만 있고 투자자별 수급 섹션/링크가 없다(페이지 전체를 curl로
  받아 href/src를 모두 뽑아 확인, "선물"/"deriv" 관련 링크 0건).
- ``finance.naver.com/sise/investorDealTrendDay.naver?bizdate=YYYYMMDD`` — "일자별
  순매수" 표가 있지만 이건 **코스피 현물** 전용이고(kiwoom market_flow가 이미 쓰는
  것과 같은 데이터), market/sosok 파라미터로 선물을 선택하는 방법이 없다.

**채택**: ``m.stock.naver.com/api/index/FUT/trend?bizdate=YYYYMMDD`` — 모바일
지수 상세 페이지(``m.stock.naver.com/domestic/index/FUT/total``)의 "투자자별"
섹션이 호출하는 내부 API. ``m.stock.naver.com/api/index/FUT/basic``으로 먼저
"FUT"가 유효한 itemCode(코스피 200 선물)임을 확인한 뒤(응답
``"stockName":"코스피 200 선물"``), 같은 itemCode로 ``/trend``를 실호출해 발견했다.

실측 예시(2026-07-16)::

    GET https://m.stock.naver.com/api/index/FUT/trend?bizdate=20260716
    {"bizdate":"20260716","personalValue":"-3,442","foreignValue":"+7,014","institutionalValue":"-3,210"}

필드 3개뿐(개인/외국인/기관, 코스피 현물 kiwoom market_flow의 13분류보다 훨씬
단순) — 계약수(volume)는 이 API에 없고 금액만 있다. 세 값의 합이 정확히 0이 아닌
경우가 있다(위 예시: -3442+7014-3210=+362) — 코스피 현물 kiwoom market_flow에
있는 "기타법인" 같은 잔여 분류가 이 API에는 노출되지 않아서로 추정된다(이 모듈은
소스가 주는 3개 값만 그대로 저장하고 나머지를 추정해서 채우지 않는다).

**단위 확정(중요, 억원 — 백만원 아님)**: 필드명이 ``...Value``라 처음엔 계약수일
가능성도 의심했으나(선물 투자자 통계는 보통 계약수가 1차 지표), 2024-05-07 "외국인
코스피200선물 역대 최대 순매수 2조3,447억원"(다음뉴스 보도, KRX 통계 집계
1996년 이후 최대) 실제 사례로 대조 검증했다::

    GET .../trend?bizdate=20240507
    {"bizdate":"20240507","personalValue":"-8,551","foreignValue":"+23,447","institutionalValue":"-14,677"}

``foreignValue=23,447``이 보도된 "2조3,447억원"과 정확히 일치(23,447억원) —
**단위는 억원**이다. models.py MarketFlow.net_value는 백만원 단위이므로 이 모듈은
저장 직전 ×100 변환까지 마쳐서 반환한다(호출자가 다시 변환할 필요 없음).

**날짜 파라미터 실호출 검증**: ``bizdate=YYYYMMDD``를 정확히 그 날짜로 존중한다
(sise_deal_rank_iframe.naver·sise_index.naver 등 다른 네이버 페이지들과 달리 임의
과거 날짜 조회가 된다 — 2020-07-20까지 실측 확인, 그 이전은 미검증이지만 3년
백필에는 충분). 주말/공휴일/데이터 없는 날짜는 예외 없이
``{"bizdate": "<요청한 날짜>", "personalValue": "0", "foreignValue": "0",
"institutionalValue": "0"}``를 돌려준다(2026-07-18/19 토·일, 2025-01-01,
2026-01-01, 심지어 1990-01-01·2030-01-01도 동일하게 확인) — 실제로 세 값이 전부
0인 거래일과 구분이 안 되지만 그런 거래일이 현실적으로 없다고 보고(코스피 현물
breadth.py 등 이 저장소의 다른 "날짜 지원 안 함" 소스들과 동일한 타협),
``fetch_futures_flow``는 이 경우 ``None``을 반환해 "휴장/데이터 없음"으로
취급한다.
"""

from __future__ import annotations

import datetime as dt
import logging

import requests

logger = logging.getLogger(__name__)

TREND_URL = "https://m.stock.naver.com/api/index/FUT/trend"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# API 필드 -> 저장용 investor 한글 표기. market_flow(kospi/kosdaq)와 겹치는 이름은
# 그대로 맞춘다(모듈 docstring의 models.py 투자자 컨벤션 — "기관계"가 이미 표준).
FIELD_TO_INVESTOR = {
    "personalValue": "개인",
    "foreignValue": "외국인",
    "institutionalValue": "기관계",
}

_EOKWON_TO_MILLION_WON = 100  # 1억원 = 100백만원


class NaverFuturesFlowError(Exception):
    """Raised when the trend endpoint returns an unparsable response."""


def _parse_eokwon_to_million(raw: str) -> int:
    """"+7,014"/"-3,442"/"0" 형태의 억원 문자열을 백만원 정수로 변환한다."""
    text = raw.strip().replace(",", "")
    try:
        eokwon = int(text)
    except ValueError as e:
        raise NaverFuturesFlowError(f"unparsable value {raw!r}") from e
    return eokwon * _EOKWON_TO_MILLION_WON


def fetch_futures_flow(target_date: dt.date, timeout: int = 15) -> dict | None:
    """target_date의 K200 선물 투자자별(개인/외국인/기관계) 순매수를 반환한다.

    Returns ``{"date": target_date, "flows": [{"investor": "개인", "net_value": int,
    "net_volume": None}, ...]}`` — net_value 단위는 **백만원**(모듈 docstring의
    억원->백만원 ×100 변환 적용 완료), net_volume은 이 소스에 없어 항상 None.

    휴장일/데이터 없음(세 값 모두 "0")이면 ``None``을 반환한다(예외 아님) — 호출자가
    "그 날은 건너뛴다"로 처리하면 된다(collectors/market_flow.py의 "종합 행 없으면
    빈 리스트" 관례와 동일한 취지).
    """
    resp = requests.get(
        TREND_URL,
        params={"bizdate": target_date.strftime("%Y%m%d")},
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()

    missing = [f for f in FIELD_TO_INVESTOR if f not in data]
    if missing:
        raise NaverFuturesFlowError(
            f"response missing fields {missing} for bizdate={target_date}: {data!r}"
        )

    parsed = {field: _parse_eokwon_to_million(data[field]) for field in FIELD_TO_INVESTOR}

    if all(v == 0 for v in parsed.values()):
        logger.info(
            "naver_futures_flow: all-zero response for %s, treating as no data (holiday?)",
            target_date,
        )
        return None

    flows = [
        {"investor": investor, "net_value": parsed[field], "net_volume": None}
        for field, investor in FIELD_TO_INVESTOR.items()
    ]
    return {"date": target_date, "flows": flows}
