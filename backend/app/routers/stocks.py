"""GET /api/stocks/* — 종목 검색·미니 상세 (PLAN.md §5.3/§6 Phase 3.7-2).

- ``GET /api/stocks/search``: stocks 마스터(DB) LIKE 검색 — 외부 호출 없음, DB만
  읽는다(§5.4 "DB 캐싱 우선" — 마스터는 collectors/value_rank.py가 매일 갱신).
- ``GET /api/stocks/{code}/series``: 캔들(네이버 fchart 종목 일봉) + 투자자별 수급
  (키움 ka10059) — 둘 다 온디맨드 + DB 캐시(§5.4 "온디맨드 보강": 미리 수집 못 하는
  것만 실시간 호출). 캔들은 stock_ohlcv, 수급은 stock_flow에 캐시하고, 이미 최신
  거래일 데이터가 있으면 외부 호출을 생략한다 — 같은 code를 반복 요청해도 두
  번째부터는 DB만 읽는다.
- ``GET /api/stocks/{code}/whale``: Phase 4+ 예정, 아직 501 스텁.

캔들/수급 실패 처리(§5.3 에러 규약 "외부 API 실패는 502 + {source, detail}"을
아래처럼 세분화):
- 캔들(네이버) 실패 → 502 ``{"source": "naver_fchart", "detail": ...}``(캔들이
  응답의 주 콘텐츠라 실패하면 전체 요청을 502로 막는다).
- 수급(키움) 실패 → 부분 성공 허용: flows는 빈 dict로 두고 200 반환, 실패 사유는
  응답의 ``meta.flows_error``에 남긴다(키움 앱키 미설정/일시 장애가 캔들 조회까지
  막지 않도록).

캐시 신선도 판정은 "가장 최근 평일(월~금)" 휴리스틱을 쓴다(``_latest_trading_day``)
— 공휴일은 반영하지 않는다. 이 휴리스틱만 쓰면 공휴일(평일인데 휴장)에는 그
"최근 평일"의 데이터가 소스에 영영 존재하지 않아 **매 요청마다** 외부 API를 다시
부르게 된다(실제로 2026-07-17 관측: 그날 하루 index_ohlcv/stock_ohlcv에 아무 소스도
데이터를 채우지 않아 재현·확인함) — 그래서 아래 ``_EXTERNAL_FETCH_COOLDOWN_SECONDS``
쿨다운을 덧붙인다: DB에 **이미 뭔가 캐시돼 있는데** 그게 최신이 아닐 때만 재시도
간격을 두고(60초), 그 사이 요청은 오래됐더라도 캐시를 그대로 서빙한다. DB에 캐시가
**전혀 없는** 최초 조회는 쿨다운 없이 항상 시도한다(줄 게 없으니 실패 시 그대로
502/빈 flows로 알려야 한다).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import naver_index
from ..clients.kiwoom import (
    MINUTE_CHART_INTERVALS,
    KiwoomAPIError,
    KiwoomAuthError,
    KiwoomClient,
    parse_minute_chart_rows,
)
from ..db import get_session
from ..models import Stock, StockFlow, StockOhlcv

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stocks", tags=["stocks"])

_NOT_IMPLEMENTED = "종목 데이터는 아직 준비되지 않았습니다 (Phase 4 예정)."

DEFAULT_SEARCH_LIMIT = 15
MAX_SEARCH_LIMIT = 100

# 종목 수급 최초 백필 한도(달력일 기준) — PLAN.md §6 Phase 3.7-2 지시: "최근
# 90일까지만". ka10059 1콜이 실측상 최근 ~100거래일(약 5개월치)을 한 번에 반환해
# (clients/kiwoom.py의 stock_investor_daily, 2026-07-19 실호출 검증) 별도 연속조회
# (cont-yn/next-key)가 필요 없다 — 받은 뒤 이 한도로 잘라서 upsert한다.
FLOW_BACKFILL_DAYS = 90

# 캔들 최초 백필 시 거래일 수 -> 달력일 버퍼(주말 비율 5/7에 공휴일 여유를 더함).
_CANDLE_CALENDAR_BUFFER_RATIO = 1.6
_CANDLE_CALENDAR_BUFFER_MIN_DAYS = 10

# 이미 캐시가 있는 code에 대해 "오래됨" 판정이 나도, 이 시간 안에 재시도했으면
# 외부 호출을 또 하지 않는다(모듈 docstring 참고 — 공휴일에 매 요청마다 재호출되는
# 것을 막는 안전장치). 프로세스 메모리 캐시라 재기동하면 초기화된다(markets.py의
# breadth live 캐시와 같은 성격, PLAN.md §5.1 "다중 워커 배포는 아직 없음").
_EXTERNAL_FETCH_COOLDOWN_SECONDS = 60.0
_candle_fetch_attempted_at: dict[str, float] = {}
_flow_fetch_attempted_at: dict[str, float] = {}

_KST = dt.timezone(dt.timedelta(hours=9))


def _today_kst() -> dt.date:
    return dt.datetime.now(_KST).date()


def _latest_trading_day() -> dt.date:
    """가장 최근 평일(월~금). 공휴일은 반영하지 않는 단순 휴리스틱(모듈 docstring
    참고)."""
    d = _today_kst()
    while d.weekday() >= 5:  # 5=토, 6=일
        d -= dt.timedelta(days=1)
    return d


# -- 검색 ---------------------------------------------------------------------


@router.get("/search")
async def search_stocks(
    q: str = Query(..., min_length=1),
    limit: int = Query(DEFAULT_SEARCH_LIMIT, ge=1, le=MAX_SEARCH_LIMIT),
    session: AsyncSession = Depends(get_session),
):
    """stocks 마스터 LIKE 검색 — 이름 부분일치(대소문자 무시) + 코드 전방일치를
    OR로 묶고, 이름 짧은 순으로 정렬한다(짧을수록 더 정확한 매치일 가능성이 높다는
    단순 휴리스틱 — 예: "삼성" 검색 시 "삼성전자"가 "삼성전자우선주"류보다 먼저).
    """
    query = q.strip()
    if not query:
        return []

    stmt = (
        select(Stock)
        .where(or_(Stock.name.ilike(f"%{query}%"), Stock.code.ilike(f"{query}%")))
        .order_by(func.length(Stock.name).asc(), Stock.name.asc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {"code": r.code, "name": r.name, "market": r.market, "is_etf": r.is_etf} for r in rows
    ]


# -- 캔들 (stock_ohlcv 캐시 + 네이버 fchart 온디맨드) --------------------------


async def _upsert_ohlcv_rows(session: AsyncSession, code: str, rows: list[dict]) -> int:
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


async def _ensure_candles_cached(session: AsyncSession, code: str, days: int) -> None:
    """stock_ohlcv에 code의 최신 거래일 캔들이 이미 있으면 아무 것도 하지 않는다
    (캐시 히트, 외부 호출 생략). 없거나 오래됐으면 네이버 fchart를 호출해 필요한
    구간만 upsert한다.

    Raises: naver_index.NaverIndexError / requests 예외를 그대로 전파한다 —
    호출측(stock_series)이 502로 변환한다.
    """
    target_end = _latest_trading_day()
    existing_max = (
        await session.execute(select(func.max(StockOhlcv.date)).where(StockOhlcv.code == code))
    ).scalar_one_or_none()

    if existing_max is not None and existing_max >= target_end:
        return  # 캐시 히트

    if existing_max is None:
        calendar_days = (
            int(days * _CANDLE_CALENDAR_BUFFER_RATIO) + _CANDLE_CALENDAR_BUFFER_MIN_DAYS
        )
        fetch_start = target_end - dt.timedelta(days=calendar_days)
    else:
        # 캐시는 있지만 오래됨 — 쿨다운 확인(모듈 docstring "공휴일" 안전장치).
        now = time.monotonic()
        last_attempt = _candle_fetch_attempted_at.get(code)
        if last_attempt is not None and (now - last_attempt) < _EXTERNAL_FETCH_COOLDOWN_SECONDS:
            return  # 최근에 이미 재시도했음 — 있는 캐시로 서빙
        # 마지막 캐시일부터 다시 받아 당일 수정치까지 반영(짧은 구간이라 저렴).
        fetch_start = existing_max

    _candle_fetch_attempted_at[code] = time.monotonic()
    rows = await asyncio.to_thread(naver_index.fetch_stock_series, code, fetch_start, target_end)
    await _upsert_ohlcv_rows(session, code, rows)


async def _read_candles(session: AsyncSession, code: str, days: int) -> list[dict]:
    """DB stock_ohlcv에서 최근 `days` 거래일을 읽어 markets series의 prices와 동일한
    컨벤션으로 반환한다(services.get_market_series_from_db와 동일 로직 — changeRate는
    컬럼이 없어 하루치를 더 읽어 전일 종가 대비로 계산)."""
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


# -- 수급 (stock_flow 캐시 + 키움 ka10059 온디맨드) ----------------------------

# ka10059(종목별투자자기관별) 응답 필드 -> stock_flow.investor 표기.
# collectors/market_flow.py(KA10051_FIELD_TO_INVESTOR)의 컨벤션과 통일한다 — 거기서
# 이미 PLAN.md §5.2 원 12분류 밖의 "국가"를 새 값으로 추가해 둔 전례를 그대로
# 따른다(아래 natn). 실호출 검증(2026-07-19, 005930 조회): orgn(기관계) 값이
# fnnc_invt~etc_corp 9개 세부 필드 합과 맞는 구조 — market_flow의 13분류(기관계
# 총계 포함)와 대응된다. penfnd_etc는 원문이 "연기금등"이라 §5.2의 "연기금"과
# 완전히 동일한 명칭은 아니지만 가장 가까운 기존 분류로 매핑한다.
KA10059_FIELD_TO_INVESTOR: dict[str, str] = {
    "ind_invsr": "개인",
    "frgnr_invsr": "외국인",
    "orgn": "기관계",
    "fnnc_invt": "금융투자",
    "insrnc": "보험",
    "invtrt": "투신",
    "etc_fnnc": "기타금융",
    "bank": "은행",
    "penfnd_etc": "연기금",
    "samo_fund": "사모",
    "natn": "국가",
    "etc_corp": "기타법인",
    "natfor": "기타외국인",
}


def _parse_signed_int(raw: object) -> int | None:
    """ka10059 숫자 필드는 "+"/"-" 부호가 붙은 문자열로 온다(예: "-218284").
    market_flow.py의 _parse_int와 동일한 방어적 파싱."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, (int, float)):
        return int(raw)
    text = str(raw).strip().replace(",", "")
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        logger.warning("stocks: ka10059 숫자 필드 파싱 실패, None 처리: %r", raw)
        return None


