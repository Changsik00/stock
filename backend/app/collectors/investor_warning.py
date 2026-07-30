"""투자주의/투자경고/투자위험종목 지정 이력 수집 → investor_warning_event upsert.

PLAN.md §5.39-2. REGISTRY["investor_warning"]로 등록된다. 소스는 KRX 공시채널
KIND(``clients/kind_investor_warning.py`` 모듈 docstring "실측 경과" 절 참고,
로그인 불필요) — requests(블로킹)라 ``asyncio.to_thread``로 감싼다
(collectors/short_selling_market.py와 동일한 패턴).

**tier별 lookback이 다르다(의도적)**: 투자경고/투자위험은 "지정 -> 유지 ->
해제"의 기간을 갖는 상태라 실제로 몇 주 안에 해제/격상되는 걸 실측으로
확인했다(클라이언트 모듈 docstring "조회기간 스코프 판단" 절 — 2026-07-30
기준 활성 지정 중 가장 오래된 것도 지정일이 2주 이내). 그래서
``_LOOKBACK_DAYS_WARNING_RISK``(90일)면 "현재 지정 중인 것"을 놓칠 일이
사실상 없다. 반면 투자주의는 "그날 하루"짜리 통보라 과거 이력이 필요 없고
``_LOOKBACK_DAYS_CAUTION``(10일, 주말/공휴일 갭 대비 여유)이면 충분하다 —
1년 치를 다 받으면 4,699건(실측)이나 되는데 그중 최신 하루 말고는 이
프로젝트가 쓸 데가 없다.

**종목 코드 매핑**: KIND 응답은 회사명 문자열만 준다(내부 법인코드는 이
프로젝트의 6자리 종목코드와 무관, 클라이언트 모듈 docstring 참고). 이
수집기가 매 실행마다 ``stocks.name``과 정확히 일치하는 종목을 찾아 code를
채운다 — 우선주도 이름 자체가 구분되므로("SK증권" vs "SK증권우") 별도
접미사 추론이 필요 없다(실측: 투자경고 이력에 "SK증권우"가 실제로 등장,
stocks 마스터에도 동일 이름으로 존재함을 확인). 일치하는 종목이 없으면
code를 NULL로 남기고 raw_name은 그대로 보존한다(조용히 버리지 않음).

collect_fn 계약(collectors/base.py): session에 upsert만 수행하고 commit/
rollback은 run_job이 전담한다.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import kind_investor_warning as kiw
from ..models import InvestorWarningEvent, Stock
from .base import REGISTRY

logger = logging.getLogger(__name__)

_LOOKBACK_DAYS_WARNING_RISK = 90
_LOOKBACK_DAYS_CAUTION = 10

_TIER_LOOKBACK_DAYS = {
    kiw.TIER_CAUTION: _LOOKBACK_DAYS_CAUTION,
    kiw.TIER_WARNING: _LOOKBACK_DAYS_WARNING_RISK,
    kiw.TIER_RISK: _LOOKBACK_DAYS_WARNING_RISK,
}


async def _resolve_codes(session: AsyncSession, names: set[str]) -> dict[str, str]:
    if not names:
        return {}
    stmt = select(Stock.name, Stock.code).where(Stock.name.in_(names))
    rows = (await session.execute(stmt)).all()
    return {name: code for name, code in rows}


async def _upsert_rows(session: AsyncSession, rows: list[dict]) -> int:
    names = {r["raw_name"] for r in rows}
    code_by_name = await _resolve_codes(session, names)

    count = 0
    for row in rows:
        if row["designated_date"] is None:
            # 지정일 파싱 실패(소스 스키마 변화 등) — 이 행은 natural key를
            # 만들 수 없어 건너뛴다(개별 행 격리, 나머지 행 적재는 계속).
            logger.warning("investor_warning: designated_date 없는 행 건너뜀: %r", row)
            continue

        stmt = pg_insert(InvestorWarningEvent).values(
            tier=row["tier"],
            raw_name=row["raw_name"],
            designated_date=row["designated_date"],
            code=code_by_name.get(row["raw_name"]),
            market=row["market"],
            warning_type=row["warning_type"],
            notice_date=row["notice_date"],
            released_date=row["released_date"],
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                InvestorWarningEvent.tier,
                InvestorWarningEvent.raw_name,
                InvestorWarningEvent.designated_date,
            ],
            set_={
                "code": stmt.excluded.code,
                "market": stmt.excluded.market,
                "warning_type": stmt.excluded.warning_type,
                "notice_date": stmt.excluded.notice_date,
                # 해제일은 최초 지정 시 "-"(None)이었다가 나중 재수집 때 실제
                # 날짜로 채워지는 게 정상 흐름이라 매번 덮어써야 최신 상태를
                # 반영한다(값이 사라지는 방향의 갱신은 소스가 그 자체로 그렇게
                # 줄 때만 — 소스가 여전히 "-"면 여전히 진행 중이라는 뜻이므로
                # 문제 없음).
                "released_date": stmt.excluded.released_date,
            },
        )
        await session.execute(stmt)
        count += 1
    return count


async def collect(session: AsyncSession, target_date: dt.date) -> int:
    """3개 tier(caution/warning/risk) 각각의 lookback 구간을 조회해 upsert.
    tier 하나의 조회 실패가 다른 tier 적재를 막지 않도록 개별 try/except로
    감싼다(collectors/short_selling_market.py의 시장별 격리와 동일한 취지)."""
    rows_written = 0

    for tier, lookback_days in _TIER_LOOKBACK_DAYS.items():
        start = target_date - dt.timedelta(days=lookback_days)
        try:
            rows = await asyncio.to_thread(kiw.fetch_designations, tier, start, target_date)
        except Exception as e:  # noqa: BLE001 - requests/파싱 예외 전부 개별 격리
            logger.warning("investor_warning: tier=%s 수집 실패: %s", tier, e)
            continue

        rows_written += await _upsert_rows(session, rows)

    return rows_written


REGISTRY["investor_warning"] = collect
