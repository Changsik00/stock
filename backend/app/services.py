"""Builds daily time series.

``get_index_series``/``get_futures_series`` below are the *original* KRX Open API
implementation — kept as-is (unused by the router since 2026-07) because
krx_client.py itself must stay per PLAN.md, and this code documents how they were
built. The KRX Open API dataset approval is currently rejected (403), so
``routers/markets.py`` no longer calls these; it reads ``index_ohlcv`` in the DB
instead via ``get_market_series_from_db`` (populated by collectors/ohlcv.py —
yfinance/네이버, see PLAN.md §5.4/§7).
"""

import logging
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .krx_client import KRXClient
from .models import IndexOhlcv, StockOhlcv

logger = logging.getLogger("krx")

# idx/{market}_dd_trd returns one row per index in the KOSPI/KOSDAQ "series"
# (코스피, 코스피 200, 코스피 100, ... / 코스닥, 코스닥 150, ...). We want the
# single headline index for each market.
INDEX_NAME = {
    "kospi": "코스피",
    "kosdaq": "코스닥",
}

# 코스피 200 선물 최근월물(가장 거래량이 큰 근월물)을 대표 선물 시세로 사용.
FUTURES_PRODUCT_NAME = "코스피 200 선물"

MAX_LOOKBACK_DAYS = 550


def _trading_days_back(n_days: int):
    """Yield up to MAX_LOOKBACK_DAYS calendar weekdays, most recent first."""
    d = date.today()
    count = 0
    while count < MAX_LOOKBACK_DAYS:
        d -= timedelta(days=1)
        if d.weekday() < 5:  # Mon-Fri; KRX holidays are simply skipped as empty days
            count += 1
            yield d


def get_index_series(client: KRXClient, market: str, days: int) -> list[dict]:
    endpoint = f"{market}_dd_trd"
    target_name = INDEX_NAME[market]
    out = []
    for d in _trading_days_back(days):
        bas_dd = d.strftime("%Y%m%d")
        rows = client.daily("idx", endpoint, bas_dd)
        row = next((r for r in rows if r.get("IDX_NM") == target_name), None)
        if row is None:
            if rows:
                logger.warning(
                    "no row named %r on %s; names seen: %s",
                    target_name,
                    bas_dd,
                    sorted({r.get("IDX_NM") for r in rows}),
                )
            continue
        out.append(
            {
                "date": bas_dd,
                "close": float(row.get("CLSPRC_IDX", 0) or 0),
                "changeRate": float(row.get("FLUC_RT", 0) or 0),
                "volume": int(float(row.get("ACC_TRDVOL", 0) or 0)),
                "value": int(float(row.get("ACC_TRDVAL", 0) or 0)),
            }
        )
        if len(out) >= days:
            break
    out.reverse()
    return out


def get_futures_series(client: KRXClient, days: int) -> list[dict]:
    out = []
    for d in _trading_days_back(days):
        bas_dd = d.strftime("%Y%m%d")
        rows = client.daily("drv", "fut_bydd_trd", bas_dd)
        candidates = [
            r
            for r in rows
            if (r.get("PROD_NM") or r.get("ISU_NM") or "").startswith(FUTURES_PRODUCT_NAME)
        ]
        if not candidates:
            continue
        # 최근월물 = 해당일 거래량이 가장 큰 종목
        row = max(candidates, key=lambda r: float(r.get("ACC_TRDVOL", 0) or 0))
        out.append(
            {
                "date": bas_dd,
                "close": float(row.get("TDD_CLSPRC", 0) or 0),
                "changeRate": float(row.get("FLUC_RT", 0) or 0),
                "volume": int(float(row.get("ACC_TRDVOL", 0) or 0)),
                "value": int(float(row.get("ACC_TRDVAL", 0) or 0)),
                "contract": row.get("ISU_NM"),
            }
        )
        if len(out) >= days:
            break
    out.reverse()
    return out


# 라우터 market 경로 파라미터(kospi/kosdaq/futures) -> index_ohlcv.market 값
# (models.py: kospi/kosdaq/k200_futures).
DB_MARKET = {"kospi": "kospi", "kosdaq": "kosdaq", "futures": "k200_futures"}