def _parse_ka10059_rows(data: dict) -> list[dict]:
    """ka10059 응답 body -> [{"date": dt.date, "investor": str, "net_value": int|None,
    "net_volume": None}, ...] (일자 x 투자자 13개).

    net_volume은 항상 None — 이 라우터는 금액 모드(amt_qty_tp="1", 기본값)만
    호출한다(수량까지 받으려면 별도 콜이 필요해 호출 예산이 배로 늘어남,
    market_flow.py의 ka10051과 동일한 절약 관례).

    DB 세션 없이 순수 계산이라 단위테스트 가능(tests/test_stocks_router.py 참고).
    """
    out: list[dict] = []
    for row in data.get("stk_invsr_orgn") or []:
        date_str = row.get("dt")
        if not date_str:
            continue
        try:
            date = dt.datetime.strptime(date_str, "%Y%m%d").date()
        except ValueError:
            logger.warning("stocks: ka10059 dt 파싱 실패, 건너뜀: %r", date_str)
            continue
        for field, investor in KA10059_FIELD_TO_INVESTOR.items():
            out.append(
                {
                    "date": date,
                    "investor": investor,
                    "net_value": _parse_signed_int(row.get(field)),
                    "net_volume": None,
                }
            )
    return out


async def _upsert_flow_rows(session: AsyncSession, code: str, rows: list[dict]) -> int:
    count = 0
    for row in rows:
        stmt = pg_insert(StockFlow).values(
            code=code,
            date=row["date"],
            investor=row["investor"],
            net_value=row["net_value"],
            net_volume=row["net_volume"],
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[StockFlow.code, StockFlow.date, StockFlow.investor],
            set_={"net_value": stmt.excluded.net_value, "net_volume": stmt.excluded.net_volume},
        )
        await session.execute(stmt)
        count += 1
    return count


