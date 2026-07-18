"""코스피/코스닥/코스피200선물/코스피200(현물지수) 일봉 수집 -> index_ohlcv upsert
(PLAN.md §5.2/§5.4/§4.5-3).

KRX Open API(data-dbg.krx.co.kr)가 403(서비스 승인 미비, 2026-07 확인)이라 지수
일봉 소스를 이 배치로 교체했다:

1. **kospi/kosdaq/k200_futures 공통 1차 소스: clients/naver_index.py(네이버 fchart
   siseJson)**. 2026-07 조사 결과 yfinance(``^KQ11``)의 코스닥 거래량이 최근
   2개월을 제외한 전체 기간에서 800~1,300 수준의 쓰레기 값이었던 반면(코스피
   ``^KS11``은 정상), 네이버는 3개 시장 모두 전 기간 일관된 거래량을 반환해 세 시장
   모두 네이버로 통일했다(k200_futures는 애초에 yfinance에 심볼이 없어 네이버만
   썼다).
2. **yfinance는 폴백**: 네이버가 실패/빈 응답이면 kospi/kosdaq만 yfinance(``^KS11``/
   ``^KQ11``)로 재시도한다(무료/무인증이라 이미 의존성 보유). k200_futures는
   yfinance에 대응 심볼이 없어 폴백 없이 실패 처리한다. 폴백이 실제로 쓰인
   날에는 collect_log.message에 어떤 시장이 어떤 소스로 대체됐는지 남긴다
   (collect_ohlcv가 (rows, message) 튜플을 반환 -> collectors/base.py 참고).

**volume 저장 단위**: 네이버 fchart와 yfinance(정상 구간)의 raw 정수값이 같은
스케일임을 실측으로 확인했다(예: 2024-07 코스닥 62만~78만이 양쪽 소스에서 동일하게
나옴) — 그래서 변환 없이 각 소스가 반환한 정수를 그대로 저장한다. 단, 과거에
kospi/kosdaq이 yfinance 1차로 적재되며 코스닥 구간 상당수가 위 쓰레기 스케일(800~
1,300)로 섞여 있었으므로, 이 변경과 함께 3년 전체를 네이버로 재백필해 덮어썼다
(scripts/backfill_index_ohlcv.py, 2026-07-17) — 부분 upsert만으로는 스케일이 섞인
과거 행이 남을 수 있어 전체 기간을 다시 채워야 했다.

거래대금(value, 원화)은 두 소스 모두 제공하지 않아 NULL로 남는다 — 추후 키움 차트
TR로 교체되면 채워질 예정(PLAN.md §7 리스크 참고).

REGISTRY["ohlcv"]로 등록된다 (routers/admin.py가 이 모듈을 import해서 등록을
트리거함 — collectors/macro.py와 동일한 패턴).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math
import time

import yfinance as yf
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import naver_index
from ..models import IndexOhlcv
from .base import REGISTRY

logger = logging.getLogger(__name__)

# 매일 1건만 필요하지만 휴장일/배치 실패로 며칠 비었을 수 있어(collectors/macro.py와
# 동일한 이유) 넉넉히 최근 며칠을 함께 조회해 upsert한다(idempotent라 안전).
LOOKBACK_DAYS = 10

YFINANCE_TICKERS = {"kospi": "^KS11", "kosdaq": "^KQ11"}

NAVER_REQUEST_DELAY_SECONDS = 0.5

# kospi200(KPI200) = KOSPI200 현물지수(§4.5-3, 베이시스 계산의 분모). k200_futures와
# 마찬가지로 yfinance 폴백 심볼이 없어(YFINANCE_TICKERS에 없음) 네이버 실패 시
# ValueError로 전파된다(fetch_market_rows 참고) -- k200_futures와 동일한 취급.
MARKETS = ("kospi", "kosdaq", "k200_futures", "kospi200")


def _to_float(v) -> float | None:
    if v is None:
        return None
    f = float(v)
    return None if math.isnan(f) else f


def _to_int(v) -> int | None:
    f = _to_float(v)
    return None if f is None else int(f)


def _fetch_yfinance(ticker: str, start: dt.date, end: dt.date) -> list[dict]:
    df = yf.Ticker(ticker).history(
        start=start.isoformat(),
        # yfinance's `end` is exclusive, so add a day to include the requested end date.
        end=(end + dt.timedelta(days=1)).isoformat(),
        auto_adjust=False,
    )
    if df.empty:
        raise ValueError(f"yfinance returned no rows for {ticker}")

    out: list[dict] = []
    for idx, row in df.iterrows():
        close = _to_float(row.get("Close"))
        if close is None:
            continue
        out.append(
            {
                "date": idx.date(),
                "open": _to_float(row.get("Open")),
                "high": _to_float(row.get("High")),
                "low": _to_float(row.get("Low")),
                "close": close,
                "volume": _to_int(row.get("Volume")),
            }
        )
    return out


def fetch_market_rows(market: str, start: dt.date, end: dt.date) -> tuple[list[dict], str]:
    """market(kospi/kosdaq/k200_futures/kospi200)의 일봉을 [start, end]로 가져온다.

    네이버(clients/naver_index.py)가 1차 소스다. 실패/빈 응답이면 kospi/kosdaq만
    yfinance로 폴백한다(k200_futures/kospi200은 yfinance 심볼이 없어 폴백 불가 ->
    예외 전파).

    Returns ``(rows, source)`` where source is ``"naver"`` or ``"yfinance-fallback"`` —
    호출측이 폴백 여부를 collect_log.message에 남기는 데 쓴다.

    Blocking(requests/yfinance) — 호출측(collect_ohlcv)이 asyncio.to_thread로 감싼다.
    """
    time.sleep(NAVER_REQUEST_DELAY_SECONDS)
    try:
        rows = naver_index.fetch_index_series(market, start, end)
        if rows:
            return rows, "naver"
        logger.warning("네이버 %s 조회 결과 0행", market)
    except Exception as e:  # noqa: BLE001 - naver_index raises requests errors / NaverIndexError
        logger.warning("네이버 조회 실패(%s, %s)", market, e)

    ticker = YFINANCE_TICKERS.get(market)
    if ticker is None:
        raise ValueError(f"네이버 조회 실패했고 {market}은 yfinance 폴백 심볼이 없습니다")

    logger.warning("%s(%s) yfinance로 폴백합니다", market, ticker)
    rows = _fetch_yfinance(ticker, start, end)
    return rows, "yfinance-fallback"


async def _upsert_rows(session: AsyncSession, market: str, rows: list[dict]) -> int:
    count = 0
    for row in rows:
        stmt = pg_insert(IndexOhlcv).values(
            market=market,
            date=row["date"],
            open=row.get("open"),
            high=row.get("high"),
            low=row.get("low"),
            close=row.get("close"),
            volume=row.get("volume"),
            value=row.get("value"),  # 두 소스 모두 거래대금 미제공 -> 항상 NULL(§7 참고)
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[IndexOhlcv.market, IndexOhlcv.date],
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


async def collect_ohlcv(session: AsyncSession, target_date: dt.date) -> tuple[int, str | None]:
    """kospi/kosdaq/k200_futures/kospi200의 target_date 전후 일봉을 index_ohlcv에 upsert.

    Returns ``(rows, message)`` — message는 이번 실행에서 yfinance로 폴백한 시장이
    있으면 그 목록을 담고, 전부 네이버로 성공했으면 None(collectors/base.py가
    collect_log.message에 그대로 기록).
    """
    start = target_date - dt.timedelta(days=LOOKBACK_DAYS)
    total = 0
    fallbacks: list[str] = []
    for market in MARKETS:
        rows, source = await asyncio.to_thread(fetch_market_rows, market, start, target_date)
        total += await _upsert_rows(session, market, rows)
        if source != "naver":
            fallbacks.append(f"{market}={source}")
    message = f"폴백 사용: {', '.join(fallbacks)}" if fallbacks else None
    return total, message


REGISTRY["ohlcv"] = collect_ohlcv
