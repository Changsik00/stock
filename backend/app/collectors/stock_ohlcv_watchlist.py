"""watchlist 종목 일봉 배치 갱신 — PLAN.md §5.35-2.

**목적**: `collectors/watchlist_sync.py`(§5.35-1)가 매일 채워 가는 `watchlist`
전 종목에 대해 `stock_ohlcv`를 배치로 최신화한다. 지금까지 개별 종목 캔들은
`routers/stocks.py::stock_series`가 사용자가 실제로 그 종목 상세를 열어봤을
때만 온디맨드로 채우는 구조였다 — watchlist에 새로 오른 종목이라도 아무도
직접 조회하지 않으면 `stock_ohlcv`가 영영 비어 있어 다음 단계
(`collectors/volume_profile_snapshot.py`)가 계산할 캔들이 없다. 이 잡이 그
공백을 매일 밤 채운다.

**중복 구현 금지(PLAN.md §5.35-2 명시 지시)**: "신규 종목 전체 백필 vs 기존
종목 증분 top-up" 크기 산정과 실제 upsert는 `services.py::
plan_stock_ohlcv_fetch_start`/`upsert_stock_ohlcv_rows`를 그대로 재사용한다 —
이 두 함수는 원래 `routers/stocks.py`의 사설(private) 헬퍼였다가 이 배치
잡과 공유하기 위해 `services.py`로 이전됐다(그쪽 모듈 docstring "종목
캔들(stock_ohlcv) 공용 헬퍼" 절 참고). 온디맨드 전용 관심사(60초 쿨다운,
NXT 장중 판정)는 가져오지 않는다 — 이 잡은 스케줄러 프로세스에서 하루 1회만
돌아 쿨다운이 무의미하고, "장중"이라는 개념도 이 배치엔 없다(18:00 KST,
정규장+NXT 모두 마감 이후 실행이 기본 전제).

**신규 vs 기존 판단**: `stock_ohlcv`에 그 코드의 행이 하나도 없으면(watchlist에
막 추가된 종목) `BACKFILL_DAYS`(180거래일 목표 — Phase 5.34 온디맨드 기본값
`routers/stocks.py::stock_series`의 `days=180`과 동일하게 맞춘다, 그 값으로
§5.34 실데이터 검증도 이미 마쳤다)만큼 전체 백필한다.
`quant/volume_profile.py`의 `MIN_BARS_FOR_LEVELS=20`을 몇 주가 아니라 즉시
만족시키기 위함이다. 이미 행이 있으면(다른 배치 실행이나 사용자의 온디맨드
조회로 이미 캐시된 종목) 마지막 저장 날짜부터 `target_date`까지만 증분
top-up한다(저렴).

**종목별 실패 격리(필수)**: 상장폐지·코드 변경·네이버 소스 일시 장애 등으로
한 종목의 조회/적재가 실패해도 나머지 수백 종목의 배치를 막아서는 안 된다 —
`collectors/short_selling_market.py`의 시장별 try/except 격리와 동일한 패턴을
종목 단위로 적용한다. 추가로 각 종목의 DB 작업을 SAVEPOINT
(`session.begin_nested()`)로 감싼다 — `watchlist.code`는 이미 `stocks.code`
FK를 통과한 값들이라 FK 위반 가능성 자체는 낮지만("watchlist_sync.py"의
FK 안전성 절과 대비되는 지점), 소스가 주는 값의 타입/범위 이상 등 예상 밖의
DB 오류가 나더라도 그 종목의 변경만 롤백되고 이미 처리한 다른 종목들의
upsert는 세션 트랜잭션에 그대로 남게 하기 위한 동일한 방어 원칙이다.

**네이버 소스 부담 완화**: `naver_index.fetch_stock_series`는 공식 rate limit이
문서화돼 있지 않은 비공식 스크레이핑성 엔드포인트다(키움 클라이언트의
토큰버킷 rate limiter와는 다른 종류의 소스라 그 리미터를 재사용하지 않는다).
이 잡은 watchlist 전 종목(많으면 수백 개)을 순차 호출하므로, **실제로 외부
호출이 발생했을 때만** 종목 사이에 `REQUEST_DELAY_SECONDS`만큼 쉰다(이미
최신이라 건너뛴 종목까지 지연시키면 배치 시간만 늘어나고 얻는 게 없다).

collect_fn 계약(collectors/base.py): session에 upsert만 수행하고 commit/rollback은
run_job이 전담한다. 반환값은 이번 실행에서 실제로 upsert된 stock_ohlcv 행 수
총합(이미 전부 최신이면 0 — 정상, 에러 아님).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import naver_index
from ..models import StockOhlcv
from ..services import plan_stock_ohlcv_fetch_start, upsert_stock_ohlcv_rows
from .base import REGISTRY
from .watchlist_sync import list_watchlist_codes

logger = logging.getLogger(__name__)

# 신규 종목(캐시 無) 전체 백필 목표(거래일 수) — 모듈 docstring "신규 vs 기존 판단"
# 참고. services.plan_stock_ohlcv_fetch_start가 이 값을 달력일로 환산한다.
BACKFILL_DAYS = 180

# 실제 외부 호출이 있었던 종목 사이의 지연(초) — 모듈 docstring "네이버 소스 부담
# 완화" 참고. collectors/etf_master.py의 REQUEST_DELAY_SECONDS(0.4, 종목당 상세
# API 1회)보다 짧게 잡는다 — 이 잡은 배치당 최대 수백 콜이라 너무 길면 전체 배치
# 시간이 과도해지고, 캔들 조회는 etf 상세보다 훨씬 가벼운 요청이라 부담도 적다.
REQUEST_DELAY_SECONDS = 0.2


async def _sync_one_code(
    session: AsyncSession, code: str, target_date: dt.date
) -> tuple[int, bool]:
    """code 하나의 stock_ohlcv를 target_date까지 채운다.

    Returns: ``(upsert된 행 수, 외부 호출을 실제로 시도했는지)`` — 후자는
    호출측이 REQUEST_DELAY_SECONDS를 줄 대상인지 판단하는 데 쓴다(이미 최신인
    종목은 호출 자체가 없으므로 쉴 필요가 없다).
    """
    existing_max = (
        await session.execute(select(func.max(StockOhlcv.date)).where(StockOhlcv.code == code))
    ).scalar_one_or_none()

    if existing_max is not None and existing_max >= target_date:
        return 0, False  # 이미 이 배치 기준일까지 캐시됨(예: 사용자가 오늘 이미 열어봄)

    fetch_start = plan_stock_ohlcv_fetch_start(existing_max, target_date, BACKFILL_DAYS)
    rows = await asyncio.to_thread(naver_index.fetch_stock_series, code, fetch_start, target_date)

    async with session.begin_nested():
        rows_written = await upsert_stock_ohlcv_rows(session, code, rows)
    return rows_written, True


async def collect(session: AsyncSession, target_date: dt.date) -> int:
    """watchlist 전 종목의 stock_ohlcv를 target_date까지 배치로 갱신 (모듈
    docstring "종목별 실패 격리" 참고 — 한 종목의 예외가 나머지를 막지 않는다)."""
    codes = await list_watchlist_codes(session)
    total_rows = 0
    failed = 0

    for code in codes:
        try:
            rows_written, fetched = await _sync_one_code(session, code, target_date)
        except Exception as e:  # noqa: BLE001 - naver_index/파싱/DB 예외 전부 개별 격리
            failed += 1
            logger.warning("stock_ohlcv_watchlist: code=%s 갱신 실패, 건너뜀: %s", code, e)
            continue

        total_rows += rows_written
        if fetched:
            await asyncio.sleep(REQUEST_DELAY_SECONDS)

    logger.info(
        "stock_ohlcv_watchlist: watchlist %d개 종목 중 %d행 upsert, %d개 실패 (%s)",
        len(codes),
        total_rows,
        failed,
        target_date.isoformat(),
    )
    return total_rows


REGISTRY["stock_ohlcv_watchlist"] = collect