async def _ensure_flows_cached(session: AsyncSession, code: str) -> None:
    """stock_flow에 code의 최신 거래일 수급이 이미 있으면 외부 호출을 생략한다
    (캐시 히트). 없으면 ka10059를 1콜 호출해 최근 FLOW_BACKFILL_DAYS일만 잘라
    upsert한다(모듈 docstring 참고 — 1콜로 충분해 연속조회 불필요).

    Raises: KiwoomAuthError/KiwoomAPIError/httpx 예외를 그대로 전파한다 — 호출측이
    "수급만 실패면 200 + flows 빈 채로"로 흡수한다.
    """
    target_end = _latest_trading_day()
    existing_max = (
        await session.execute(select(func.max(StockFlow.date)).where(StockFlow.code == code))
    ).scalar_one_or_none()

    if existing_max is not None and existing_max >= target_end:
        return  # 캐시 히트

    if existing_max is not None:
        # 캐시는 있지만 오래됨 — 쿨다운 확인(모듈 docstring "공휴일" 안전장치).
        now = time.monotonic()
        last_attempt = _flow_fetch_attempted_at.get(code)
        if last_attempt is not None and (now - last_attempt) < _EXTERNAL_FETCH_COOLDOWN_SECONDS:
            return  # 최근에 이미 재시도했음 — 있는 캐시로 서빙

    _flow_fetch_attempted_at[code] = time.monotonic()
    async with KiwoomClient() as client:
        data, _headers = await client.stock_investor_daily(code)

    rows = _parse_ka10059_rows(data)
    cutoff = target_end - dt.timedelta(days=FLOW_BACKFILL_DAYS)
    rows = [r for r in rows if r["date"] >= cutoff]
    await _upsert_flow_rows(session, code, rows)


