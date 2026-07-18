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
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..derivatives import days_to_expiry, is_quadruple_witching, next_futures_expiry
from ..models import IndexOhlcv

router = APIRouter(tags=["markets"])

FUTURES_MARKET = "k200_futures"
SPOT_MARKET = "kospi200"


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
