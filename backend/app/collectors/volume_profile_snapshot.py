"""거래량 프로파일 일별 스냅샷 적재 — PLAN.md §5.35-3.

`collectors/watchlist_sync.py`(§5.35-1)가 등록한 `watchlist` 종목 + 지수 3종
(코스피/코스닥/선물)에 대해 매일 `quant/volume_profile.py`의
`compute_volume_profile`/`detect_levels`(§5.34 — 거래량 프로파일 근사 +
지지/저항 후보, **예측/매매 신호 아님**, 그 모듈 docstring 참고)를 돌려
`volume_profile_daily`에 upsert한다. §5.34는 요청마다 다시 계산할 뿐 아무것도
남기지 않는 완전 온디맨드였다 — 이 잡이 그 결과를 매일 쌓아 "POC가 어느
방향으로 이동 중인지" 같은 추이 조회를 가능하게 한다(사용자 요청, PLAN.md
§5.35 설계 절 참고). 이 잡 자체는 새로운 계산·판정을 추가하지 않는다 —
§5.34가 이미 계산해 온디맨드 응답에 붙이던 것과 **완전히 동일한 함수 호출**을
그대로 재사용해 결과만 저장한다.

**모듈 파일명과 REGISTRY 잡 이름이 다른 이유**: 이 파일이
`quant/volume_profile.py`와 이름이 겹치는 걸 피하려고 파일명은
`volume_profile_snapshot.py`로 하되, `REGISTRY` 키(잡 이름)는
`"volume_profile_daily"`로 등록한다(테이블 이름과 맞춰 admin 수동 트리거
`/api/admin/collect/volume_profile_daily`나 `collect_log` 조회 시 더 직관적).
이 코드베이스의 다른 모든 수집기는 파일명=잡 이름이 우연히 일치했을 뿐,
`base.py`/`__init__.py`의 REGISTRY 계약 자체는 파일명과 잡 이름이 같아야
한다는 제약을 두지 않는다(`collectors/base.py` 확인 — `REGISTRY[job_name] =
collect_fn`은 그냥 dict 대입이다).

**입력 읽기 경로(지수/종목 공용, PLAN.md §5.35-3)**:
- 지수 3종은 `services.py::get_market_series_from_db(session, market,
  PROFILE_LOOKBACK_DAYS)` — `routers/markets.py::MARKETS`와 동일한 표기
  (kospi/kosdaq/futures)를 그대로 쓴다(`collectors/ohlcv.py` 내부의
  k200_futures/kospi200 표기가 아니다 — 그 변환은 이미
  `get_market_series_from_db` 안에서 끝나 있다).
- watchlist 종목은 `services.py::get_stock_series_from_db(session, code,
  PROFILE_LOOKBACK_DAYS)` — `routers/stocks.py::stock_series`가 캔들 응답에
  쓰는 것과 동일한 함수(PLAN.md §5.35-2 리팩터로 `services.py`에 이전됨).

**자가치유(별도 로직 불필요, 확인 완료)**: 위 두 읽기 함수는 애초에
"`date == target_date`인 행이 있는지" 같은 정확 일치 조건으로 조회하지
않는다 — 그냥 `ORDER BY date DESC LIMIT days`로 **존재하는 가장 최근
`days`개**를 가져온다(`services.py` 두 함수 구현 참고). 따라서 이 배치가
파이프라인 순서를 벗어나 `collectors/ohlcv.py`/
`collectors/stock_ohlcv_watchlist.py`보다 먼저 실행되거나, 그날 아직 그
소스들이 못 채워 넣은 상태라도, 이 잡은 그 시점까지 실제로 존재하는 가장
최근 캔들들로 그대로 계산한다 — 크래시하지도, target_date 데이터가 없다고
빈 결과를 강제하지도 않는다. (다만 "표본이 아예 없거나 극히 적음"은 여전히
정직하게 `poc_price=None`/`levels=[]`로 나타난다 — 아래 "표본 부족" 절.)

**표본 부족 시에도 행을 적재한다(스킵하지 않음)**: `compute_volume_profile`이
유효한 봉을 하나도 못 찾거나(watchlist에 막 추가돼 `stock_ohlcv`가 아직
비어 있는 등), `detect_levels`가 `MIN_BARS_FOR_LEVELS`(20) 미달로 빈
리스트를 반환해도 그 결과 그대로(`poc_price=None`, `levels=[]`,
`bar_count`는 실제 유효 봉 수) `volume_profile_daily`에 upsert한다. 행 자체를
건너뛰면 나중에 추이를 조회할 때 "이 날짜엔 계산을 안 한 것"과 "계산했더니
표본 부족이었던 것"을 구분할 방법이 없어진다 — `bar_count`가 그 판단 근거를
남긴다(models.py:VolumeProfileDaily 참고).

**종목/지수별 실패 격리**: 한 종목·지수의 계산/적재 실패(예상 밖의 DB 오류 등)가
나머지를 막지 않도록 개별 try/except + SAVEPOINT로 감싼다
(`collectors/short_selling_market.py`의 시장별 격리, `stock_ohlcv_watchlist.py`의
종목별 격리와 동일한 방어적 설계 원칙).

collect_fn 계약(collectors/base.py): session에 upsert만 수행하고 commit/rollback은
run_job이 전담한다. 반환값은 이번 실행에서 upsert된 행 수 총합(지수 3 + watchlist
종목 수 이하 — 실패한 항목은 제외).
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import VolumeProfileDaily
from ..quant.volume_profile import compute_volume_profile, detect_levels
from ..services import get_market_series_from_db, get_stock_series_from_db
from .base import REGISTRY
from .watchlist_sync import list_watchlist_codes

logger = logging.getLogger(__name__)

# routers/markets.py::MARKETS와 동일 — 지수 3종(코스피/코스닥/선물)만 스냅샷한다.
INDEX_ENTITIES = ("kospi", "kosdaq", "futures")

# 조회 창(거래일) — Phase 5.34 실데이터 검증에 쓰인 값과 동일하게 맞춘다
# (routers/stocks.py::stock_series, routers/markets.py::market_series의 기본
# days=180과 통일). 이 값 자체를 volume_profile_daily.lookback_days에 그대로
# 기록해 나중에 스냅샷 간 조회 창이 달라졌을 수 있다는 맥락을 남긴다.
PROFILE_LOOKBACK_DAYS = 180


async def _upsert_profile_row(
    session: AsyncSession,
    entity_type: str,
    entity_code: str,
    date: dt.date,
    profile: dict,
    lookback_days: int,
) -> None:
    """profile(compute_volume_profile 반환값)을 한 행으로 upsert한다. 표본
    부족이면 poc_price=None/levels=[]인 채로도 그대로 적재한다(모듈 docstring
    "표본 부족 시에도 행을 적재한다" 참고)."""
    levels = detect_levels(profile)
    poc = profile.get("poc")
    poc_price = poc["price_mid"] if poc is not None else None

    stmt = pg_insert(VolumeProfileDaily).values(
        entity_type=entity_type,
        entity_code=entity_code,
        date=date,
        poc_price=poc_price,
        levels=levels,
        bar_count=profile.get("bar_count", 0),
        total_volume=profile.get("total_volume"),
        lookback_days=lookback_days,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            VolumeProfileDaily.entity_type,
            VolumeProfileDaily.entity_code,
            VolumeProfileDaily.date,
        ],
        set_={
            "poc_price": stmt.excluded.poc_price,
            "levels": stmt.excluded.levels,
            "bar_count": stmt.excluded.bar_count,
            "total_volume": stmt.excluded.total_volume,
            "lookback_days": stmt.excluded.lookback_days,
        },
    )
    await session.execute(stmt)


async def _snapshot_index(session: AsyncSession, market: str, target_date: dt.date) -> None:
    series = await get_market_series_from_db(session, market, PROFILE_LOOKBACK_DAYS)
    profile = compute_volume_profile(series)
    async with session.begin_nested():
        await _upsert_profile_row(
            session, "index", market, target_date, profile, PROFILE_LOOKBACK_DAYS
        )


async def _snapshot_stock(session: AsyncSession, code: str, target_date: dt.date) -> None:
    series = await get_stock_series_from_db(session, code, PROFILE_LOOKBACK_DAYS)
    profile = compute_volume_profile(series)
    async with session.begin_nested():
        await _upsert_profile_row(
            session, "stock", code, target_date, profile, PROFILE_LOOKBACK_DAYS
        )


async def collect(session: AsyncSession, target_date: dt.date) -> int:
    """지수 3종 + watchlist 전 종목의 거래량 프로파일을 계산해 적재.

    모듈 docstring "종목/지수별 실패 격리" 참고 — 하나의 실패가 나머지를
    막지 않는다."""
    rows_written = 0

    for market in INDEX_ENTITIES:
        try:
            await _snapshot_index(session, market, target_date)
            rows_written += 1
        except Exception as e:  # noqa: BLE001 - 지수 하나 실패가 나머지를 막지 않도록
            logger.warning(
                "volume_profile_daily: index=%s 계산/적재 실패, 건너뜀: %s", market, e
            )

    codes = await list_watchlist_codes(session)

    failed = 0
    for code in codes:
        try:
            await _snapshot_stock(session, code, target_date)
            rows_written += 1
        except Exception as e:  # noqa: BLE001 - 종목 하나 실패가 나머지를 막지 않도록
            failed += 1
            logger.warning(
                "volume_profile_daily: stock=%s 계산/적재 실패, 건너뜀: %s", code, e
            )

    logger.info(
        "volume_profile_daily: 지수 %d종 + watchlist %d개 종목 중 %d행 upsert, "
        "%d개 종목 실패 (%s)",
        len(INDEX_ENTITIES),
        len(codes),
        rows_written,
        failed,
        target_date.isoformat(),
    )
    return rows_written


REGISTRY["volume_profile_daily"] = collect