async def _read_flows(session: AsyncSession, code: str, days: int) -> dict[str, list[dict]]:
    """investor -> [{date, net_value, net_volume, cum_net_value}, ...] (날짜 오름차순).
    cum_net_value는 이 응답 창(window) 안에서의 누적 순매수 — 차트 왼쪽 끝을 0으로
    본다(PLAN.md §6 Phase 3.7-2 "누적순매수" 요구)."""
    since = _latest_trading_day() - dt.timedelta(days=days)
    stmt = (
        select(StockFlow)
        .where(StockFlow.code == code, StockFlow.date >= since)
        .order_by(StockFlow.investor, StockFlow.date)
    )
    rows = (await session.execute(stmt)).scalars().all()

    flows: dict[str, list[dict]] = {}
    cum: dict[str, int] = {}
    for r in rows:
        cum[r.investor] = cum.get(r.investor, 0) + (r.net_value or 0)
        flows.setdefault(r.investor, []).append(
            {
                "date": r.date.strftime("%Y%m%d"),
                "net_value": r.net_value,
                "net_volume": r.net_volume,
                "cum_net_value": cum[r.investor],
            }
        )
    return flows


# -- 엔드포인트 -----------------------------------------------------------------


@router.get("/{code}/series")
async def stock_series(
    code: str,
    days: int = Query(180, ge=1, le=1500),
    session: AsyncSession = Depends(get_session),
):
    stock = await session.get(Stock, code)
    # 이름/시장/ETF 여부를 지금 바로 일반 값으로 떼어 둔다 — 아래에서 실패 시
    # session.rollback()을 호출하면 expire_on_commit 설정과 무관하게 이 ORM
    # 인스턴스가 expire되어, 나중에 stock.name에 접근하면 (동기 컨텍스트에서)
    # 지연 재조회를 시도하다 MissingGreenlet으로 죽는다 — 그걸 피하기 위함.
    stock_name = stock.name if stock else None
    stock_market = stock.market if stock else None
    stock_is_etf = stock.is_etf if stock else None

    try:
        await _ensure_candles_cached(session, code, days)
        await session.commit()
    except Exception as e:  # noqa: BLE001 - naver_index.NaverIndexError / requests 등
        await session.rollback()
        raise HTTPException(
            502, detail={"source": "naver_fchart", "detail": str(e)[:300]}
        ) from e

    prices = await _read_candles(session, code, days)

    meta: dict[str, str] = {}
    try:
        await _ensure_flows_cached(session, code)
        await session.commit()
    except (KiwoomAuthError, KiwoomAPIError) as e:
        await session.rollback()
        logger.warning("stocks: %s 수급 조회 실패(키움), flows 빈 채로 반환: %s", code, e)
        meta["flows_error"] = str(e)[:300]
    except Exception as e:  # noqa: BLE001 - httpx 등 네트워크 계열 예외 포함
        await session.rollback()
        logger.warning("stocks: %s 수급 조회 실패, flows 빈 채로 반환: %s", code, e)
        meta["flows_error"] = str(e)[:300]

    flows = await _read_flows(session, code, days)

    return {
        "code": code,
        "name": stock_name,
        "market": stock_market,
        "is_etf": stock_is_etf,
        "days": days,
        "prices": prices,
        "flows": flows,
        "meta": meta,
    }


