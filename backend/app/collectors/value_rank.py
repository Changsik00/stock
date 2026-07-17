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
4. **stocks 마스터 upsert (2026-07-18 추가, 종목명 미해석 버그 수정)**:
   naver_value_rank.fetch_all()이 이미 market당 전 종목(코스피 ~2,478개/코스닥
   ~1,821개)을 이름 포함으로 완주해 주므로, value_rank(상위 TOP_N)와 별개로
   **그 market의 전량**을 ``stocks``에 upsert한다(``_upsert_stock_master``) —
   routers/flow_rank.py의 flow-path 핸들러가 이름을 찾는 1순위 테이블이
   stocks인데, 지금까지는 collectors/etf_master.py가 적재하는 ETF ~300개만
   있어서 나머지 종목은 code가 그대로 노출됐다. 이 upsert로 이름 해석이
   전면 해결되고, 향후 종목 검색(Phase 2-2, `GET /api/stocks/search`)의
   데이터 기반도 겸한다.

   **is_etf는 이 함수의 책임이 아니다** — ETF 분류(is_etf=True 태깅)는
   collectors/etf_master.py가 전담한다(단일 책임 원칙, 그 모듈이 이미
   etfItemList 기반으로 정확히 태깅함). 그래서 `_upsert_stock_master`는:
   신규 insert는 항상 ``is_etf=False``로 넣고, 이미 존재하는 행은 ON
   CONFLICT의 SET 절에 ``is_etf``를 아예 포함하지 않아 Postgres가 기존 값을
   그대로 보존한다(등록 순서와 무관하게 등록된 True가 이 upsert로 절대
   덮이지 않는다). name/market은 이 소스가 매 실행 최신값을 주므로 갱신한다
   (market은 'KOSPI'/'KOSDAQ' — etf_master.py가 몰라서 넣어둔 placeholder
   'ETF'보다 이 값이 더 정확하다).

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
from ..models import Stock, ValueRank
from .base import REGISTRY

logger = logging.getLogger(__name__)

MARKETS = ("kospi", "kosdaq")

# stocks.market에 쓰는 표기(models.py 주석 "KOSPI/KOSDAQ" 그대로) — naver_value_rank의
# 소문자 market 키와는 별도 네임스페이스.
MARKET_LABEL = {"kospi": "KOSPI", "kosdaq": "KOSDAQ"}

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


async def _upsert_stock_master(session: AsyncSession, market: str, rows: list[dict]) -> int:
    """market의 전 종목(rows, TOP_N 슬라이스 전 — 모듈 docstring 4번 항목 참고)을
    stocks 마스터에 upsert한다. is_etf는 여기서 판단/변경하지 않는다(위 docstring
    참고 — collectors/etf_master.py 전담, 기존 값 보존)."""
    market_label = MARKET_LABEL[market]
    count = 0
    for row in rows:
        code = row.get("code")
        if not code:
            continue
        stmt = pg_insert(Stock).values(
            code=code,
            name=row.get("name") or code,
            market=market_label,
            is_etf=False,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Stock.code],
            set_={"name": stmt.excluded.name, "market": stmt.excluded.market},
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
    stock_master_total = 0
    dates_seen: set[dt.date] = set()
    counts_by_market: dict[str, int] = {}

    for market in MARKETS:
        result = await asyncio.to_thread(_fetch_all_blocking, market)
        rows = result["rows"]
        date = result.get("date") or target_date
        dates_seen.add(date)
        counts_by_market[market] = len(rows)

        total += await _upsert_market_rows(session, date, market, rows, etf_codes)
        # 전량(rows, TOP_N 슬라이스 전)을 stocks 마스터에 upsert — 모듈 docstring
        # 4번 항목 참고. value_rank 행 수(total)와는 별개로 세서 기존
        # collect_log.rows 의미(=value_rank 적재 행 수)를 바꾸지 않는다.
        stock_master_total += await _upsert_stock_master(session, market, rows)

    dates_str = ", ".join(sorted(d.isoformat() for d in dates_seen))
    market_summary = ", ".join(f"{m}={n}종목 순회" for m, n in counts_by_market.items())
    if dates_seen and target_date not in dates_seen:
        message = (
            f"소스가 날짜 쿼리를 지원하지 않아 실제 적재된 날짜: {dates_str} "
            f"(요청한 target_date={target_date.isoformat()}는 무시됨). {market_summary}"
        )
    else:
        message = f"적재된 날짜: {dates_str}. {market_summary}"
    message += f". stocks 마스터 upsert {stock_master_total}건"

    return total, message


REGISTRY["value_rank"] = collect_value_rank