async def get_market_series_from_db(
    session: AsyncSession, market: str, days: int
) -> list[dict]:
    """market(kospi/kosdaq/futures)의 최근 `days` 거래일 지수 일봉을 DB에서 조회.

    PLAN.md §5.4 "DB 캐싱 우선" — 외부 API를 직접 호출하지 않고 collectors/ohlcv.py가
    미리 적재해 둔 index_ohlcv만 읽는다. 데이터가 없으면 빈 리스트(에러 아님).

    응답 형태는 위 get_index_series/get_futures_series(KRX 기반, 현재는 미사용)와
    동일하게 맞춘다 — 프런트가 그대로 동작하도록: date는 "YYYYMMDD" 문자열,
    changeRate는 index_ohlcv에 컬럼이 없어(KRX가 주던 FLUC_RT 대신) 하루 더 가져와
    전일 종가 대비로 계산한다. value(거래대금)는 현재 소스(yfinance/네이버)가
    제공하지 않아 항상 0이다(§7 리스크 참고).

    open/high/low는 index_ohlcv에 그대로 있어 함께 내려준다 (프런트 CandleChart용,
    2026-07-17 추가 — 기존 필드는 그대로 두는 additive 변경).
    """
    db_market = DB_MARKET.get(market)
    if db_market is None:
        raise ValueError(f"unknown market {market!r}, expected one of {sorted(DB_MARKET)}")

    # changeRate 계산용 버퍼로 하루치를 더 가져온다 — 나중에 맨 앞 한 건을 잘라낸다.
    stmt = (
        select(IndexOhlcv)
        .where(IndexOhlcv.market == db_market)
        .order_by(IndexOhlcv.date.desc())
        .limit(days + 1)
    )
    rows = list(reversed((await session.execute(stmt)).scalars().all()))

    out: list[dict] = []
    prev_close: float | None = None
    for r in rows:
        close = float(r.close) if r.close is not None else None
        change_rate = 0.0
        if prev_close is not None and close is not None:
            change_rate = (close - prev_close) / prev_close * 100
        out.append(
            {
                "date": r.date.strftime("%Y%m%d"),
                "open": float(r.open) if r.open is not None else None,
                "high": float(r.high) if r.high is not None else None,
                "low": float(r.low) if r.low is not None else None,
                "close": close,
                "changeRate": round(change_rate, 4),
                "volume": int(r.volume) if r.volume is not None else 0,
                "value": int(r.value) if r.value is not None else 0,
            }
        )
        if close is not None:
            prev_close = close

    return out[-days:] if len(out) > days else out


# -- 종목 캔들(stock_ohlcv) 공용 헬퍼 — PLAN.md §5.35-2 -----------------------
#
# 아래 세 함수는 원래 routers/stocks.py에 있던 온디맨드 전용 사설(private) 헬퍼
# (``_upsert_ohlcv_rows``/``_ensure_candles_cached``의 백필 크기 산정/
# ``_read_candles``)였다. §5.35-2가 "watchlist 전 종목을 배치로 갱신하는
# collectors/stock_ohlcv_watchlist.py"를 요구하면서, 라우터(온디맨드 요청 1건당
# 1종목)와 배치(하루 1회, 수백 종목)가 정확히 같은 "stock_ohlcv upsert" 및
# "신규 종목 전체 백필 vs 기존 종목 증분 top-up" 로직을 필요로 하게 됐다 —
# 이 파일이 이미 get_market_series_from_db(지수판 DB 읽기)를 통해 "라우터·수집기가
# 함께 쓰는 DB 헬퍼 계층" 역할을 하고 있어 그대로 이곳으로 옮긴다(collectors/*는
# routers/*를 import하지 않는다는 이 코드베이스의 레이어링 방향에 맞춤).
#
# 온디맨드 전용 관심사(쿨다운 dict, 장중 판정)는 옮기지 않고 routers/stocks.py에
# 남겨 둔다 — 배치 잡은 하루 1회만 돌아 쿨다운이 필요 없고, 스케줄러 프로세스에는
# "장중" 개념도 무의미하기 때문이다.

# 캔들 최초 백필 시 거래일 수 -> 달력일 버퍼(주말 비율 5/7에 공휴일 여유를 더함).
# routers/stocks.py(온디맨드)와 collectors/stock_ohlcv_watchlist.py(배치, PLAN.md
# §5.35-2)가 함께 쓴다.
CANDLE_CALENDAR_BUFFER_RATIO = 1.6
CANDLE_CALENDAR_BUFFER_MIN_DAYS = 10


