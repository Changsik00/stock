"""GET /api/stocks/* — 종목 검색·미니 상세 (PLAN.md §5.3/§6 Phase 3.7-2).

- ``GET /api/stocks/search``: stocks 마스터(DB) LIKE 검색 — 외부 호출 없음, DB만
  읽는다(§5.4 "DB 캐싱 우선" — 마스터는 collectors/value_rank.py가 매일 갱신).
- ``GET /api/stocks/{code}/series``: 캔들(네이버 fchart 종목 일봉) + 투자자별 수급
  (키움 ka10059) + 종목별 프로그램매매(키움 ka90013, PLAN.md §5.29) + 종목별
  공매도(KRX 정보데이터시스템, PLAN.md §5.32) — 넷 다 온디맨드 + DB 캐시(§5.4
  "온디맨드 보강": 미리 수집 못 하는 것만 실시간 호출). 캔들은 stock_ohlcv, 수급은
  stock_flow, 프로그램매매는 program_trade, 공매도는 short_selling_stock에
  캐시하고, 이미 최신 거래일 데이터가 있으면 외부 호출을 생략한다 — 같은 code를
  반복 요청해도 두 번째부터는 DB만 읽는다. ka90013은 차익/비차익 분리를 주지 않아
  (collectors/program_stock.py 모듈 docstring 참고) 응답의 `program_trade`
  행은 `total_net`만 채워지고 `arb_net`/`non_arb_net`는 항상 null이다. 응답의
  `program_trade_summary`는 `program_trade`를 요약한 추세 지표
  (`quant/regime_backtest.py::next_streak`을 그대로 재사용한 연속 순매수/
  순매도 일수 `streak`, 최근 5/10거래일 누적 순매수 `cumulative_net_5d`/
  `cumulative_net_10d`) — 판단/추천이 아니라 관측값만 담는다. 공매도
  (`short_selling`)는 `clients/krx_short_selling.py` 모듈 docstring 참고 —
  대차잔고(대시보드 "대차잔고" 타일, macro_series.lending_balance)와는 별개
  지표다. `include_flow_percentile=true`(옵트인, PLAN.md §5.38)이면 응답에
  `flow_percentile`(동일 시가총액 tier 내 오늘 수급 percentile,
  quant/flow_percentile.py)을 추가한다 — 새 외부 호출 없이 value_rank+
  stock_flow만 조인한다.
- ``GET /api/stocks/{code}/whale``: Phase 4+ 예정, 아직 501 스텁.
- ``GET /api/stocks/{code}/volume-profile-history``: `collectors/
  volume_profile_snapshot.py`가 watchlist 종목에 대해 매일 적재한 §5.34 거래량
  프로파일 스냅샷의 누적 시계열을 그대로 반환한다(PLAN.md §5.35-4, 재계산
  아님 — DB 전용 읽기).

캔들/수급 실패 처리(§5.3 에러 규약 "외부 API 실패는 502 + {source, detail}"을
아래처럼 세분화):
- 캔들(네이버) 실패 → 502 ``{"source": "naver_fchart", "detail": ...}``(캔들이
  응답의 주 콘텐츠라 실패하면 전체 요청을 502로 막는다).
- 수급(키움) 실패 → 부분 성공 허용: flows는 빈 dict로 두고 200 반환, 실패 사유는
  응답의 ``meta.flows_error``에 남긴다(키움 앱키 미설정/일시 장애가 캔들 조회까지
  막지 않도록).
- 프로그램매매(키움) 실패 → 수급과 동일한 부분 성공 정책: program_trade는 빈
  리스트로 두고 200 반환, 실패 사유는 ``meta.program_trade_error``에 남긴다.
- 공매도(KRX) 실패 → 위와 동일한 부분 성공 정책: short_selling은 빈 리스트로
  두고 200 반환, 실패 사유는 ``meta.short_selling_error``에 남긴다.

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

from ..clients import krx_short_selling, naver_index
from ..clients.kiwoom import (
    MINUTE_CHART_INTERVALS,
    KiwoomAPIError,
    KiwoomAuthError,
    KiwoomClient,
    parse_minute_chart_rows,
)
from ..collectors.program_stock import parse_program_trade_rows
from ..db import get_session
from ..market_hours import KST, is_nxt_closed
from ..models import (
    InvestorWarningEvent,
    ProgramTrade,
    ShortSellingStock,
    Stock,
    StockFlow,
    StockOhlcv,
    ValueRank,
    VolumeProfileDaily,
)
from ..quant.flow_percentile import compute_flow_percentiles
from ..quant.investor_warning_status import classify_investor_warning_status
from ..quant.regime_backtest import next_streak
from ..quant.signals import (
    compute_vwap,
    detect_breakout,
    momentum,
    moving_average_cross,
    volume_spike,
)
from ..quant.volume_profile import compute_volume_profile, detect_levels
from ..services import (
    get_stock_series_from_db,
    plan_stock_ohlcv_fetch_start,
    upsert_stock_ohlcv_rows,
)

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

# 종목별 프로그램매매(ka90013) 백필 한도 — PLAN.md §5.29-3. ka90013은 ka10059와
# 달리 한 콜에 정확히 20거래일(약 1개월)만 온다(collectors/program_stock.py
# 모듈 docstring, clients/kiwoom.py "ka90013 실측 확정" 절 참고) — 90일 컷오프를
# 둬도 실제로는 항상 20거래일 전부가 그대로 들어온다(자르는 효과 없음, 다른
# 종목별 데이터와 동일한 관례를 유지하기 위해 형식상 둔다).
PROGRAM_TRADE_BACKFILL_DAYS = 90

# 종목별 공매도(KRX 정보데이터시스템) 백필 한도 — PLAN.md §5.32-3. 이 소스는
# ka10059/ka90013과 달리 요청 자체가 날짜 범위(strtDd/endDd)라 "한 콜에 며칠 오는지"
# 제약이 없다(clients/krx_short_selling.py 모듈 docstring "날짜 범위 제약" 절 —
# 2년 범위도 한 번에 됨을 실측 확인) — collectors/short_selling_market.py의
# LOOKBACK_DAYS(120)와 동일한 값을 써서 시장 전체 수집기와 조회 창을 맞춘다.
SHORT_SELLING_BACKFILL_DAYS = 120

# 이미 캐시가 있는 code에 대해 "오래됨" 판정이 나도, 이 시간 안에 재시도했으면
# 외부 호출을 또 하지 않는다(모듈 docstring 참고 — 공휴일에 매 요청마다 재호출되는
# 것을 막는 안전장치). 프로세스 메모리 캐시라 재기동하면 초기화된다(markets.py의
# breadth live 캐시와 같은 성격, PLAN.md §5.1 "다중 워커 배포는 아직 없음").
_EXTERNAL_FETCH_COOLDOWN_SECONDS = 60.0
_candle_fetch_attempted_at: dict[str, float] = {}
_flow_fetch_attempted_at: dict[str, float] = {}
_program_trade_fetch_attempted_at: dict[str, float] = {}
_short_selling_fetch_attempted_at: dict[str, float] = {}


def _today_kst() -> dt.date:
    return dt.datetime.now(KST).date()


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


async def _ensure_candles_cached(session: AsyncSession, code: str, days: int) -> None:
    """stock_ohlcv에 code의 최신 거래일 캔들이 이미 있고 **장 마감**이면 아무 것도
    하지 않는다(캐시 히트, 외부 호출 생략 — 그 값이 진짜 확정 종가이므로). 없거나
    오래됐으면, 또는 장중인데 쿨다운이 지났으면 네이버 fchart를 호출해 필요한
    구간만 upsert한다.

    **2026-07-21 수정(SOT 이슈)**: 원래는 "오늘 날짜 행이 존재하면" 그것만으로
    캐시 히트 처리했다 — 장중에 처음 이 종목 상세를 연 사람이 그 순간의 부분
    스냅샷을 그대로 그날 하루 종일 고정시켜버리는 버그였다(사용자 지적: 리스트
    카드는 실시간으로 갱신되는데 종목 상세 모달만 다른 값을 계속 보여줌 — attention/
    value-rank/live 등은 60초~7분 TTL로 계속 새로 받아오는데 이 캐시만 "오늘치
    있음=끝"으로 하루 종일 얼어붙어 있었다). 장중에는 오늘 행이 있어도 여전히
    쿨다운(60초)마다 재조회해 index-tiles/live와 같은 성격의 "장중엔 계속 새로
    받는다" 원칙을 맞춘다 — 장 마감 후에만 진짜로 캐시 히트로 취급한다.

    **2026-07-21 추가 수정(NXT)**: "장중" 판정은 KRX 정규장(09:00~15:30)이 아니라
    ``market_hours.is_nxt_closed``(NXT 확장세션 08:00~20:00)를 쓴다 — 사용자 확인 +
    실측(18:36에도 개별 종목 시세가 계속 바뀜)으로, 개별 종목은 정규장 마감 이후
    NXT에서 20:00까지 계속 거래된다. 지수/집계 통계(index-tiles/basis/groups 등)는
    정규장에서 그대로 고정되는 게 실측으로 확인돼(``is_market_closed`` 그대로 유지)
    이 함수만 더 넓은 창을 쓴다 — market_hours.py 모듈 docstring 참고.

    **2026-07-28 리팩터(PLAN.md §5.35-2)**: upsert 자체(``services.
    upsert_stock_ohlcv_rows``)와 "전체 백필 vs 증분 top-up" 크기 산정
    (``services.plan_stock_ohlcv_fetch_start``)을 ``services.py``로 옮겼다 —
    새로 생긴 ``collectors/stock_ohlcv_watchlist.py``(watchlist 전 종목 배치
    갱신)가 이 온디맨드 경로와 정확히 같은 로직을 필요로 해서, 두 곳에 같은
    코드를 베껴 두는 대신 한 곳으로 합쳤다. 쿨다운 dict(``_candle_fetch_
    attempted_at``)와 장중 판정은 이 함수(온디맨드 전용 관심사)에 그대로
    남는다 — 배치 잡은 하루 1회뿐이라 쿨다운이 필요 없다.
    """
    target_end = _latest_trading_day()
    existing_max = (
        await session.execute(select(func.max(StockOhlcv.date)).where(StockOhlcv.code == code))
    ).scalar_one_or_none()

    market_open = not is_nxt_closed(dt.datetime.now(KST))

    if existing_max is not None and existing_max >= target_end and not market_open:
        return  # NXT까지 마감 — 확정치, 캐시 히트

    if existing_max is not None:
        # 캐시는 있지만 오래됐거나(기존 사유) 장중이라 오늘치가 아직 잠정치인 경우 —
        # 쿨다운 확인(모듈 docstring "공휴일" 안전장치 + 장중 재조회 과호출 방지 겸용).
        now = time.monotonic()
        last_attempt = _candle_fetch_attempted_at.get(code)
        if last_attempt is not None and (now - last_attempt) < _EXTERNAL_FETCH_COOLDOWN_SECONDS:
            return  # 최근에 이미 재시도했음 — 있는 캐시로 서빙

    # 신규 코드면 전체 백필 시작일을, 기존 코드면 마지막 캐시일(top-up)을 반환한다
    # (services.plan_stock_ohlcv_fetch_start, 배치 경로와 공유하는 순수 계산).
    fetch_start = plan_stock_ohlcv_fetch_start(existing_max, target_end, days)

    _candle_fetch_attempted_at[code] = time.monotonic()
    rows = await asyncio.to_thread(naver_index.fetch_stock_series, code, fetch_start, target_end)
    await upsert_stock_ohlcv_rows(session, code, rows)


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
    """PLAN.md §5.60(2026-08-08) — 예전엔 row마다 `session.execute()`를 따로
    호출했다(즉 최대 `FLOW_BACKFILL_DAYS`(90)일치 x 투자자 13종 ≈ 최대 780개
    개별 DB 왕복). 종목 상세를 온디맨드로 한 번 여는 정도면 무해했지만,
    `collectors/accumulation_screener.py`(§5.57, 전 종목 순회)가 이 함수를
    코드마다 반복 호출하면서 실측으로 드러났다 — 2026-08-07 첫 실행이 예상
    70~90분이 아니라 3시간 35분 걸렸다. 원인을 벤치마크로 직접 재현·확인:
    같은 780행을 개별 execute로 upsert하면 1.557초, 하나의 다중값 INSERT ..
    ON CONFLICT로 배치하면 0.172초(약 9배) — 실측치를 그대로 여기 남긴다.
    동작(on_conflict_do_update 결과)은 완전히 동일하고 왕복 횟수만 준다."""
    if not rows:
        return 0
    values = [
        {
            "code": code,
            "date": row["date"],
            "investor": row["investor"],
            "net_value": row["net_value"],
            "net_volume": row["net_volume"],
        }
        for row in rows
    ]
    stmt = pg_insert(StockFlow).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[StockFlow.code, StockFlow.date, StockFlow.investor],
        set_={"net_value": stmt.excluded.net_value, "net_volume": stmt.excluded.net_volume},
    )
    await session.execute(stmt)
    return len(rows)


async def _ensure_flows_cached(session: AsyncSession, code: str) -> None:
    """stock_flow에 code의 최신 거래일 수급이 이미 있고 **장 마감**이면 외부 호출을
    생략한다(캐시 히트). 없거나, 오래됐거나, 장중인데 쿨다운이 지났으면 ka10059를
    1콜 호출해 최근 FLOW_BACKFILL_DAYS일만 잘라 upsert한다(모듈 docstring 참고 —
    1콜로 충분해 연속조회 불필요).

    2026-07-21 수정(SOT 이슈) — `_ensure_candles_cached`와 동일한 이유·동일한
    수정(위 함수 docstring 참고): 장중엔 "오늘치 있음"만으로 신선하다고 보지 않고
    쿨다운(60초)마다 재조회한다.

    Raises: KiwoomAuthError/KiwoomAPIError/httpx 예외를 그대로 전파한다 — 호출측이
    "수급만 실패면 200 + flows 빈 채로"로 흡수한다.
    """
    target_end = _latest_trading_day()
    existing_max = (
        await session.execute(select(func.max(StockFlow.date)).where(StockFlow.code == code))
    ).scalar_one_or_none()

    market_open = not is_nxt_closed(dt.datetime.now(KST))

    if existing_max is not None and existing_max >= target_end and not market_open:
        return  # 장 마감 확정치 — 캐시 히트

    if existing_max is not None:
        # 캐시는 있지만 오래됐거나(기존 사유) 장중이라 오늘치가 아직 잠정치인 경우 —
        # 쿨다운 확인(모듈 docstring "공휴일" 안전장치 + 장중 재조회 과호출 방지 겸용).
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


async def _upsert_program_trade_rows(session: AsyncSession, rows: list[dict]) -> int:
    """PLAN.md §5.60(2026-08-08) — `_upsert_flow_rows`와 동일한 배치화(row마다
    개별 `session.execute()` 대신 다중값 INSERT .. ON CONFLICT 하나로 묶는다,
    동작(on_conflict_do_update 결과)은 완전히 동일)."""
    if not rows:
        return 0
    values = [
        {
            "code": row["code"],
            "date": row["date"],
            "arb_net": row["arb_net"],
            "non_arb_net": row["non_arb_net"],
            "total_net": row["total_net"],
        }
        for row in rows
    ]
    stmt = pg_insert(ProgramTrade).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[ProgramTrade.code, ProgramTrade.date],
        set_={
            "arb_net": stmt.excluded.arb_net,
            "non_arb_net": stmt.excluded.non_arb_net,
            "total_net": stmt.excluded.total_net,
        },
    )
    await session.execute(stmt)
    return len(rows)


async def _ensure_program_trade_cached(session: AsyncSession, code: str) -> None:
    """program_trade에 code의 최신 거래일 프로그램매매가 이미 있고 **장 마감**이면
    외부 호출을 생략한다 — `_ensure_flows_cached`(종목별 수급, ka10059)와 완전히
    동일한 온디맨드+DB캐시 철학(collectors/program_stock.py 모듈 docstring
    "REGISTRY에 등록하지 않는다" 절 참고 — 새 스윕 잡 대신 이 패턴을 재사용하기로
    한 판단 근거).

    ka90013은 한 콜에 정확히 20거래일(약 1개월)만 오므로(clients/kiwoom.py
    "ka90013 실측 확정" 절) `PROGRAM_TRADE_BACKFILL_DAYS`로 자를 필요가 실질적으로
    없지만, `_ensure_flows_cached`와 동일한 형태를 유지하기 위해 그대로 둔다.

    Raises: KiwoomAuthError/KiwoomAPIError/httpx 예외를 그대로 전파한다 — 호출측이
    "프로그램매매만 실패면 200 + program_trade 빈 채로"로 흡수한다(flows_error와
    동일한 정책, meta.program_trade_error).
    """
    target_end = _latest_trading_day()
    existing_max = (
        await session.execute(
            select(func.max(ProgramTrade.date)).where(ProgramTrade.code == code)
        )
    ).scalar_one_or_none()

    market_open = not is_nxt_closed(dt.datetime.now(KST))

    if existing_max is not None and existing_max >= target_end and not market_open:
        return  # 장 마감 확정치 — 캐시 히트

    if existing_max is not None:
        now = time.monotonic()
        last_attempt = _program_trade_fetch_attempted_at.get(code)
        if last_attempt is not None and (now - last_attempt) < _EXTERNAL_FETCH_COOLDOWN_SECONDS:
            return  # 최근에 이미 재시도했음 — 있는 캐시로 서빙

    _program_trade_fetch_attempted_at[code] = time.monotonic()
    async with KiwoomClient() as client:
        data, _headers = await client.stock_program_trading(code)

    rows = parse_program_trade_rows(data, code)
    cutoff = target_end - dt.timedelta(days=PROGRAM_TRADE_BACKFILL_DAYS)
    rows = [r for r in rows if r["date"] >= cutoff]
    await _upsert_program_trade_rows(session, rows)


async def _read_program_trade(session: AsyncSession, code: str, days: int) -> list[dict]:
    """[{date, arb_net, non_arb_net, total_net}, ...] (날짜 오름차순) — ka90013이
    차익/비차익 분리를 주지 않아(모듈 docstring 참고) arb_net/non_arb_net는 항상
    None이고 total_net만 실값이다. 데이터가 전혀 없으면 빈 리스트(프런트가 섹션
    자체를 생략하는 근거로 쓴다)."""
    since = _latest_trading_day() - dt.timedelta(days=days)
    stmt = (
        select(ProgramTrade)
        .where(ProgramTrade.code == code, ProgramTrade.date >= since)
        .order_by(ProgramTrade.date)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "date": r.date.strftime("%Y%m%d"),
            "arb_net": r.arb_net,
            "non_arb_net": r.non_arb_net,
            "total_net": r.total_net,
        }
        for r in rows
    ]


def _compute_program_trade_summary(rows: list[dict]) -> dict:
    """program_trade 행 리스트(``_read_program_trade`` 출력, 날짜 오름차순)에서
    연속 순매수/순매도 일수(스트릭)와 최근 5/10거래일 누적 순매수를 계산한다 —
    사용자가 알테오젠(196170) 상세에서 지적한 "프로그램이 매수로 보이면 외인이
    산 걸로 나온다" 패턴을 요약 지표로 보여주기 위함(PLAN.md 참고).

    스트릭은 시장 전체 수급 스트릭과 동일한 판정 알고리즘인
    ``quant/regime_backtest.py::next_streak``를 그대로 재사용한다(부호가 이전과
    같으면 한 칸 더 누적, 바뀌면 ±1로 새로 시작, total_net이 None인 날은
    건너뛴다) — 새 판단 로직을 따로 만들지 않는다.

    Returns ``{"streak": int, "cumulative_net_5d": int|None, "cumulative_net_10d": int|None}``.
    - streak: rows 전체(주어진 창 안)에 next_streak을 순서대로 적용한 최종값.
      양수=연속 N일 순매수, 음수=연속 N일 순매도, 0=데이터 없음/누적 없음.
    - cumulative_net_5d/10d: 가장 최근 5/10개 행(rows 끝에서부터)의 total_net
      합계 — total_net이 전부 None이면 None, 일부만 None이면 있는 값만 합산한다.
      rows가 5/10개보다 적으면 있는 만큼만 합산한다.
    """
    streak = 0
    for row in rows:
        streak = next_streak(streak, row.get("total_net"))

    def _sum_recent(n: int) -> int | None:
        window = rows[-n:]
        values = [r["total_net"] for r in window if r.get("total_net") is not None]
        if not values:
            return None
        return sum(values)

    return {
        "streak": streak,
        "cumulative_net_5d": _sum_recent(5),
        "cumulative_net_10d": _sum_recent(10),
    }


async def _upsert_short_selling_rows(session: AsyncSession, code: str, rows: list[dict]) -> int:
    """PLAN.md §5.60(2026-08-08) — `_upsert_flow_rows`와 동일한 배치화(row마다
    개별 `session.execute()` 대신 다중값 INSERT .. ON CONFLICT 하나로 묶는다,
    동작(on_conflict_do_update 결과)은 완전히 동일)."""
    if not rows:
        return 0
    values = [
        {
            "code": code,
            "date": row["date"],
            "volume": row["volume"],
            "value": row["value"],
            "balance_qty": row["balance_qty"],
            "balance_value": row["balance_value"],
        }
        for row in rows
    ]
    stmt = pg_insert(ShortSellingStock).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[ShortSellingStock.code, ShortSellingStock.date],
        set_={
            "volume": stmt.excluded.volume,
            "value": stmt.excluded.value,
            "balance_qty": stmt.excluded.balance_qty,
            "balance_value": stmt.excluded.balance_value,
        },
    )
    await session.execute(stmt)
    return len(rows)


async def _ensure_short_selling_cached(session: AsyncSession, code: str) -> None:
    """short_selling_stock에 code의 최신 거래일 공매도 데이터가 이미 있고 **장 마감**
    이면 외부 호출을 생략한다 — `_ensure_program_trade_cached`(종목별 프로그램매매,
    ka90013)와 완전히 동일한 온디맨드+DB캐시 철학(PLAN.md §5.32-3, 소스만 키움이
    아니라 KRX 정보데이터시스템 — clients/krx_short_selling.py 모듈 docstring 참고).

    krx_short_selling 클라이언트는 requests(블로킹)라 collectors/macro.py와 동일하게
    ``asyncio.to_thread``로 감싼다.

    Raises: KrxShortSellingError/requests 예외를 그대로 전파한다 — 호출측이
    "공매도만 실패면 200 + short_selling 빈 채로"로 흡수한다(flows_error/
    program_trade_error와 동일한 정책).
    """
    target_end = _latest_trading_day()
    existing_max = (
        await session.execute(
            select(func.max(ShortSellingStock.date)).where(ShortSellingStock.code == code)
        )
    ).scalar_one_or_none()

    market_open = not is_nxt_closed(dt.datetime.now(KST))

    if existing_max is not None and existing_max >= target_end and not market_open:
        return  # 장 마감 확정치 — 캐시 히트

    if existing_max is not None:
        now = time.monotonic()
        last_attempt = _short_selling_fetch_attempted_at.get(code)
        if last_attempt is not None and (now - last_attempt) < _EXTERNAL_FETCH_COOLDOWN_SECONDS:
            return  # 최근에 이미 재시도했음 — 있는 캐시로 서빙

    _short_selling_fetch_attempted_at[code] = time.monotonic()
    start = target_end - dt.timedelta(days=SHORT_SELLING_BACKFILL_DAYS)
    rows = await asyncio.to_thread(
        krx_short_selling.fetch_stock_short_selling, code, start, target_end
    )
    await _upsert_short_selling_rows(session, code, rows)


async def _read_short_selling(session: AsyncSession, code: str, days: int) -> list[dict]:
    """[{date, volume, value, balance_qty, balance_value}, ...] (날짜 오름차순).
    데이터가 전혀 없으면 빈 리스트(프런트가 섹션 자체를 생략하는 근거로 쓴다)."""
    since = _latest_trading_day() - dt.timedelta(days=days)
    stmt = (
        select(ShortSellingStock)
        .where(ShortSellingStock.code == code, ShortSellingStock.date >= since)
        .order_by(ShortSellingStock.date)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "date": r.date.strftime("%Y%m%d"),
            "volume": r.volume,
            "value": r.value,
            "balance_qty": r.balance_qty,
            "balance_value": r.balance_value,
        }
        for r in rows
    ]


async def _read_investor_warning_status(session: AsyncSession, code: str) -> dict:
    """이 종목의 현재 투자주의/투자경고/투자위험 지정 상태(PLAN.md §5.39).
    ``investor_warning_event``는 하루 1회 배치(collectors/investor_warning.py)로
    채워지는 테이블이라 별도 온디맨드 외부 호출·캐시가 필요 없다 — DB 조회만
    한다(다른 소스들의 `_ensure_*_cached` 패턴과 달리 이 함수만 이 형태인
    이유).

    caution tier의 "활성" 판정에 필요한 전역 기준일(이 프로젝트에 있는 caution
    데이터 중 가장 최근 지정일 — quant/investor_warning_status.py 모듈 docstring
    "tier별 활성 정의가 다르다" 절 참고)을 이 종목 것과 별개로 한 번 더 조회한다."""
    stmt = select(InvestorWarningEvent).where(InvestorWarningEvent.code == code)
    rows = [
        {
            "tier": r.tier,
            "market": r.market,
            "warning_type": r.warning_type,
            "notice_date": r.notice_date,
            "designated_date": r.designated_date,
            "released_date": r.released_date,
        }
        for r in (await session.execute(stmt)).scalars().all()
    ]

    caution_as_of = (
        await session.execute(
            select(func.max(InvestorWarningEvent.designated_date)).where(
                InvestorWarningEvent.tier == "caution"
            )
        )
    ).scalar_one_or_none()

    result = classify_investor_warning_status(rows, caution_as_of=caution_as_of)
    return {
        "active_tier": result["active_tier"],
        "label": result["label"],
        "market": result["market"],
        "designated_date": (
            result["designated_date"].strftime("%Y%m%d") if result["designated_date"] else None
        ),
        "warning_type": result["warning_type"],
    }


async def _ensure_stock_master_stub(
    session: AsyncSession,
    code: str,
    name: str | None,
    market: str | None,
    is_etf: bool | None,
) -> None:
    """`stocks`에 code가 아직 없을 때 최소 정보로 stub 행을 upsert한다(PLAN.md
    §5.28) — `stock_ohlcv.code`/`stock_flow.code`가 `stocks.code`에 대한 FK라서,
    마스터에 없는 code로 `_ensure_candles_cached`/`_ensure_flows_cached`가 그대로
    INSERT를 시도하면 FK 위반으로 크래시한다(§5.28 원인 진단 — 실제로
    "에이치엘지노믹스"(0156T0)에서 502로 재현됨). 이 함수를 그 INSERT들보다
    먼저 호출해 FK가 항상 만족되도록 한다.

    호출측(프런트 `StockDetailModal`이 `initial` prop — 검색/랭킹 카드가 이미
    갖고 있던 이름/시장 정보)이 넘겨준 힌트로 최대한 정확히 채우되, 힌트가
    없으면 `name=code`, `market="KOSPI"`, `is_etf=False`로 채운다. 이 fallback은
    임의 추정값이지만 영구 오류가 아니다 — 다음 EOD 배치
    (`collectors/value_rank.py::_upsert_stock_master`)가 전 종목을 매일
    `ON CONFLICT DO UPDATE`로 다시 upsert하면서 정확한 name/market으로 자동
    교정한다.

    **ON CONFLICT DO NOTHING(중요, DO UPDATE 아님)**: `_upsert_stock_master`와
    반대 방향의 신중함이 필요하다 — 그쪽은 신뢰할 수 있는 전체 시장 소스라 매번
    최신값으로 갱신하는 게 맞지만, 여기는 "이 종목상세를 어쩌다 먼저 연 특정
    요청의 best-guess 힌트"일 뿐이다. 이미 배치가 정확히 채워둔 행이 있든,
    이 함수가 커밋하는 순간과 동시에 배치가 막 써넣은 행이든, 이 stub이 그걸
    덮어써서는 절대 안 된다 — 그래서 DO NOTHING으로 "없을 때만 채운다"만
    보장한다.

    `stocks.market`은 대문자 표기 관례(`value_rank.py`의 `MARKET_LABEL`:
    "KOSPI"/"KOSDAQ")를 따라 힌트를 대문자로 정규화한다. 커밋은 호출측
    (`stock_series`)이 기존 트랜잭션 경계에 맞춰 담당한다 — 이 함수는 실행만
    한다.
    """
    stmt = pg_insert(Stock).values(
        code=code,
        name=name or code,
        market=(market.upper() if market else "KOSPI"),
        # is_etf 힌트가 없으면(None) False로 채운다 — bool(None)이 False라는
        # 암묵적 형변환에 기대지 않고, 의도를 코드로 명시한다.
        is_etf=is_etf if is_etf is not None else False,
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=[Stock.code])
    await session.execute(stmt)


async def _read_turnover(session: AsyncSession, code: str) -> dict | None:
    """value_rank에서 이 종목의 최신 회전율(%)을 가져온다. value_rank는 거래대금
    상위 종목만 적재되므로(PLAN.md §5.16) 없는 종목은 None을 그대로 반환 —
    억지로 채우지 않는다(§5 "정직한 표시" 원칙)."""
    stmt = (
        select(ValueRank.turnover, ValueRank.date)
        .where(ValueRank.code == code)
        .order_by(ValueRank.date.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).first()
    if row is None or row.turnover is None:
        return None
    return {"value": float(row.turnover), "date": row.date.strftime("%Y%m%d")}


# quant/screener.py·routers/scalp.py::_stock_flow_lookup와 동일한 "외국인+기관계"
# 조합(PLAN.md §5.38-3) — 의도적 중복(screener.py의 LARGE_DECLINE_WARNING_PCT
# 중복 관례와 동일한 이유: 이 모듈은 다른 라우터 모듈에 의존하지 않는다).
_FLOW_PERCENTILE_INVESTORS = ("외국인", "기관계")


async def _read_flow_percentile(session: AsyncSession, code: str) -> dict | None:
    """이 종목의 수급 유니버스 상대순위(PLAN.md §5.38, quant/flow_percentile.py).

    이 종목의 최신 value_rank 스냅샷(날짜+시장)을 찾고, **같은 날 같은 시장**의
    value_rank 전체(ETF 제외 — collectors/live_refresh.py::_run_stock_flow_scan이
    애초에 ETF를 스윕 대상에서 뺘서 stock_flow에 ETF 데이터가 사실상 없다, 모듈
    docstring 참고)를 시가총액 tier로 나눠 이 종목이 그 tier 안에서 오늘 수급
    (외국인+기관계 순매수)이 몇 퍼센타일인지 계산한다. 새 DB 테이블/외부 호출
    없음 — value_rank+stock_flow 조인만 한다.

    value_rank에 이 종목이 아예 없으면(거래대금 상위 top100 밖 — 이 지표의
    스코프 한계, 모듈 docstring 참고) None을 반환한다 — `_read_turnover`와
    동일한 "억지로 채우지 않는다" 원칙. 있어도 `market_value_million`이
    없으면(§5.38-1 마이그레이션 이전 과거 행) 마찬가지로 None.

    표본 부족/이 종목의 수급 데이터 없음이면 크래시하지 않고 ``reason``이
    채워진 dict를 반환한다(quant/flow_percentile.py의 "표본 부족은 정직하게
    표시" house rule을 그대로 노출 — 프런트가 이 필드로 "계산 불가" 문구를
    표시한다).
    """
    latest_stmt = (
        select(ValueRank.date, ValueRank.market, ValueRank.market_value_million)
        .where(ValueRank.code == code)
        .order_by(ValueRank.date.desc())
        .limit(1)
    )
    latest = (await session.execute(latest_stmt)).first()
    if latest is None or latest.market_value_million is None:
        return None

    date, market = latest.date, latest.market

    peers_stmt = select(ValueRank.code, ValueRank.market_value_million).where(
        ValueRank.date == date,
        ValueRank.market == market,
        ValueRank.is_etf.is_(False),
        ValueRank.market_value_million.isnot(None),
    )
    peers = (await session.execute(peers_stmt)).all()
    if not peers:
        return {"date": date.strftime("%Y%m%d"), "market": market, "reason": "동일 날짜 시가총액 데이터가 없음"}

    peer_codes = [p.code for p in peers]
    # scalp.py::_stock_flow_lookup와 동일한 조회 패턴(재사용 아님, 이 라우터는
    # 이미 그쪽 파일을 참조하지 않는 독립 모듈이라 동일 로직만 반복).
    flow_stmt = select(StockFlow.code, StockFlow.net_value).where(
        StockFlow.code.in_(peer_codes),
        StockFlow.date == date,
        StockFlow.investor.in_(_FLOW_PERCENTILE_INVESTORS),
        StockFlow.net_value.isnot(None),
    )
    flow_rows = (await session.execute(flow_stmt)).all()
    flow_totals: dict[str, int] = {}
    for flow_code, net_value in flow_rows:
        flow_totals[flow_code] = flow_totals.get(flow_code, 0) + net_value

    rows = [
        {
            "code": p.code,
            "market": market,
            "market_value_million": p.market_value_million,
            "flow_net_value": flow_totals.get(p.code),
        }
        for p in peers
    ]
    computed = compute_flow_percentiles(rows)
    market_result = computed.get(market)
    if market_result is None or market_result["reason"] is not None:
        reason = market_result["reason"] if market_result else "계산 불가"
        return {"date": date.strftime("%Y%m%d"), "market": market, "reason": reason}

    own = next((r for r in market_result["results"] if r["code"] == code), None)
    if own is None:
        return {
            "date": date.strftime("%Y%m%d"),
            "market": market,
            "reason": "오늘 이 종목의 수급(외국인+기관계) 데이터가 아직 없음",
        }

    return {
        "date": date.strftime("%Y%m%d"),
        "market": market,
        "reason": None,
        "tier": own["tier"],
        "tier_count": market_result["tier_count"],
        "tier_size": own["tier_size"],
        "sample_size": market_result["sample_size"],
        "percentile": own["percentile"],
        "flow_net_value": own["flow_net_value"],
    }


# -- 엔드포인트 -----------------------------------------------------------------


@router.get("/{code}/series")
async def stock_series(
    code: str,
    days: int = Query(180, ge=1, le=1500),
    name: str | None = Query(
        None, description="stocks 마스터에 없을 때 stub 생성에 쓸 이름 힌트(§5.28)"
    ),
    market: str | None = Query(
        None, description="stocks 마스터에 없을 때 stub 생성에 쓸 시장 힌트(§5.28)"
    ),
    is_etf: bool | None = Query(
        None, description="stocks 마스터에 없을 때 stub 생성에 쓸 ETF 여부 힌트(§5.28)"
    ),
    include_volume_profile: bool = Query(
        False,
        description=(
            "true면 응답에 volume_profile(거래량 프로파일 근사 + 지지/저항 후보, "
            "PLAN.md §5.34)을 추가한다. 기본 false — 일반 시세 조회마다 계산 "
            "비용을 붙이지 않기 위해 옵트인으로 둔다(quant/volume_profile.py "
            "모듈 docstring 참고, 예측/매매 신호 아님)."
        ),
    ),
    include_flow_percentile: bool = Query(
        False,
        description=(
            "true면 응답에 flow_percentile(동일 시가총액 tier 내 수급 순매수 "
            "percentile, PLAN.md §5.38)을 추가한다. 기본 false — value_rank+"
            "stock_flow 조인·tier 계산 비용을 일반 조회마다 붙이지 않기 위해 "
            "옵트인으로 둔다(quant/flow_percentile.py 모듈 docstring 참고, "
            "관찰 지표일 뿐 매매 신호 아님)."
        ),
    ),
    session: AsyncSession = Depends(get_session),
):
    """종목 캔들+수급 조회. PLAN.md §5.28: `stock is None`(아직 `stocks` 마스터
    EOD 배치가 못 따라잡은 신규/이형 코드 — 실사례: "에이치엘지노믹스" 0156T0)
    이면, `stock_ohlcv`/`stock_flow`에 FK로 물려있는 `stocks.code`를 만족시키기
    위해 `_ensure_candles_cached`/`_ensure_flows_cached`를 부르기 **전에** 먼저
    stub 행을 만든다(`_ensure_stock_master_stub`) — 그렇게 안 하면 그 함수들의
    INSERT가 FK 위반으로 그대로 502 크래시한다(§5.28 원인 진단, 실제로 재현됨).
    `name`/`market`/`is_etf` 쿼리파라미터는 프런트가 이미 알고 있는 값(검색/
    랭킹 카드가 `initial` prop으로 들고 있던 정보)을 전달하기 위한 선택적
    힌트이고, 없으면 `_ensure_stock_master_stub`의 fallback(`name=code`,
    `market="KOSPI"`, `is_etf=False`)을 그대로 쓴다.
    """
    stock = await session.get(Stock, code)
    # 이름/시장/ETF 여부를 지금 바로 일반 값으로 떼어 둔다 — 아래에서 실패 시
    # session.rollback()을 호출하면 expire_on_commit 설정과 무관하게 이 ORM
    # 인스턴스가 expire되어, 나중에 stock.name에 접근하면 (동기 컨텍스트에서)
    # 지연 재조회를 시도하다 MissingGreenlet으로 죽는다 — 그걸 피하기 위함.
    # stock이 존재하면 DB 값을 그대로 쓰고(신뢰할 수 있는 SOT), stock이 없을
    # 때만 쿼리파라미터 힌트로 대체한다 — 이 값들이 그대로 응답 바디의
    # name/market/is_etf가 되므로, stub 생성 이후에도 힌트가 null로 보이지
    # 않게 하기 위함(§5.28). fallback 값은 아래 _ensure_stock_master_stub이
    # 실제로 DB에 쓰는 값과 정확히 일치시킨다(대문자 정규화 포함) — 응답이
    # DB 상태와 어긋나지 않도록.
    stock_name = stock.name if stock else (name or code)
    stock_market = stock.market if stock else (market.upper() if market else "KOSPI")
    stock_is_etf = stock.is_etf if stock else (is_etf if is_etf is not None else False)

    if stock is None:
        # FK(stock_ohlcv.code/stock_flow.code -> stocks.code)를 만족시키기 위해
        # 아래 캐시 함수들의 INSERT보다 먼저 stub을 확정한다. ON CONFLICT DO
        # NOTHING이라 동시 요청/배치와 경합해도 예외가 나지 않으므로(중복 키가
        # 조용히 no-op 처리됨) 별도 try/except 없이 바로 커밋한다 — 여기서
        # 실패할 수 있는 유일한 경우는 DB 연결 자체의 문제인데, 그건 아래
        # _ensure_candles_cached 호출도 똑같이 겪을 문제라 이 지점만 따로
        # 방어할 이유가 없다.
        await _ensure_stock_master_stub(session, code, name, market, is_etf)
        await session.commit()

    try:
        await _ensure_candles_cached(session, code, days)
        await session.commit()
    except Exception as e:  # noqa: BLE001 - naver_index.NaverIndexError / requests 등
        await session.rollback()
        raise HTTPException(
            502, detail={"source": "naver_fchart", "detail": str(e)[:300]}
        ) from e

    prices = await get_stock_series_from_db(session, code, days)

    # 거래량 프로파일(PLAN.md §5.34) — 옵트인일 때만 계산한다. 이미 위에서 읽어
    # 둔 prices(캔들 응답의 소스와 동일)를 그대로 재사용하므로 새 DB 조회/외부
    # API 호출이 전혀 없다(quant/volume_profile.py 모듈 docstring "입력 형태"
    # 참고 — get_stock_series_from_db 출력 그대로 넘길 수 있게 필드가 맞춰져 있음).
    volume_profile_result: dict | None = None
    if include_volume_profile:
        profile = compute_volume_profile(prices)
        volume_profile_result = {**profile, "levels": detect_levels(profile)}

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
    turnover = await _read_turnover(session, code)

    # 수급 유니버스 상대순위(PLAN.md §5.38) — 옵트인일 때만 계산한다. peer 종목들의
    # stock_flow는 이미 collectors/live_refresh.py::_run_stock_flow_scan(10분
    # 티어)이 채워 둔 값을 읽기만 한다(이 요청이 peer들에 대해 새로 키움을 호출하지
    # 않음 — 위 _ensure_flows_cached는 code 자기 자신만 온디맨드 갱신한다).
    flow_percentile_result: dict | None = None
    if include_flow_percentile:
        flow_percentile_result = await _read_flow_percentile(session, code)

    try:
        await _ensure_program_trade_cached(session, code)
        await session.commit()
    except (KiwoomAuthError, KiwoomAPIError) as e:
        await session.rollback()
        logger.warning("stocks: %s 프로그램매매 조회 실패(키움), program_trade 빈 채로 반환: %s", code, e)
        meta["program_trade_error"] = str(e)[:300]
    except Exception as e:  # noqa: BLE001 - httpx 등 네트워크 계열 예외 포함
        await session.rollback()
        logger.warning("stocks: %s 프로그램매매 조회 실패, program_trade 빈 채로 반환: %s", code, e)
        meta["program_trade_error"] = str(e)[:300]

    program_trade = await _read_program_trade(session, code, days)
    program_trade_summary = _compute_program_trade_summary(program_trade)

    try:
        await _ensure_short_selling_cached(session, code)
        await session.commit()
    except Exception as e:  # noqa: BLE001 - krx_short_selling.KrxShortSellingError/requests 등
        await session.rollback()
        logger.warning("stocks: %s 공매도 조회 실패, short_selling 빈 채로 반환: %s", code, e)
        meta["short_selling_error"] = str(e)[:300]

    short_selling = await _read_short_selling(session, code, days)

    investor_warning = await _read_investor_warning_status(session, code)

    return {
        "code": code,
        "name": stock_name,
        "market": stock_market,
        "is_etf": stock_is_etf,
        "days": days,
        "prices": prices,
        "flows": flows,
        "meta": meta,
        "turnover": turnover,
        "program_trade": program_trade,
        "program_trade_summary": program_trade_summary,
        "short_selling": short_selling,
        "investor_warning": investor_warning,
        **({"volume_profile": volume_profile_result} if include_volume_profile else {}),
        **({"flow_percentile": flow_percentile_result} if include_flow_percentile else {}),
    }


def _serialize_volume_profile_row(r: VolumeProfileDaily) -> dict:
    return {
        "date": r.date.isoformat(),
        "poc_price": float(r.poc_price) if r.poc_price is not None else None,
        "levels": r.levels if r.levels is not None else [],
        "bar_count": r.bar_count,
        "total_volume": float(r.total_volume) if r.total_volume is not None else None,
        "lookback_days": r.lookback_days,
    }


@router.get("/{code}/volume-profile-history")
async def stock_volume_profile_history(
    code: str,
    days: int = Query(90, ge=1, le=400),
    session: AsyncSession = Depends(get_session),
):
    """`volume_profile_daily`(collectors/volume_profile_snapshot.py가 watchlist
    종목에 대해 매일 적재하는 §5.34 거래량 프로파일 스냅샷, PLAN.md §5.35-4)의
    누적 시계열을 그대로 반환한다 — **재계산 아님**, 순수 조회
    (routers/markets.py::market_volume_profile_history의 종목판, 동일한 house
    rule: 추이를 그대로 노출할 뿐 판정을 추가하지 않는다).

    watchlist에 없거나 아직 배치가 한 번도 안 돈 종목은 빈 ``series``를 그대로
    반환한다(에러 아님 — "아직 스냅샷이 없다"와 "조회 실패"는 다른 상태)."""
    since = dt.date.today() - dt.timedelta(days=days)
    stmt = (
        select(VolumeProfileDaily)
        .where(
            VolumeProfileDaily.entity_type == "stock",
            VolumeProfileDaily.entity_code == code,
            VolumeProfileDaily.date >= since,
        )
        .order_by(VolumeProfileDaily.date)
    )
    rows = (await session.execute(stmt)).scalars().all()

    return {
        "code": code,
        "days": days,
        "series": [_serialize_volume_profile_row(r) for r in rows],
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


# -- 진입 타이밍 시그널 (PLAN.md §5.3 — /{code}/intraday 재사용 + quant/signals.py) ----
#
# 계산 자체는 quant/signals.py의 순수 함수(DB/네트워크 무관, 단위테스트로 검증)에
# 위임하고, 이 핸들러는 "오늘 분봉을 가져와 넘기고 응답을 조립"만 한다(위 intraday
# 핸들러와 동일한 캐시를 그대로 재사용 — 같은 (code, interval)을 반복 호출해도
# 키움을 다시 부르지 않는다).
#
# **원칙(모듈 docstring·PLAN.md §5 전체): 전부 관찰 서술이다.** 아래 응답의 어떤
# 필드도 "사라/팔아라" 판단을 담지 않는다 — breakout.direction, ma_cross.state 등은
# 전부 "지금까지 관측된 사실"이며, 프런트도 이를 배지로만 노출하고 지시문은
# "참고용 기술적 관찰 — 매매 신호 아님" 한 줄만 고정 표시한다(§5.3 UI 지시).

_MOMENTUM_TARGET_MINUTES = 5  # "최근 5분 수익률" — PLAN.md §5.3 예시 그대로 고정.


@router.get("/{code}/signals")
async def stock_signals(code: str, interval: int = Query(1, description="분봉 간격(분)")):
    """분봉 기반 진입 타이밍 시그널(관찰 서술, 매매 지시 아님) — PLAN.md §5.3.

    intraday(위 `/{code}/intraday`)와 같은 캐시를 재사용해 "오늘" 분봉을 가져온
    뒤 quant/signals.py의 5개 순수 함수로 계산한다. `interval` 기본값은 1분
    (가장 세밀한 시그널), 허용값은 intraday와 동일(`MINUTE_CHART_INTERVALS`).

    Returns ``{"code", "interval", "computed_at": iso8601,
    "vwap": {"value", "deviation_pct"},
    "breakout": {"direction": "high"|"low"|"none"},
    "ma_cross": {"state": "golden"|"dead"|"none", "short_ma", "long_ma"},
    "volume_spike": {"zscore", "is_spike", "ratio"},
    "momentum": {"return_pct", "window_minutes"}}``. 분봉이 없거나(장 시작
    직전 등) 지표별로 계산에 필요한 봉 수가 모자라면 해당 필드는 None/`"none"`
    (500이 아니라 "아직 계산 불가"를 그대로 전달).
    """
    if interval not in MINUTE_CHART_INTERVALS:
        raise HTTPException(
            400, f"interval must be one of {sorted(MINUTE_CHART_INTERVALS)}"
        )
    intraday = await _warm_stock_intraday(code, interval)
    bars = intraday["bars"]

    # "5분 모멘텀"을 분봉 개수로 환산 — interval이 5보다 크면(예: 60분봉) 최소 1봉.
    window_bars = max(1, round(_MOMENTUM_TARGET_MINUTES / interval))
    mom = momentum(bars, window_bars=window_bars)

    return {
        "code": code,
        "interval": interval,
        "computed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "vwap": compute_vwap(bars),
        "breakout": detect_breakout(bars),
        "ma_cross": moving_average_cross(bars),
        "volume_spike": volume_spike(bars),
        "momentum": {
            "return_pct": mom["return_pct"],
            "window_minutes": mom["window_bars"] * interval,
        },
    }
