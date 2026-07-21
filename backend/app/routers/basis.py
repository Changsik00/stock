"""GET /api/markets/basis — K200 선물-현물 베이시스 시계열 + 만기 상태
(PLAN.md §4.5-3 "외인 양손 보기").

DB 전용 조회다(§5.4 "DB 캐싱 우선") — index_ohlcv에 collectors/ohlcv.py 일별 배치가
미리 적재해 둔 두 시장(market='k200_futures' 선물종가, market='kospi200' KOSPI200
현물지수종가, clients/naver_index.py SYMBOLS 참고)을 그대로 읽어 베이시스를 계산할
뿐, 이 라우터에서 네이버를 직접 호출하지 않는다.

베이시스 = 선물종가 - KOSPI200 현물지수종가. 양수=콘탱고(선물이 비쌈, 정상),
음수=**백워데이션**(선물이 저평가 -> 프로그램 매도차익 유인) — §4.5 배경 절대로
"함정 탐지기"가 아니라 **중립적 상태 계기판**으로 취급한다(§4.5 서두 원칙,
signal 명칭도 "백워데이션" 같은 중립 표현만 쓴다).

만기 정보(expiry)는 app/derivatives.py의 순수 함수(next_futures_expiry/
is_quadruple_witching/days_to_expiry)로 계산한다 — K200 선물 만기는 결제월의
둘째 목요일이고, 3/6/9/12월物은 옵션 만기와 겹치는 "네 마녀의 날"이다.

**주의(작업 지시, PLAN.md §4.5-3)**: 이 라우터는 아직 ``main.py``에 등록하지
않는다 — routers/markets.py는 §4.5-2(외인 선물 수급)를 진행 중인 다른 에이전트
소유라 수정 금지이고, main.py 배선은 통합 단계에서 메인 세션이 처리한다. 그래서
테스트도 이 모듈의 ``router``를 ``TestClient``/``ASGITransport``에 직접 include해서
검증한다(routers/groups.py와 동일한 패턴, tests/test_basis_router.py 참고).

## GET /api/markets/basis/live (PLAN.md §4.7 3단 갱신 주기, 2026-07-20 장중 실측)

장중 실측 결과 fchart의 "오늘" 봉(k200_futures/kospi200 둘 다)이 체결마다 갱신되는
진짜 장중 캔들임을 확인 — 5~10분 캐시로 편입한다. DB(index_ohlcv)는 여전히 일별
배치(collectors/ohlcv.py)만 쓰고, 이 엔드포인트는 clients/naver_index.py를 직접
호출해 **메모리 캐시**로만 감싼다(§3.5 원칙 — 장중 값은 DB에 쌓지 않는다).
breadth/live·flow/live(routers/markets.py)와 동일한 "warm 함수 + TTL + Lock" 패턴이지만,
DB 폴백이 필요 없어(소스 자체가 최근 며칠 창을 항상 주므로) 세션 의존이 없다 —
그래서 collectors/live_refresh.py의 5~10분 인터벌 잡이 세션 없이 바로 호출할 수 있다.

**장 마감 게이트(2026-07-20, routers/markets.py breadth/flow/attention 라이브의
버그 수정과 함께 신규 5~10분 티어 전체에 처음부터 적용)**: 장 마감이면
``is_market_closed``로 걸러 네이버를 아예 호출하지 않는다 — DB 폴백이 없으므로
마지막 캐시(있으면)를 ``market_closed: true``로 재사용하고, 캐시조차 없으면
빈 값 + ``market_closed: true``로 응답한다(502 아님).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import naver_index
from ..db import get_session
from ..derivatives import days_to_expiry, is_quadruple_witching, next_futures_expiry
from ..market_hours import KST, is_market_closed
from ..models import IndexOhlcv

logger = logging.getLogger(__name__)

router = APIRouter(tags=["markets"])

FUTURES_MARKET = "k200_futures"
SPOT_MARKET = "kospi200"

# 1분 장중 라이브 캐시 TTL — collectors/live_refresh.py의 60초 인터벌 잡과 맞춘다.
# 그 잡이 죽어 있어도(예: 로컬 미기동) 이 라우트 핸들러가 캐시 미스 시 직접
# 채우므로 최소 기능은 항상 동작한다(breadth/live 등 기존 패턴과 동일).
# 2026-07-21(§5.5-2→§5.6 회귀 수정): 단일 심볼 조회 1~2회뿐이라 1분으로 당겨도
# 비용이 늘지 않는다고 판단해 프런트 폴링 주기만 먼저 옮겼는데, 이 TTL과
# live_refresh.py 스케줄러 잡 배정을 함께 옮기는 걸 빠뜨려 실제로는 계속 7분
# 캐시로 응답하는 회귀가 있었다(§5.6 후속 사용자 지적으로 재발견). TTL도 맞춘다.
LIVE_TTL_SECONDS = 60

_basis_live_cache: dict[str, object] = {"ts": 0.0, "data": None}
_basis_live_cache_lock = asyncio.Lock()


def _fetch_index_series_blocking(market: str, start: dt.date, end: dt.date) -> list[dict]:
    return naver_index.fetch_index_series(market, start, end)


async def _warm_basis_live() -> dict:
    """basis/live 캐시를 채우고 payload를 반환한다 — 라우트 핸들러와
    collectors/live_refresh.py의 5~10분 인터벌 잡이 공유한다."""
    now = time.monotonic()
    async with _basis_live_cache_lock:
        cached = _basis_live_cache["data"]
        if cached is not None and (now - _basis_live_cache["ts"]) < LIVE_TTL_SECONDS:
            return cached

        now_kst = dt.datetime.now(KST)
        if is_market_closed(now_kst):
            if cached is not None:
                payload = {**cached, "market_closed": True}
            else:
                payload = {
                    "date": None,
                    "futures_close": None,
                    "kospi200_close": None,
                    "basis": None,
                    "basis_pct": None,
                    "backwardation": None,
                    "expiry": _build_expiry(now_kst.date()),
                    "market_closed": True,
                    "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                }
            _basis_live_cache["data"] = payload
            _basis_live_cache["ts"] = now
            return payload

        today = dt.date.today()
        start = today - dt.timedelta(days=5)
        errors: dict[str, str] = {}
        futures_row = spot_row = None
        try:
            rows = await asyncio.to_thread(_fetch_index_series_blocking, FUTURES_MARKET, start, today)
            futures_row = rows[-1] if rows else None
        except Exception as e:  # noqa: BLE001
            errors["futures"] = str(e)[:200]
        try:
            rows = await asyncio.to_thread(_fetch_index_series_blocking, SPOT_MARKET, start, today)
            spot_row = rows[-1] if rows else None
        except Exception as e:  # noqa: BLE001
            errors["spot"] = str(e)[:200]

        if futures_row is None or spot_row is None:
            raise HTTPException(502, f"basis live fetch failed: {errors}")

        basis = futures_row["close"] - spot_row["close"]
        basis_pct = (basis / spot_row["close"] * 100) if spot_row["close"] else None

        payload = {
            "date": futures_row["date"].isoformat(),
            "futures_close": futures_row["close"],
            "kospi200_close": spot_row["close"],
            "basis": round(basis, 2),
            "basis_pct": round(basis_pct, 4) if basis_pct is not None else None,
            "backwardation": basis < 0,
            "expiry": _build_expiry(today),
            "market_closed": False,
            "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        _basis_live_cache["data"] = payload
        _basis_live_cache["ts"] = now
        return payload


def _build_expiry(today: dt.date) -> dict:
    expiry_date = next_futures_expiry(today)
    return {
        "date": expiry_date.isoformat(),
        "d_day": days_to_expiry(today),
        "quadruple": is_quadruple_witching(expiry_date),
    }


@router.get("/api/markets/basis")
async def basis_series(
    days: int = Query(180, ge=1, le=1500),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """K200 선물종가 - KOSPI200 현물지수종가의 일별 베이시스 시계열.

    두 시리즈 모두 date >= (today - days)로 각각 조회한 뒤 **날짜 교집합**만
    반환한다(휴장일/소스 지연으로 한쪽에만 있는 날짜는 제외 — 임의 채움 없이
    안전하게 맞춘다). 응답:

    - ``series``: [{date, futures_close, kospi200_close, basis, basis_pct}, ...]
      오름차순. basis_pct = basis / kospi200_close * 100.
    - ``latest``: series 마지막 행 기준 {date, backwardation, basis, basis_pct}
      — series가 비면 전부 None.
    - ``expiry``: {date, d_day, quadruple} — 오늘(dt.date.today()) 기준 다음 K200
      선물 만기(app/derivatives.py).
    """
    since = dt.date.today() - dt.timedelta(days=days)
    stmt = select(IndexOhlcv).where(
        IndexOhlcv.market.in_((FUTURES_MARKET, SPOT_MARKET)),
        IndexOhlcv.date >= since,
    )
    rows = (await session.execute(stmt)).scalars().all()

    futures_by_date: dict[dt.date, float] = {}
    spot_by_date: dict[dt.date, float] = {}
    for r in rows:
        if r.close is None:
            continue
        if r.market == FUTURES_MARKET:
            futures_by_date[r.date] = float(r.close)
        else:
            spot_by_date[r.date] = float(r.close)

    common_dates = sorted(set(futures_by_date) & set(spot_by_date))

    series: list[dict] = []
    for d in common_dates:
        futures_close = futures_by_date[d]
        kospi200_close = spot_by_date[d]
        basis = futures_close - kospi200_close
        basis_pct = (basis / kospi200_close * 100) if kospi200_close else None
        series.append(
            {
                "date": d.isoformat(),
                "futures_close": futures_close,
                "kospi200_close": kospi200_close,
                "basis": round(basis, 2),
                "basis_pct": round(basis_pct, 4) if basis_pct is not None else None,
            }
        )

    if series:
        latest = series[-1]
        latest_status = {
            "date": latest["date"],
            "backwardation": latest["basis"] < 0,
            "basis": latest["basis"],
            "basis_pct": latest["basis_pct"],
        }
    else:
        latest_status = {"date": None, "backwardation": None, "basis": None, "basis_pct": None}

    return {
        "days": days,
        "series": series,
        "latest": latest_status,
        "expiry": _build_expiry(dt.date.today()),
    }


@router.get("/api/markets/basis/live")
async def basis_live() -> dict:
    """K200 선물-현물 베이시스 장중 라이브(PLAN.md §4.7, 2026-07-20 실측 편입).

    fchart siseJson의 "오늘" 봉을 온디맨드로 재조회해 7분 메모리 캐시로 감싼다
    (DB에는 쓰지 않는다 — §3.5 원칙). 응답은 `/api/markets/basis`의 `latest`/`expiry`와
    같은 모양이다: ``{date, futures_close, kospi200_close, basis, basis_pct,
    backwardation, expiry, cached_at}``.
    """
    return await _warm_basis_live()
