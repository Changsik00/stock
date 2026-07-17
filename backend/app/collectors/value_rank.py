"""거래대금 상위 종목("돈이 모이는 곳") 일별 스냅샷 수집 -> value_rank upsert
(PLAN.md §4.6 3.6-1).

소스: clients/naver_value_rank.py(모바일 quantTop 거래량 상위 API를 시장 전 종목
순회 + 로컬 재정렬). 실호출로 확인된 제약(naver_value_rank.py 모듈 docstring
참고) 때문에 이 수집기도 collectors/flow_rank.py와 비슷하게 "소스가 날짜 쿼리를
지원하지 않는다"는 전제로 동작한다 — target_date는 로그 메시지 비교에만 쓰이고,
실제 적재 날짜는 소스가 반환한 날짜(각 종목 localTradedAt에서 뽑음)를 그대로
쓴다.

collect_value_rank가 하는 일:

1. is_etf 태깅용 ETF 코드셋을 naver_rank.fetch_etf_codes()로 한 번 조회한다
   (naver_etf.fetch_etf_list()를 또 부르지 않고 이미 있는 헬퍼를 재사용 —
   collectors/flow_rank.py와 동일 소스, PLAN.md 지시 "etfItemList 코드셋 대조").
2. market(kospi/kosdaq) 별로 naver_value_rank.fetch_all()을 호출한다. 이 함수가
   이미 그 시장 전 종목(코스피 ~2,478개/코스닥 ~1,821개, 2026-07-18 실측)을
   완주해 거래대금(백만 원) 내림차순으로 정렬해 준다 — 그래서 이 수집기는 상위
   ``TOP_N``(100)개만 잘라 저장한다.
3. turnover(회전율, %) = value_million ÷ market_value_million × 100 — 소스
   응답에 이미 시가총액(marketValueRaw)이 딸려 와서(naver_value_rank.py 모듈
   docstring 참고) **종목별 추가 API 호출이 필요 없다**. PLAN.md가 예상한
   "개별주는 flow_rank 방식(integration API) 재사용, 호출 수 고려해 상위
   50개만"은 이 소스에서는 불필요해졌다 — 전량(상위 100개 전부)에 대해
   turnover를 채운다. ETF도 marketValueRaw가 AUM으로 채워져 있어 flow_rank처럼
   ETF/개별주를 분기할 필요가 없다(둘 다 같은 필드 하나로 계산).

REGISTRY["value_rank"]로 등록된다(collectors/flow_rank.py와 동일 패턴).
routers/admin.py에 이 모듈을 import해야 POST /api/admin/collect/value_rank로
수동 트리거할 수 있다 — 이 배선은 이번 작업 범위 밖(admin.py는 병렬로 다른
작업이 건드릴 수 있는 공유 파일이라 손대지 않음, 최종 보고 참고).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import naver_rank, naver_value_rank
from ..models import ValueRank
from .base import REGISTRY

logger = logging.getLogger(__name__)

MARKETS = ("kospi", "kosdaq")

# 시장당 20~25회 페이지 호출이 필요해(naver_value_rank.py 모듈 docstring 실측
# totalCount 기준) collectors/flow_rank.py와 동일한 정책(0.5초)으로 서버 부담을
# 줄인다.
NAVER_REQUEST_DELAY_SECONDS = 0.5

# 소스가 이미 거래대금 내림차순으로 정렬해 전 종목을 주므로, 저장은 상위 100개만
# 한다(PLAN.md "가능하면 50+" 충족 — 전량을 순회한 뒤라 50이 아니라 100까지
# 늘려도 추가 호출 비용이 없다).
TOP_N = 100


def _fetch_etf_codes_blocking() -> set[str]:
    time.sleep(NAVER_REQUEST_DELAY_SECONDS)
    return naver_rank.fetch_etf_codes()


def _fetch_all_blocking(market: str) -> dict:
    return naver_value_rank.fetch_all(market, sleep_seconds=NAVER_REQUEST_DELAY_SECONDS)


async def _upsert_market_rows(
    session: AsyncSession, date: dt.date, market: str, rows: list[dict], etf_codes: set[str]
) -> int:
    count = 0
    for i, row in enumerate(rows[:TOP_N], start=1):
        value = row.get("value_million")
        market_value = row.get("market_value_million")
        turnover = None
        if value is not None and market_value:
            turnover = round(value / market_value * 100, 4)

        stmt = pg_insert(ValueRank).values(
            date=date,
            market=market,
            rank=i,
            code=row["code"],
            name=row.get("name"),
            value=value,
            change_rate=row.get("change_rate"),
            is_etf=row["code"] in etf_codes,
            turnover=turnover,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[ValueRank.date, ValueRank.market, ValueRank.rank],
            set_={
                "code": stmt.excluded.code,
                "name": stmt.excluded.name,
                "value": stmt.excluded.value,
                "change_rate": stmt.excluded.change_rate,
                "is_etf": stmt.excluded.is_etf,
                "turnover": stmt.excluded.turnover,
            },
        )
        await session.execute(stmt)
        count += 1
    return count


async def collect_value_rank(session: AsyncSession, target_date: dt.date) -> tuple[int, str | None]:
    """market(kospi/kosdaq) 별 거래대금 상위 ``TOP_N``종목을 value_rank에 적재한다.

    target_date는 소스가 날짜 쿼리를 지원하지 않아 실제로는 사용되지 않는다 —
    소스가 반환하는 실제 날짜를 그대로 적재하고, target_date와 다르면 로그
    메시지에 남긴다(collectors/flow_rank.py와 동일 관례).
    """
    etf_codes = await asyncio.to_thread(_fetch_etf_codes_blocking)

    total = 0
    dates_seen: set[dt.date] = set()
    counts_by_market: dict[str, int] = {}

    for market in MARKETS:
        result = await asyncio.to_thread(_fetch_all_blocking, market)
        rows = result["rows"]
        date = result.get("date") or target_date
        dates_seen.add(date)
        counts_by_market[market] = len(rows)

        total += await _upsert_market_rows(session, date, market, rows, etf_codes)

    dates_str = ", ".join(sorted(d.isoformat() for d in dates_seen))
    market_summary = ", ".join(f"{m}={n}종목 순회" for m, n in counts_by_market.items())
    if dates_seen and target_date not in dates_seen:
        message = (
            f"소스가 날짜 쿼리를 지원하지 않아 실제 적재된 날짜: {dates_str} "
            f"(요청한 target_date={target_date.isoformat()}는 무시됨). {market_summary}"
        )
    else:
        message = f"적재된 날짜: {dates_str}. {market_summary}"

    return total, message


REGISTRY["value_rank"] = collect_value_rank
