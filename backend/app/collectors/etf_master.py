"""ETF 마스터·구성종목·순유입 수집 (PLAN.md §4.5/§6 Phase 3.5-1).

네이버 ``etfItemList``(목록) + ``etfAnalysis``(상세, 종목당 1회 호출)로:

- ``stocks``      — 대상 ETF를 ``is_etf=True``로 upsert (market은 알 수 없어 'ETF')
- ``etf_holdings``— top10 구성종목 당일 스냅샷 (기존 (etf_code, date) 행을 지우고
                     다시 넣는다 — top10 멤버 수가 재실행 때마다 달라질 수 있어
                     upsert만으로는 빠진 종목이 stale로 남는 문제를 막는다)
- ``etf_stats``   — nav/aum/net_inflow. net_inflow는 etfAnalysis의
                     ``cumulativeNetInflowList.cumulativeNetInflow1d`` 값을
                     그대로 쓴다(clients/naver_etf.py 모듈독스트링 참고 — 이미
                     "그 날 하루치" 값이라 diff가 필요 없다). 이 값이 참조하는
                     실제 날짜(``referenceDate``)를 행의 date로 쓴다 — target_date와
                     다를 수 있어(휴장일 등) target_date를 그대로 쓰면 어긋난다.

대상 선정: etfTabCode in (1, 2, 3, 7)(국내 시총식/업종테마/국내파생/혼합) 전체에서
거래대금(amonut) 상위 top_n개(``naver_etf.select_domestic_equity_targets``, 기본
300개 — PLAN.md §4.5). 이름 기반 제외는 하지 않는다 — 단일종목 레버리지 등도
실물 주식을 보유하므로 포함하고, 주식을 안 갖는 인버스/선물형은 top10 파싱에서
주식코드가 없어 etf_holdings에 행이 생기지 않는 방식으로 자연 탈락한다
(clients/naver_etf.py의 유니버스 원칙 주석 참고).

collect_fn 계약(collectors/base.py)을 따른다: 이 함수는 세션을 commit/rollback하지
않는다(run_job이 트랜잭션을 소유). ``REGISTRY["etf_master"]``로 등록한다 —
routers/admin.py에서 import해서 활성화하는 건 별도 작업(동시 진행 중, 이 모듈은
건드리지 않는다).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import naver_etf
from ..models import EtfHolding, EtfStat, Stock
from .base import REGISTRY

logger = logging.getLogger(__name__)

TOP_N = 300
REQUEST_DELAY_SECONDS = 0.4


def fetch_targets_with_analysis(top_n: int = TOP_N) -> list[dict]:
    """블로킹: 목록 조회 -> 대상 선정 -> 종목별 etfAnalysis 순차 호출(요청 간 딜레이).

    Returns a list of::

        {
            "code", "name",
            "analysis": <raw etfAnalysis dict> | None,  # 실패 시 None(해당 종목만 skip)
        }

    한 종목의 etfAnalysis 호출이 실패해도 나머지 종목 수집을 막지 않는다 — 개별
    ETF 상세 페이지가 일시적으로 비정상이어도 나머지 ~299개는 정상 적재되게 하기
    위함(collectors/macro.py의 kofia 개별 실패 흡수 패턴과 동일한 취지).
    """
    items = naver_etf.fetch_etf_list()
    targets = naver_etf.select_domestic_equity_targets(items, top_n=top_n)

    out = []
    for i, t in enumerate(targets):
        if i > 0:
            time.sleep(REQUEST_DELAY_SECONDS)
        code = t["code"]
        try:
            analysis = naver_etf.fetch_etf_analysis(code)
        except Exception as e:  # noqa: BLE001 - isolate per-ETF failures
            logger.warning("etfAnalysis(%s, %s) 조회 실패: %s", code, t.get("name"), e)
            analysis = None
        out.append({"code": code, "name": t.get("name"), "list_info": t, "analysis": analysis})
    return out


async def _upsert_stock(session: AsyncSession, code: str, name: str | None) -> None:
    stmt = pg_insert(Stock).values(code=code, name=name or code, market="ETF", is_etf=True)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Stock.code],
        set_={"name": stmt.excluded.name, "is_etf": True},
    )
    await session.execute(stmt)


async def _replace_holdings(
    session: AsyncSession, etf_code: str, date: dt.date, holdings: list[dict]
) -> int:
    await session.execute(
        delete(EtfHolding).where(EtfHolding.etf_code == etf_code, EtfHolding.date == date)
    )
    count = 0
    for h in holdings:
        await session.execute(
            pg_insert(EtfHolding).values(
                etf_code=etf_code,
                date=date,
                stock_code=h["stock_code"],
                weight=h["weight"],
                shares=h.get("shares"),
            )
        )
        count += 1
    return count


async def _upsert_stat(
    session: AsyncSession,
    code: str,
    date: dt.date,
    nav: float | None,
    aum_million: int | None,
    net_inflow_million: int | None,
) -> None:
    stmt = pg_insert(EtfStat).values(
        code=code, date=date, nav=nav, aum=aum_million, net_inflow=net_inflow_million
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[EtfStat.code, EtfStat.date],
        set_={
            "nav": stmt.excluded.nav,
            "aum": stmt.excluded.aum,
            "net_inflow": stmt.excluded.net_inflow,
        },
    )
    await session.execute(stmt)


async def collect_etf_master(session: AsyncSession, target_date: dt.date) -> tuple[int, str | None]:
    """국내주식형 거래대금 상위 ETF의 마스터/구성/통계를 적재.

    Returns ``(rows, message)`` — rows는 stocks + etf_holdings + etf_stats에 쓴 행
    수의 합, message는 etfAnalysis 조회에 실패해 skip된 ETF 수(있을 때만).
    """
    results = await asyncio.to_thread(fetch_targets_with_analysis, TOP_N)

    total = 0
    failed: list[str] = []

    for r in results:
        code = r["code"]
        name = r["name"]
        analysis = r["analysis"]

        await _upsert_stock(session, code, name)
        total += 1

        if analysis is None:
            failed.append(code)
            continue

        holdings = naver_etf.parse_top10_holdings(analysis)
        total += await _replace_holdings(session, code, target_date, holdings)

        nav_aum = naver_etf.parse_nav_aum(analysis)
        inflow = naver_etf.parse_net_inflow_snapshot(analysis)
        # aum은 목록 조회(etfItemList.marketSum)가 더 안정적인 1차 소스라 우선하고,
        # etfAnalysis 파싱값은 목록에 없을 때만 폴백으로 쓴다(clients/naver_etf.py
        # 모듈독스트링 — 두 값은 KODEX 200 기준 완전히 일치함을 실측 확인했다).
        aum_million = r["list_info"].get("aum_million")
        if aum_million is None:
            aum_million = nav_aum["aum_million"]
        stat_date = inflow["reference_date"] or target_date

        await _upsert_stat(
            session,
            code,
            stat_date,
            nav_aum["nav"],
            aum_million,
            inflow["net_inflow_1d_million"],
        )
        total += 1

    message = f"etfAnalysis 조회 실패 {len(failed)}건: {', '.join(failed)}" if failed else None
    return total, message


REGISTRY["etf_master"] = collect_etf_master
