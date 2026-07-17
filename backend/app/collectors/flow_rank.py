"""투자자별(외인/기관) 순매수·순매도 상위 종목 스냅샷 수집 -> flow_rank upsert
(PLAN.md §4.5/§6 3.5-2b).

소스: clients/naver_rank.py(``sise_deal_rank_iframe.naver``). 실호출로 확인된 핵심
제약(naver_rank.py 모듈 docstring 참고) 때문에 이 수집기는 아래 처리를 배치의
일반 패턴(collectors/macro.py, collectors/ohlcv.py의 LOOKBACK_DAYS)과 다르게
한다:

1. **날짜 파라미터 없음** — 소스가 항상 "최근 2거래일" 고정 응답만 준다. 그래서
   ``target_date``를 소스 쿼리에 쓰지 않는다(쿼리할 방법 자체가 없음). 대신 소스가
   실제로 반환한 날짜(들)를 그대로 ``flow_rank.date``에 적재한다 — target_date와 다를
   수 있다(예: 주말/공휴일에 수동 트리거하면 마지막 거래일이 온다). 이 결과 이 잡은
   ``run_job``에 어떤 target_date를 넘겨도 동일하게 동작한다(idempotent).
2. **시장(코스피/코스닥) 구분은 rank 공간을 나누지 않고 별도 컬럼으로만 보존한다**
   — models.py의 FlowRank PK는 여전히 (date, investor, side, rank)뿐이다(수정
   금지 대상). PLAN.md §4.5 작업 지시가 "investor에 시장을 합성하지 말고
   (foreign_kospi 등 금지), 시장별로 rank 공간을 나누지도 말 것"이라고 명시했고
   "상위 N 안에서 시장 섞임 허용"을 대안으로 제시했다 — 그래서 이 수집기는
   **코스피 top20 + 코스닥 top20을 하나로 합쳐 |net_value| 내림차순으로
   재정렬**해 investor(foreign/institution) x side(buy/sell) 하나의 rank
   1..N(최대 40)으로 적재한다. 즉 "외국인 순매수 상위"는 코스피·코스닥 통합
   기준이다. 다만 PLAN.md §4.6 3.6-1(시황 대시보드, market 배지 요구)에서
   FlowRank에 nullable ``market`` 컬럼(PK 아님)이 추가됐다 — 병합 직전에 각
   row가 어느 시장 페이지(``MARKETS`` 루프)에서 왔는지 태깅해 두고, 병합·재정렬
   후에도 그 값을 그대로 저장한다(_upsert_rank_rows의 ``market=row.get("market")``).
   2026-07-18 이전 적재분은 이 태깅 없이 저장돼 market이 NULL로 남는다
   (models.py FlowRank docstring 참고).
3. **side/부호 정규화 (§6 3.5-2b 결정)** — naver_rank.fetch_deal_rank가 type="sell"
   에 대해 반환하는 net_value/quantity는 소스 그대로 음수다. 이 수집기는 그 값을
   ``abs()``로 정규화해 **항상 양수(크기) + side='buy'|'sell' 컬럼**으로 저장한다.
   이유: 부호와 방향 컬럼을 동시에 두면(sell인데 양수, 혹은 반대) 어느 쪽이 진실인지
   헷갈리고 UI 색상 규칙(순매수=빨강/순매도=파랑, side 기준)과도 어긋난다. 랭킹 정렬은
   |net_value| 기준(side별로 이미 양수/음수가 갈려 있으므로 buy는 net_value 자체,
   sell은 절대값 — 정규화 이전 raw 값 기준으로 정렬한 뒤 저장 시점에 abs()한다).
4. **회전율(turnover, %) = 당일 거래대금 ÷ 시가총액 × 100** — 수집 시점에 계산해
   저장한다(§6 3.5-2b "API 조회 시 계산 vs 수집 시 저장" 결정: 수집 시 저장 채택 —
   과거 스냅샷을 그대로 재현할 수 있고, 조회 시마다 종목당 API를 다시 부르지 않아도
   되기 때문. 근거는 PLAN.md 작업 로그 참고). 소스가 둘로 갈린다:
     - **ETF**: clients/naver_etf.fetch_etf_list()가 이미 벌크로
       amount_million(거래대금)·aum_million(시가총액)을 주므로 종목당 추가 호출 없이
       계산한다.
     - **개별주**: 랭킹에 오른 종목마다 clients/naver_rank.fetch_stock_market_value()로
       개별 조회한다(네이버가 벌크 API를 제공하지 않음). 하루 랭킹에 오른 종목은
       중복 제거하면 ~100개 내외(§6 3.5-2b 작업 지시 추정과 일치)이므로 종목당 1회,
       0.5초 간격으로만 호출한다(전체 배치에 buy/sell/foreign/institution 8번의
       deal-rank 호출 + ETF 목록 1회 + 개별주 최대 ~100회 = 대략 110회, 앞서 이미
       존재하던 배치 실행 시간과 같은 자릿수).

is_etf 태깅은 stocks.is_etf에 의존하지 않는다(다른 배치가 동시에 stocks를 적재
중이라 PLAN.md 지시에 따라 의존 금지) — naver_etf.fetch_etf_list()로 독립적으로
조회한 전체 ETF 목록과 대조한다(예전에는 naver_rank.fetch_etf_codes()를 따로
불렀지만 같은 엔드포인트를 두 번 때리는 셈이라 회전율 계산에 필요한 fetch_etf_list
하나로 통합했다 — naver_rank.fetch_etf_codes 자체는 계속 존재/테스트됨, 이 수집기가
안 쓸 뿐).

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

from ..clients import naver_etf, naver_rank
from ..models import FlowRank
from .base import REGISTRY

logger = logging.getLogger(__name__)

MARKETS = ("kospi", "kosdaq")
INVESTORS = ("foreign", "institution")
SIDES = ("buy", "sell")

# 네이버 요청 간 0.5초 간격 (PLAN.md 지시 — 서버 부담/차단 방지). deal-rank 호출뿐
# 아니라 개별주 회전율 조회(fetch_stock_market_value)에도 동일하게 적용한다.
NAVER_REQUEST_DELAY_SECONDS = 0.5


def _fetch_deal_rank_blocking(market: str, investor: str, side: str) -> list[dict]:
    time.sleep(NAVER_REQUEST_DELAY_SECONDS)
    return naver_rank.fetch_deal_rank(market, investor, type_=side)


def _fetch_etf_list_blocking() -> list[dict]:
    time.sleep(NAVER_REQUEST_DELAY_SECONDS)
    return naver_etf.fetch_etf_list()


def _fetch_stock_market_value_blocking(code: str) -> dict:
    time.sleep(NAVER_REQUEST_DELAY_SECONDS)
    return naver_rank.fetch_stock_market_value(code)


def _etf_turnover_map(etf_items: list[dict]) -> dict[str, float]:
    """ETF의 거래대금/시가총액을 이미 벌크로 갖고 있으니(fetch_etf_list) 종목당
    API 호출 없이 회전율(%)을 계산한다. 둘 중 하나라도 없거나 시가총액이 0이면
    스킵(turnover는 그 코드에 대해 그냥 없는 채로 남는다 — NULL)."""
    turnover: dict[str, float] = {}
    for item in etf_items:
        code = item.get("code")
        amount = item.get("amount_million")
        aum = item.get("aum_million")
        if not code or amount is None or not aum:
            continue
        turnover[code] = round(amount / aum * 100, 4)
    return turnover


async def _fetch_stock_turnover_map(codes: set[str]) -> dict[str, float]:
    """개별주(ETF 아님) 코드 집합에 대해 종목당 1회씩 회전율을 조회한다. 실패한
    종목은 조용히 건너뛴다(수백 종목 중 하나 실패로 배치 전체가 죽지 않도록) —
    warning 로그만 남긴다."""
    turnover: dict[str, float] = {}
    for code in sorted(codes):
        try:
            mv = await asyncio.to_thread(_fetch_stock_market_value_blocking, code)
        except Exception:
            logger.warning("flow_rank: 종목 %s 회전율 조회 실패", code, exc_info=True)
            continue
        amount = mv.get("accumulated_trading_value_million")
        market_value = mv.get("market_value_million")
        if amount is not None and market_value:
            turnover[code] = round(amount / market_value * 100, 4)
    return turnover


async def _upsert_rank_rows(
    session: AsyncSession,
    date: dt.date,
    investor: str,
    side: str,
    rows: list[dict],
    etf_codes: set[str],
    turnover_map: dict[str, float],
) -> int:
    count = 0
    for i, row in enumerate(rows, start=1):
        code = row["code"]
        net_value = row.get("net_value")
        quantity = row.get("quantity")
        stmt = pg_insert(FlowRank).values(
            date=date,
            investor=investor,
            side=side,
            rank=i,
            code=code,
            name=row["name"],
            # §모듈독스트링 3번: 소스 부호(sell=음수)를 여기서 양수 크기로 정규화한다.
            net_value=None if net_value is None else abs(net_value),
            quantity=None if quantity is None else abs(quantity),
            turnover=turnover_map.get(code),
            is_etf=code in etf_codes,
            # §4.6 3.6-1: 어느 시장 랭킹 페이지에서 왔는지(kospi/kosdaq) — 병합
            # 전에 collect_flow_rank가 row별로 태깅해 둔 값을 그대로 저장한다.
            market=row.get("market"),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[FlowRank.date, FlowRank.investor, FlowRank.side, FlowRank.rank],
            set_={
                "code": stmt.excluded.code,
                "name": stmt.excluded.name,
                "net_value": stmt.excluded.net_value,
                "quantity": stmt.excluded.quantity,
                "turnover": stmt.excluded.turnover,
                "is_etf": stmt.excluded.is_etf,
                "market": stmt.excluded.market,
            },
        )
        await session.execute(stmt)
        count += 1
    return count


async def collect_flow_rank(session: AsyncSession, target_date: dt.date) -> tuple[int, str | None]:
    """investor(foreign/institution) x side(buy/sell) x market(kospi/kosdaq) 상위
    20종목을 코스피+코스닥 통합 후 |net_value| 내림차순으로 재정렬해 flow_rank에
    적재한다. 회전율(turnover)도 함께 계산해 저장한다(§모듈독스트링 4번).

    target_date는 소스가 날짜 쿼리를 지원하지 않아 실제로는 사용되지 않는다 — 소스가
    반환하는 실제 날짜(들)를 그대로 적재하고, 그 날짜 목록을 message로 남긴다(§ 모듈
    docstring 참고).
    """
    etf_items = await asyncio.to_thread(_fetch_etf_list_blocking)
    etf_codes = {it["code"] for it in etf_items if it.get("code")}
    etf_turnover = _etf_turnover_map(etf_items)

    # (date, investor, side) -> 병합된 rows. 먼저 전부 모아서(1) 회전율이 필요한
    # 개별주 코드를 한 번에 dedup하고 (2) 그 다음에야 upsert한다 — 같은 코드가 여러
    # 그룹(예: buy에도 sell에도, foreign에도 institution에도)에 나타나면 turnover
    # 조회를 코드당 1회로 줄이기 위함.
    per_group: dict[tuple[dt.date, str, str], list[dict]] = {}
    all_dates: set[dt.date] = set()

    for side in SIDES:
        for investor in INVESTORS:
            by_date: dict[dt.date, list[dict]] = {}
            for market in MARKETS:
                blocks = await asyncio.to_thread(
                    _fetch_deal_rank_blocking, market, investor, side
                )
                for block in blocks:
                    # §4.6 3.6-1: 병합 전에 각 row가 어느 시장 페이지에서 왔는지
                    # 태깅해 둔다 — 아래에서 코스피+코스닥을 하나로 합쳐 재정렬한
                    # 뒤에도 market을 잃지 않기 위함(FlowRank.market 컬럼).
                    for row in block["rows"]:
                        row["market"] = market
                    by_date.setdefault(block["date"], []).extend(block["rows"])

            for date, rows in by_date.items():
                rows.sort(key=lambda r: abs(r["net_value"]), reverse=True)
                per_group[(date, investor, side)] = rows
                all_dates.add(date)

    non_etf_codes = {
        row["code"]
        for rows in per_group.values()
        for row in rows
        if row["code"] not in etf_codes
    }
    stock_turnover = await _fetch_stock_turnover_map(non_etf_codes)
    turnover_map = {**stock_turnover, **etf_turnover}

    total = 0
    for (date, investor, side), rows in per_group.items():
        total += await _upsert_rank_rows(
            session, date, investor, side, rows, etf_codes, turnover_map
        )

    message = None
    if all_dates:
        dates_str = ", ".join(sorted(d.isoformat() for d in all_dates))
        if target_date not in all_dates:
            message = (
                f"소스가 날짜 쿼리를 지원하지 않아 실제 적재된 날짜: {dates_str} "
                f"(요청한 target_date={target_date.isoformat()}는 무시됨). "
                f"회전율 조회 종목 {len(non_etf_codes)}개(개별주) + {len(etf_turnover)}개(ETF)"
            )
        else:
            message = (
                f"적재된 날짜: {dates_str}. "
                f"회전율 조회 종목 {len(non_etf_codes)}개(개별주) + {len(etf_turnover)}개(ETF)"
            )
    else:
        logger.warning("flow_rank: 소스에서 날짜 블록을 하나도 받지 못함")

    return total, message


REGISTRY["flow_rank"] = collect_flow_rank