async def upsert_stock_ohlcv_rows(session: AsyncSession, code: str, rows: list[dict]) -> int:
    """stock_ohlcv에 rows(``clients/naver_index.py::fetch_stock_series`` 출력
    형태 — ``{"date", "open", "high", "low", "close", "volume"}``, value는 그
    소스가 거래대금을 안 줘 항상 없음)를 upsert하고 upsert한 행 수를 반환한다.

    routers/stocks.py(``stock_series`` 온디맨드 경로)와
    collectors/stock_ohlcv_watchlist.py(배치 경로)가 공유하는 **유일한**
    stock_ohlcv 쓰기 경로 — PLAN.md §5.35-2 "중복 구현 금지"에 따라 이 함수
    하나로 통일한다."""
    count = 0
    for row in rows:
        stmt = pg_insert(StockOhlcv).values(
            code=code,
            date=row["date"],
            open=row.get("open"),
            high=row.get("high"),
            low=row.get("low"),
            close=row.get("close"),
            volume=row.get("volume"),
            value=row.get("value"),  # 네이버 fchart는 거래대금 미제공 -> 항상 NULL
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[StockOhlcv.code, StockOhlcv.date],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "value": stmt.excluded.value,
            },
        )
        await session.execute(stmt)
        count += 1
    return count


def plan_stock_ohlcv_fetch_start(existing_max: date | None, target_end: date, days: int) -> date:
    """기존 stock_ohlcv 최대 날짜(``existing_max``)를 보고 "얼마나 되돌아가서
    받을지"를 정하는 순수 함수(DB/외부 호출 없음, 단위테스트 가능) —
    routers/stocks.py::_ensure_candles_cached(온디맨드, 쿨다운/장중 판정은
    호출측 책임으로 남는다)와 collectors/stock_ohlcv_watchlist.py(배치, 하루
    1회뿐이라 쿨다운 불필요)가 "전체 백필 vs 증분 top-up" 크기 산정 로직만
    공유한다.

    - ``existing_max``가 ``None``(watchlist에 막 추가된 신규 코드) -> 전체
      백필: ``days`` 거래일 목표를 달력일로 환산(``CANDLE_CALENDAR_BUFFER_RATIO``/
      ``CANDLE_CALENDAR_BUFFER_MIN_DAYS`` 버퍼 적용)해 그만큼 이전부터.
    - 있으면(이미 캐시된 코드) -> 그 날짜부터 top-up(증분)만 — 짧은 구간이라
      저렴하다.
    """
    if existing_max is None:
        calendar_days = (
            int(days * CANDLE_CALENDAR_BUFFER_RATIO) + CANDLE_CALENDAR_BUFFER_MIN_DAYS
        )
        return target_end - timedelta(days=calendar_days)
    return existing_max


async def get_stock_series_from_db(session: AsyncSession, code: str, days: int) -> list[dict]:
    """DB stock_ohlcv에서 code의 최근 `days` 거래일을 markets series와 동일한
    컨벤션으로 반환한다 — ``get_market_series_from_db``(지수)와 짝을 이루는
    종목판(changeRate는 컬럼이 없어 하루치를 더 읽어 전일 종가 대비로 계산하는
    것까지 동일한 로직).

    routers/stocks.py(``stock_series`` 온디맨드 응답의 ``prices``)와
    collectors/volume_profile_snapshot.py(배치, PLAN.md §5.35-3, 이 함수가 읽은
    캔들을 그대로 ``quant/volume_profile.py``에 넘긴다)가 공유한다 — 쿼리 로직
    중복 금지."""
    stmt = (
        select(StockOhlcv)
        .where(StockOhlcv.code == code)
        .order_by(StockOhlcv.date.desc())
        .limit(days + 1)
    )
    rows = list(reversed((await session.execute(stmt)).scalars().all()))

    out: list[dict] = []
    prev_close: float | None = None
    for r in rows:
        close = float(r.close) if r.close is not None else None
        change_rate = 0.0
        if prev_close is not None and close is not None and prev_close:
            change_rate = (close - prev_close) / prev_close * 100
        out.append(
            {
                "date": r.date.strftime("%Y%m%d"),
                "open": float(r.open) if r.open is not None else None,
                "high": float(r.high) if r.high is not None else None,
                "low": float(r.low) if r.low is not None else None,
                "close": close,
                "changeRate": round(change_rate, 4),
                "volume": int(r.volume) if r.volume is not None else 0,
                "value": int(r.value) if r.value is not None else 0,
            }
        )
        if close is not None:
            prev_close = close

    return out[-days:] if len(out) > days else out