@router.get("/{code}/whale")
def stock_whale(code: str):
    raise HTTPException(501, _NOT_IMPLEMENTED)


# -- 분봉 (ka10080 온디맨드 + 짧은 메모리 캐시, DB 미저장 — PLAN.md §5 Phase 5.1) ------
#
# 분봉은 "오늘 하루치만" 온디맨드 조회로 충분하다는 §5 원칙에 따라 stock_ohlcv 같은
# 영구 캐시 테이블을 두지 않는다(일봉과 다른 저장 정책 — 모듈 docstring 참고).
# 캐시는 markets.py의 breadth/live·flow/live와 동일한 "모듈 전역 dict + asyncio.Lock"
# 패턴이지만, 종목마다 독립 데이터라 (code, interval) 튜플로 키를 잡는다. 프로세스
# 재기동 시 초기화되는 단순 캐시로 충분(다중 워커 배포 없음, PLAN.md §5.1).

_intraday_cache: dict[tuple[str, int], dict] = {}
_intraday_cache_lock = asyncio.Lock()


def _intraday_ttl_seconds(interval: int) -> int:
    """1분봉은 60초, 그 외(3/5/10/15/30/45/60분)는 interval*60초 — PLAN.md §5.1
    지시("interval 값에 따라 TTL 차등") 그대로. 분봉 주기보다 짧게 캐시해봤자
    같은 봉을 다시 받을 뿐이라 봉 주기에 맞춘 것."""
    return 60 if interval == 1 else interval * 60


async def _warm_stock_intraday(code: str, interval: int) -> dict:
    """intraday 캐시를 채우고 payload를 반환한다. 키움 호출 실패는 502로 변환
    (markets.py 라이브 엔드포인트들과 동일한 정책 — 이 엔드포인트는 종목 캔들이
    응답의 전부라 stock_series의 "수급만 부분 실패 허용"과 달리 실패를 그대로
    502로 알린다)."""
    cache_key = (code, interval)
    ttl = _intraday_ttl_seconds(interval)
    now = time.monotonic()
    async with _intraday_cache_lock:
        cached = _intraday_cache.get(cache_key)
        if cached is not None and (now - cached["ts"]) < ttl:
            return cached["data"]

        try:
            async with KiwoomClient() as client:
                data, _headers = await client.stock_minute_chart(code, str(interval))
        except (KiwoomAuthError, KiwoomAPIError) as e:
            raise HTTPException(
                502, detail={"source": "kiwoom_ka10080", "detail": str(e)[:300]}
            ) from e
        except Exception as e:  # noqa: BLE001 - httpx 등 네트워크 계열 예외 포함
            raise HTTPException(
                502, detail={"source": "kiwoom_ka10080", "detail": str(e)[:300]}
            ) from e

        bars = parse_minute_chart_rows(data, "ka10080")
        payload = {
            "code": code,
            "interval": interval,
            "date": bars[-1]["date"] if bars else None,
            "bars": bars,
            "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        _intraday_cache[cache_key] = {"ts": now, "data": payload}
        return payload


@router.get("/{code}/intraday")
async def stock_intraday(code: str, interval: int = Query(..., description="분봉 간격(분)")):
    """종목 분봉 — 키움 ka10080을 온디맨드로 호출해 "오늘"(최신 거래일) 하루치만
    반환한다(DB 미저장, §5 원칙). `interval`은 실호출로 확정된 값만 허용
    (`MINUTE_CHART_INTERVALS` — 1/3/5/10/15/30/45/60), 그 외는 400.

    Returns ``{"code", "interval", "date": "YYYYMMDD"|None, "bars": [{"date",
    "time": "HHMM", "timestamp": iso8601, "open", "high", "low", "close",
    "volume"}, ...], "cached_at": iso8601}`` — bars는 오름차순(과거->최신).
    """
    if interval not in MINUTE_CHART_INTERVALS:
        raise HTTPException(
            400, f"interval must be one of {sorted(MINUTE_CHART_INTERVALS)}"
        )
    return await _warm_stock_intraday(code, interval)
