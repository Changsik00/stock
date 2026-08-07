"""배치 잡: 개인 매도 / 외국인·기관 전환 매집 관찰 스크리너.

사용자가 유한양행(000100) 실 데이터에서 발견한 패턴이 계기다 — 개인이 계속
순매도하는데 외국인/기관이 순매수로 전환·가속하면서 가격이 급등 없이 조용히
우상향하는 종목("개인은 모르고 파는데 프로그램/외국인·기관이 미리 걸린 매도
물량을 받아먹는" 패턴)을 찾아 1~2% 스윙을 위한 "관찰용 스크리너"(매매 신호
아님, 자동매매 아님 — 후보 목록 API일 뿐)에 쓸 데이터를 매일 쌓는다. 판정
자체는 이 모듈이 새로 만들지 않고 ``quant/screener.py::evaluate_accumulation_
pattern``(순수 함수, DB/네트워크 무관)에 그대로 위임한다 — 이 모듈은 "누구를,
언제 순회하고, 그 판정에 필요한 5개 값을 어디서 읽어올지"만 정한다.

## 유니버스와 호출 패턴 (``collectors/live_refresh.py::_run_stock_flow_scan`` 참고)

대상은 ``stocks`` 테이블(``Stock`` 모델)의 ``is_etf=False`` 전 종목이다 — 매일
``value_rank`` 잡이 이미 이 테이블을 전체 시장 리스팅으로 채워두므로 여기서 새로
크롤링하지 않는다. ``_run_stock_flow_scan``과 동일한 이유로 **``KiwoomClient``
인스턴스를 스윕 전체에서 딱 하나만 연다** — 이 클라이언트의 rate limiter
(``clients/kiwoom.py``의 ``_bucket``)가 인스턴스 속성이라, 종목마다 새
인스턴스를 열면 토큰 버킷이 매번 리셋돼 사실상 무제한 버스트가 되어버린다(1req/s
제한이 무력화됨). 종목 하나 조회/파싱/판정이 실패해도(일시적 네트워크 오류,
데이터 부족 등) 나머지 스윕을 막지 않는다 — per-code try/except로 부분 실패를
허용하는 것도 동일한 이유(``_run_stock_flow_scan`` 모듈 docstring 참고).

**``_run_stock_flow_scan``과의 차이**: 그 잡은 value-rank 후보군(거래대금 상위
최대 200개)만 순회하는 10분 주기 라이브 캐시 워밍이지만, 이 잡은 **전 종목**
(코스피+코스닥, ETF 제외 — 실측 기준 수천 종목)을 순회하는 일 1회 배치라 훨씬
오래 걸린다(예상 70~90분, 키움 1req/s 제한 기준). 그래서 전용 20:00 KST 크론
(``collectors/scheduler.py``)에서만 돌고, 기존 18:00 본배치(REGISTRY 전체 순회)에는
넣지 않는다 — 넣으면 그 배치 전체가 70~90분 밀린다.

## 종목별 처리: upsert 직후 바로 패턴 판정

종목마다 ka10059(``client.stock_investor_daily``)를 1콜 호출해
``routers.stocks._parse_ka10059_rows``로 파싱하고
``routers.stocks._upsert_flow_rows``로 ``stock_flow``에 upsert한다(이미 존재하고
검증된 두 함수를 그대로 재사용 — 재구현하지 않는다). 그 직후, 방금 갱신된
``stock_flow``에서 최근 ``LOOKBACK_TRADING_DAYS``(10)거래일치 "개인"/
("외국인"+"기관계") net_value를 읽고, ``services.get_stock_series_from_db``로
같은 기간 가격 시계열(일별 ``changeRate``)을 읽어 5개 값(개인 10일 순매수,
외국인+기관 최근/직전 5일 순매수, 10일 누적 등락률, 10일 중 최대 일간 등락폭)을
계산해 ``evaluate_accumulation_pattern``에 넘긴다. 10거래일치가 안 차거나 값이
결측이면(신규 상장 등) 에러로 취급하지 않고 조용히 스킵한다.

``matched=True``면 ``AccumulationPick``에 upsert한다 — ``date``는 오늘 날짜가
아니라 **그 판정이 근거로 삼은 stock_flow의 가장 최근 거래일**이다(소스가 아직
그날 수급을 반영하지 못했으면 그 이전 거래일이 될 수 있다 — flow_rank 잡이
"소스가 실제로 반환한 날짜를 그대로 적재"하는 것과 같은 이유, PLAN.md §5.46
``collectors/flow_rank.py`` 참고).

## collect_fn 계약 (``collectors/base.py``)

시그니처는 ``async def collect_accumulation_screener(session, target_date) ->
tuple[int, str | None]`` — rows는 매칭돼 upsert된 종목 수, message는 스캔한 전체
종목 수 요약이다. **이 함수 안에서 session.commit()을 호출하지 않는다** —
``run_job``이 트랜잭션을 소유한다(재시도 시 깨끗하게 rollback하기 위함,
``collectors/base.py`` 모듈 docstring 참고). 코드 하나 실패는 여기서 흡수하지만,
스윕 전체를 막는 예외(예: 키움 인증 실패로 ``KiwoomClient()`` 자체가 안 열림)는
그대로 전파해 ``run_job``의 재시도(최대 3회, 지수 백오프)·``collect_log`` 실패
기록에 맡긴다 — 이 함수가 임의로 삼키지 않는다.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .. import services
from ..models import AccumulationPick, Stock, StockFlow
from ..quant.screener import (
    LOOKBACK_TRADING_DAYS,
    RECENT_HALF_DAYS,
    evaluate_accumulation_pattern,
)
from .base import REGISTRY, CollectResult

logger = logging.getLogger(__name__)

# 진행 상황 로그 주기 — 전 종목(수천 개) 스윕이라 중간 관찰이 필요하다(작업
# 지시 §3.4 "500종목마다 logger.info").
PROGRESS_LOG_INTERVAL = 500

# 패턴 판정에 쓰는 투자자 라벨 — routers/stocks.py의 KA10059_FIELD_TO_INVESTOR와
# 동일한 한국어 문자열("개인"/"외국인"/"기관계")이어야 stock_flow 조회가 맞는다.
_INVESTOR_INDIVIDUAL = "개인"
_INVESTOR_FOREIGN = "외국인"
_INVESTOR_INSTITUTION = "기관계"
_REQUIRED_INVESTORS = (_INVESTOR_INDIVIDUAL, _INVESTOR_FOREIGN, _INVESTOR_INSTITUTION)


async def _load_recent_flow_window(
    session: AsyncSession, code: str, lookback_days: int
) -> list[dict] | None:
    """code의 stock_flow에서 가장 최근 `lookback_days` 거래일치 "개인"/
    ("외국인"+"기관계") net_value를 최신순(index 0 = 가장 최근)으로 반환한다.

    거래일 수가 `lookback_days`에 못 미치거나, 그 안의 어느 날짜라도 필요한 3개
    투자자 라벨 중 하나라도 값이 없으면(신규 상장 직후 등) None — "에러"가 아니라
    "판정에 쓸 데이터가 아직 부족함"이라는 뜻이며 호출부는 조용히 스킵해야 한다.
    """
    stmt = (
        select(StockFlow.date, StockFlow.investor, StockFlow.net_value)
        .where(StockFlow.code == code, StockFlow.investor.in_(_REQUIRED_INVESTORS))
        .order_by(StockFlow.date.desc())
    )
    rows = (await session.execute(stmt)).all()

    by_date: dict[dt.date, dict[str, int | None]] = {}
    for date_, investor, net_value in rows:
        by_date.setdefault(date_, {})[investor] = net_value

    dates_desc = sorted(by_date.keys(), reverse=True)[:lookback_days]
    if len(dates_desc) < lookback_days:
        return None

    out: list[dict] = []
    for d in dates_desc:
        vals = by_date[d]
        individual = vals.get(_INVESTOR_INDIVIDUAL)
        foreign = vals.get(_INVESTOR_FOREIGN)
        inst = vals.get(_INVESTOR_INSTITUTION)
        if individual is None or foreign is None or inst is None:
            return None
        out.append({"date": d, "individual": individual, "foreign_inst": foreign + inst})
    return out


def _cumulative_price_stats(price_rows: list[dict]) -> tuple[float, float] | None:
    """get_stock_series_from_db가 반환한 (오름차순, 각 행에 그날의 changeRate가
    이미 계산돼 있는) 행들로 10거래일 누적 등락률과 그 구간 중 최대 일간
    등락폭(절대값)을 계산한다. changeRate가 하나라도 없으면(None) None."""
    change_rates: list[float] = []
    for row in price_rows:
        rate = row.get("changeRate")
        if rate is None:
            return None
        change_rates.append(float(rate))

    cumulative = 1.0
    for rate in change_rates:
        cumulative *= 1 + rate / 100
    price_return_10d_pct = round((cumulative - 1) * 100, 4)
    max_abs_daily_return_10d_pct = round(max(abs(r) for r in change_rates), 4)
    return price_return_10d_pct, max_abs_daily_return_10d_pct


async def _load_stock_universe(session: AsyncSession) -> list[tuple[str, str, str | None]]:
    """대상 유니버스 — ``stocks``(Stock 모델)의 ``is_etf=False`` 전 종목
    (code, name, market). 별도 함수로 뺀 이유: 단위테스트가 이 조회만
    몽키패치해 실제 dev Postgres의 stocks 테이블(수천 행, value_rank 잡이
    매일 채움)을 건드리지 않고 소수의 테스트 종목만으로 스윕 로직을 검증할 수
    있게 하기 위함이다(``routers.flow_rank._warm_value_rank_live``를
    몽키패치하는 ``_run_stock_flow_scan`` 테스트와 동일한 격리 패턴,
    ``tests/test_stock_flow_scan.py`` 참고)."""
    stmt = select(Stock.code, Stock.name, Stock.market).where(Stock.is_etf.is_(False))
    return [(row.code, row.name, row.market) for row in (await session.execute(stmt)).all()]


async def collect_accumulation_screener(session: AsyncSession, target_date: dt.date) -> CollectResult:
    # 지연 임포트 — routers.stocks/clients.kiwoom는 이 잡이 실행될 때만
    # 필요하고, 다른 collectors 모듈들의 관례(예: live_refresh.py의 함수
    # 내부 임포트)와 동일하게 순환 임포트/기동 순서 문제를 피한다.
    from ..clients.kiwoom import KiwoomClient
    from ..routers import stocks as stocks_router

    stocks = await _load_stock_universe(session)
    if not stocks:
        return 0, "stocks 마스터가 비어 있어 건너뜀"

    total = len(stocks)
    scanned = 0
    matched = 0

    async with KiwoomClient() as client:
        for code, name, market in stocks:
            scanned += 1
            try:
                data, _headers = await client.stock_investor_daily(code)
                rows = stocks_router._parse_ka10059_rows(data)
                await stocks_router._upsert_flow_rows(session, code, rows)

                flow_window = await _load_recent_flow_window(session, code, LOOKBACK_TRADING_DAYS)
                if flow_window is None:
                    continue

                price_rows = await services.get_stock_series_from_db(
                    session, code, LOOKBACK_TRADING_DAYS
                )
                if len(price_rows) < LOOKBACK_TRADING_DAYS:
                    continue
                price_stats = _cumulative_price_stats(price_rows)
                if price_stats is None:
                    continue
                price_return_10d_pct, max_abs_daily_return_10d_pct = price_stats

                individual_net_10d = sum(r["individual"] for r in flow_window)
                recent5 = flow_window[:RECENT_HALF_DAYS]
                prior5 = flow_window[RECENT_HALF_DAYS:LOOKBACK_TRADING_DAYS]
                foreign_inst_net_recent5d = sum(r["foreign_inst"] for r in recent5)
                foreign_inst_net_prior5d = sum(r["foreign_inst"] for r in prior5)

                result = evaluate_accumulation_pattern(
                    individual_net_10d=float(individual_net_10d),
                    foreign_inst_net_recent5d=float(foreign_inst_net_recent5d),
                    foreign_inst_net_prior5d=float(foreign_inst_net_prior5d),
                    price_return_10d_pct=price_return_10d_pct,
                    max_abs_daily_return_10d_pct=max_abs_daily_return_10d_pct,
                )

                if result["matched"]:
                    pick_date = flow_window[0]["date"]  # 판정 근거인 최신 거래일
                    pick_stmt = pg_insert(AccumulationPick).values(
                        date=pick_date,
                        code=code,
                        name=name,
                        market=market,
                        individual_net_10d=individual_net_10d,
                        foreign_inst_net_recent5d=foreign_inst_net_recent5d,
                        foreign_inst_net_prior5d=foreign_inst_net_prior5d,
                        price_return_10d_pct=price_return_10d_pct,
                        max_abs_daily_return_10d_pct=max_abs_daily_return_10d_pct,
                        reason=result["reason"],
                    )
                    pick_stmt = pick_stmt.on_conflict_do_update(
                        index_elements=[AccumulationPick.date, AccumulationPick.code],
                        set_={
                            "name": pick_stmt.excluded.name,
                            "market": pick_stmt.excluded.market,
                            "individual_net_10d": pick_stmt.excluded.individual_net_10d,
                            "foreign_inst_net_recent5d": pick_stmt.excluded.foreign_inst_net_recent5d,
                            "foreign_inst_net_prior5d": pick_stmt.excluded.foreign_inst_net_prior5d,
                            "price_return_10d_pct": pick_stmt.excluded.price_return_10d_pct,
                            "max_abs_daily_return_10d_pct": pick_stmt.excluded.max_abs_daily_return_10d_pct,
                            "reason": pick_stmt.excluded.reason,
                        },
                    )
                    await session.execute(pick_stmt)
                    matched += 1
            except Exception as e:  # noqa: BLE001 - 종목 하나 실패가 나머지 스윕을 막지 않도록
                logger.warning("accumulation-screener: %s(%s) 처리 실패: %s", code, name, e)

            if scanned % PROGRESS_LOG_INTERVAL == 0:
                logger.info(
                    "accumulation-screener: %d/%d 종목 처리, 현재까지 %d 매칭",
                    scanned,
                    total,
                    matched,
                )

    message = f"{scanned}/{total} 종목 스캔, {matched}건 매칭"
    logger.info("accumulation-screener: 완료 — %s", message)
    return matched, message


REGISTRY["accumulation_screener"] = collect_accumulation_screener
