"""watchlist 재활용 — 오늘의 "활동성 상위" 종목을 누적 등록 (PLAN.md §5.35-1).

**배경**: §5.34(quant/volume_profile.py)는 요청마다 온디맨드로 계산할 뿐 아무것도
남기지 않았다("단발성 확인" — 사용자 지적). 추이 분석을 하려면 매일 스냅샷을
쌓아야 하는데, 전 종목(4천여 개)을 매일 스냅샷하는 건 불가능하다(개별 종목
`stock_ohlcv`는 사용자가 열어본 종목만 온디맨드로 캐시되는 구조,
`routers/stocks.py::_ensure_candles_cached`). 이 프로젝트엔 시가총액/지수
편입비중 데이터가 없어 진짜 "지수 가중치" 기준 유니버스도 만들 수 없다.

**대안(사용자 확인 완료)**: 이미 매일 배치로 쌓이는 "오늘 활동성 상위" 3개
테이블 — 수급 상위 `value_rank`, 거래대금 상위 `flow_rank`, ETF 경유 상위
`flow_path` — 의 코드 합집합을 근사 유니버스로 쓴다. 대형주가 반복적으로
상위에 뜨는 경향 때문에 실용적으로는 나쁘지 않은 근사다.

**`watchlist` 테이블 재활용**: Phase 2-3 설계 당시("일별 수집 대상 종목") 만들어
졌지만 실제로는 어디에도 연결되지 않은 채 0행으로 방치돼 있던 테이블
(`models.py:Watchlist`)을 되살려 쓴다. 이 잡은 그 위에 **신규 등록만** 한다 —
`ON CONFLICT (code) DO NOTHING`으로 이미 있는 코드의 `added_at`(최초 등장
시각)을 절대 덮어쓰지 않고, 삭제도 하지 않는다("한 번 오르면 계속 유지" —
시간이 지날수록 "자주 활동하는 종목군"으로 자연 수렴하는 설계, PLAN.md §5.35
설계 절 참고).

**날짜 self-healing**: 세 소스 테이블 각각에 대해 독립적으로 `target_date` 행이
있으면 그걸 쓰고, 없으면(예: 이 잡이 파이프라인 순서를 벗어나 단독/수동 실행돼
그날 배치가 아직 안 끝났거나, 애초에 그 테이블에 그날 데이터가 없는 경우) 그
테이블에서 가장 최근 존재하는 날짜로 자동 대체한다 — 이 코드베이스 전반의
self-healing 관례(예: `collectors/flow_path.py::_nearest_date`, `market_flow.py`의
ka10051 폴백)와 같은 취지. 한 테이블이 완전히 비어 있으면(배치를 한 번도
안 돌린 개발 초기 등) 그 테이블 몫은 그냥 빈 집합으로 취급하고 나머지로 진행한다
(전체를 실패시키지 않는다).

**FK 안전성**: `watchlist.code`는 `stocks.code`에 대한 FK가 걸려 있다
(`models.py`). 반면 `value_rank.code`/`flow_rank.code`/`flow_path.code`는 **FK가
걸려 있지 않다**(models.py 확인 — 세 테이블 모두 code를 순수 문자열로만
저장) — 즉 이 세 테이블에서 얻은 코드가 어쩌다 아직 `stocks` 마스터에 없는
경우(EOD 마스터 배치가 못 따라잡은 신규/이형 코드, `routers/stocks.py` §5.28
사례와 동일한 부류) `watchlist` INSERT가 FK 위반으로 실패할 수 있다. 우선
합집합 전체를 한 번에 upsert 시도(공통 케이스, 저렴)하고, 실패하면
SAVEPOINT(`session.begin_nested()`)로 감싸 코드 하나씩 재시도해 문제 코드
하나만 건너뛴다(`collectors/short_selling_market.py`의 시장별 격리와 같은
취지 — 배치 전체가 막히면 안 된다).

collect_fn 계약(collectors/base.py): session에 upsert만 수행하고 commit/rollback은
run_job이 전담한다. 반환값은 **새로 삽입된** 행 수 — 이미 다 등록돼 있어 0을
반환하는 날도 정상(에러 아님, 오히려 유니버스가 안정됐다는 뜻).
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import FlowPath, FlowRank, ValueRank, Watchlist
from .base import REGISTRY

logger = logging.getLogger(__name__)

# (라벨, date 컬럼, code 컬럼) — PLAN.md §5.35-1이 지정한 세 소스 테이블.
_SOURCE_TABLES: tuple[tuple[str, ColumnElement, ColumnElement], ...] = (
    ("value_rank", ValueRank.date, ValueRank.code),
    ("flow_rank", FlowRank.date, FlowRank.code),
    ("flow_path", FlowPath.date, FlowPath.code),
)


async def _codes_for_date_or_latest(
    session: AsyncSession,
    label: str,
    date_col: ColumnElement,
    code_col: ColumnElement,
    target_date: dt.date,
) -> set[str]:
    """date_col == target_date인 code들. 없으면 그 테이블의 가장 최근 날짜로
    자동 대체(모듈 docstring "날짜 self-healing" 참고). 테이블이 통째로 비어
    있으면 빈 집합."""
    stmt = select(code_col).where(date_col == target_date).distinct()
    codes = set((await session.execute(stmt)).scalars().all())
    if codes:
        return codes

    latest = (await session.execute(select(func.max(date_col)))).scalar_one_or_none()
    if latest is None:
        logger.info("watchlist_sync: %s 테이블이 비어 있음 — 건너뜀", label)
        return set()

    if latest != target_date:
        logger.info(
            "watchlist_sync: %s에 %s 데이터 없음 — 최근 날짜 %s로 대체",
            label,
            target_date.isoformat(),
            latest.isoformat(),
        )
    stmt = select(code_col).where(date_col == latest).distinct()
    return set((await session.execute(stmt)).scalars().all())


async def _upsert_watchlist_codes(session: AsyncSession, codes: set[str]) -> int:
    """codes를 watchlist에 `ON CONFLICT (code) DO NOTHING`으로 upsert하고
    **새로 삽입된** 행 수를 반환한다(이미 있던 코드는 세지 않음 — 모듈 docstring
    "watchlist 테이블 재활용" 참고, added_at을 절대 덮어쓰지 않는다).

    모듈 docstring "FK 안전성" 절 참고: 우선 전체를 한 번에 시도하고, FK 위반
    등으로 실패하면 코드별 SAVEPOINT 재시도로 좁힌다."""
    if not codes:
        return 0

    sorted_codes = sorted(codes)

    try:
        async with session.begin_nested():
            stmt = (
                pg_insert(Watchlist)
                .values([{"code": c} for c in sorted_codes])
                .on_conflict_do_nothing(index_elements=[Watchlist.code])
                .returning(Watchlist.code)
            )
            result = await session.execute(stmt)
            return len(result.scalars().all())
    except IntegrityError as e:
        logger.warning(
            "watchlist_sync: 일괄 upsert 실패(FK 위반 가능성 — stocks 마스터에 "
            "아직 없는 코드 포함) — 코드별로 재시도: %s",
            e,
        )

    inserted = 0
    for code in sorted_codes:
        try:
            async with session.begin_nested():
                stmt = (
                    pg_insert(Watchlist)
                    .values(code=code)
                    .on_conflict_do_nothing(index_elements=[Watchlist.code])
                    .returning(Watchlist.code)
                )
                result = await session.execute(stmt)
                if result.scalars().first() is not None:
                    inserted += 1
        except IntegrityError as e:  # noqa: BLE001 - 코드 하나만 격리, 나머지는 계속
            logger.warning(
                "watchlist_sync: code=%s 등록 실패(stocks 마스터에 없는 코드로 추정), "
                "건너뜀: %s",
                code,
                e,
            )
    return inserted


async def list_watchlist_codes(session: AsyncSession) -> list[str]:
    """watchlist 전 종목 코드(코드순 정렬) — `collectors/stock_ohlcv_watchlist.py`/
    `collectors/volume_profile_snapshot.py`가 공유하는 유일한 조회 경로다(두
    모듈 모두 이 잡 다음 순서로 실행되며 watchlist를 읽기만 한다). 한 곳으로
    모아 두면 테스트에서도 이 함수 하나만 monkeypatch해 "watchlist에 이 코드들이
    있다고 치자"를 통제할 수 있다."""
    stmt = select(Watchlist.code).order_by(Watchlist.code)
    return list((await session.execute(stmt)).scalars().all())


async def collect(session: AsyncSession, target_date: dt.date) -> int:
    """value_rank/flow_rank/flow_path 코드 합집합을 watchlist에 신규 등록."""
    codes: set[str] = set()
    for label, date_col, code_col in _SOURCE_TABLES:
        codes |= await _codes_for_date_or_latest(session, label, date_col, code_col, target_date)

    inserted = await _upsert_watchlist_codes(session, codes)
    logger.info(
        "watchlist_sync: 합집합 %d개 코드 중 %d개 신규 등록 (%s)",
        len(codes),
        inserted,
        target_date.isoformat(),
    )
    return inserted


REGISTRY["watchlist_sync"] = collect
