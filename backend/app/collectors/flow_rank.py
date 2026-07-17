"""투자자별(외인/기관) 순매수 상위 종목 스냅샷 수집 -> flow_rank upsert (PLAN.md §4.5).

소스: clients/naver_rank.py(``sise_deal_rank_iframe.naver``). 실호출로 확인된 핵심
제약(naver_rank.py 모듈 docstring 참고) 때문에 이 수집기는 아래 두 가지를 배치의
일반 패턴(collectors/macro.py, collectors/ohlcv.py의 LOOKBACK_DAYS)과 다르게
처리한다:

1. **날짜 파라미터 없음** — 소스가 항상 "최근 2거래일" 고정 응답만 준다. 그래서
   ``target_date``를 소스 쿼리에 쓰지 않는다(쿼리할 방법 자체가 없음). 대신 소스가
   실제로 반환한 날짜(들)를 그대로 ``flow_rank.date``에 적재한다 — target_date와 다를
   수 있다(예: 주말/공휴일에 수동 트리거하면 마지막 거래일이 온다). 이 결과 이 잡은
   ``run_job``에 어떤 target_date를 넘겨도 동일하게 동작한다(idempotent).
2. **시장(코스피/코스닥) 구분을 flow_rank 스키마가 갖고 있지 않음** — models.py의
   FlowRank PK는 (date, investor, rank)뿐이라 시장 컬럼이 없다(수정 금지 대상).
   PLAN.md §4.5 작업 지시가 "investor에 시장을 합성하지 말고(foreign_kospi 등 금지),
   시장별로 rank 공간을 나누지도 말 것"이라고 명시했고 "상위 N 안에서 시장 섞임
   허용"을 대안으로 제시했다 — 그래서 이 수집기는 **코스피 top20 + 코스닥 top20을
   하나로 합쳐 net_value 내림차순으로 재정렬**해 investor(foreign/institution) 하나의
   rank 1..N(최대 40)으로 적재한다. 즉 "외국인 순매수 상위"는 코스피·코스닥 통합
   기준이며, 어느 시장 종목인지는 이 테이블만으로는 알 수 없다(필요해지면 stocks.market
   조인으로 나중에 복원 가능 — 이 배치는 그 테이블에 의존하지 않는다).

is_etf 태깅은 stocks.is_etf에 의존하지 않는다(다른 배치가 동시에 stocks를 적재
중이라 PLAN.md 지시에 따라 의존 금지) — naver_rank.fetch_etf_codes()로 독립적으로
조회한 itemcode 집합과 대조한다.

REGISTRY["flow_rank"]로 등록된다 (collectors/macro.py와 동일한 패턴 — routers/admin.py가
이 모듈을 import해야 실제로 실행 가능해진다; admin.py 배선은 이 작업 범위 밖).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import naver_rank
from ..models import FlowRank
from .base import REGISTRY

logger = logging.getLogger(__name__)

MARKETS = ("kospi", "kosdaq")
INVESTORS = ("foreign", "institution")

# 네이버 요청 간 0.5초 간격 (PLAN.md 지시 — 서버 부담/차단 방지).
NAVER_REQUEST_DELAY_SECONDS = 0.5


def _fetch_deal_rank_blocking(market: str, investor: str) -> list[dict]:
    time.sleep(NAVER_REQUEST_DELAY_SECONDS)
    return naver_rank.fetch_deal_rank(market, investor)


def _fetch_etf_codes_blocking() -> set[str]:
    time.sleep(NAVER_REQUEST_DELAY_SECONDS)
    return naver_rank.fetch_etf_codes()


async def _upsert_rank_rows(
    session: AsyncSession, date: dt.date, investor: str, rows: list[dict], etf_codes: set[str]
) -> int:
    count = 0
    for i, row in enumerate(rows, start=1):
        stmt = pg_insert(FlowRank).values(
            date=date,
            investor=investor,
            rank=i,
            code=row["code"],
            name=row["name"],
            net_value=row["net_value"],
            is_etf=row["code"] in etf_codes,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[FlowRank.date, FlowRank.investor, FlowRank.rank],
            set_={
                "code": stmt.excluded.code,
                "name": stmt.excluded.name,
                "net_value": stmt.excluded.net_value,
                "is_etf": stmt.excluded.is_etf,
            },
        )
        await session.execute(stmt)
        count += 1
    return count


async def collect_flow_rank(session: AsyncSession, target_date: dt.date) -> tuple[int, str | None]:
    """investor(foreign/institution) x market(kospi/kosdaq) 순매수 상위 20종목을
    코스피+코스닥 통합 후 net_value 내림차순으로 재정렬해 flow_rank에 적재한다.

    target_date는 소스가 날짜 쿼리를 지원하지 않아 실제로는 사용되지 않는다 — 소스가
    반환하는 실제 날짜(들)를 그대로 적재하고, 그 날짜 목록을 message로 남긴다(§ 모듈
    docstring 참고).
    """
    etf_codes = await asyncio.to_thread(_fetch_etf_codes_blocking)

    total = 0
    all_dates: set[dt.date] = set()
    for investor in INVESTORS:
        by_date: dict[dt.date, list[dict]] = {}
        for market in MARKETS:
            blocks = await asyncio.to_thread(_fetch_deal_rank_blocking, market, investor)
            for block in blocks:
                by_date.setdefault(block["date"], []).extend(block["rows"])

        for date, rows in by_date.items():
            rows.sort(key=lambda r: r["net_value"], reverse=True)
            total += await _upsert_rank_rows(session, date, investor, rows, etf_codes)
            all_dates.add(date)

    message = None
    if all_dates:
        dates_str = ", ".join(sorted(d.isoformat() for d in all_dates))
        if target_date not in all_dates:
            message = (
                f"소스가 날짜 쿼리를 지원하지 않아 실제 적재된 날짜: {dates_str} "
                f"(요청한 target_date={target_date.isoformat()}는 무시됨)"
            )
        else:
            message = f"적재된 날짜: {dates_str}"
    else:
        logger.warning("flow_rank: 소스에서 날짜 블록을 하나도 받지 못함")

    return total, message


REGISTRY["flow_rank"] = collect_flow_rank
