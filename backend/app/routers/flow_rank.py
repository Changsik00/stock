"""GET /api/markets/flow-rank — 투자자별 순매수/순매도 상위 종목 스냅샷 (PLAN.md §4.5/§6 3.5-2b).
GET /api/markets/flow-path — ETF look-through 수급 경로 분해 상위, direction=in(유입)/
out(유출) 토글 (PLAN.md §4.5/§6 3.5-3, 유출 확장은 §4.6 3.6-4).
GET /api/markets/value-rank — 거래대금 상위 종목("돈이 모이는 곳") 스냅샷 (PLAN.md §4.6 3.6-1).
GET /api/markets/sentiment — 시장 종합 매수세/매도세 게이지(-100~+100) (PLAN.md §4.6 3.6-4).

DB 전용 조회다(§5.4 "DB 캐싱 우선") — collectors/flow_rank.py·collectors/flow_path.py가
미리 적재해 둔 테이블을 그대로 읽어 반환할 뿐, 이 라우터에서 네이버를 직접 호출하지
않는다.

flow-rank는 날짜별로 묶어 반환한다(최근 날짜 먼저) — flow_rank는 소스 제약상
(naver_rank.py docstring 참고) 하루 배치당 최근 2거래일만 채워지므로, days로 조회
가능한 날짜 수는 실제로는 배치를 며칠 반복 실행한 누적分만큼이다. side 파라미터
(buy/sell, 기본 buy)로 순매수/순매도 랭킹을 고른다 — 기본값이 buy라 side를 안 주는
기존 호출자는 그대로 동작한다(하위호환). 각 row의 net_value/quantity는 항상 양수
(크기)이고 어느 방향인지는 side로만 구분한다(models.py FlowRank docstring 참고).

flow-path는 side 파라미터가 없다 — collectors/flow_path.py가 direct_net을 계산할 때
이미 side='buy'(순매수) 행만 쓰도록 고정했으므로(순매도까지 합치면 "직접 순매수"의
의미가 사라짐) 이 핸들러 자체는 변경하지 않는다. days 창 안에서 가장 최근 날짜
하나만 골라(flow_rank와 달리 날짜별 비교 UI가 아직 없음) via_etf_net 정렬 상위
limit개를 반환한다 — direction="in"(기본값, 하위호환)이면 기존 그대로 내림차순
(유입 상위), direction="out"이면 via_etf_net < 0인 행만 오름차순(가장 큰 음수=가장
큰 유출이 1등)으로 정렬한다(§4.6 3.6-4 "ETF 경유 유출 상위 병기").

sentiment는 market_breadth(등락 비율)·flow_rank(외인+기관 순매수/순매도 상위 합)·
etf_stats(ETF 순유입 합 ÷ AUM 합) 세 요소를 app/sentiment.py의 순수 함수로 가중평균한
근사 게이지다(§4.6 한계: 상위 랭킹·ETF 유니버스 기반 근사치, 시장 전체 정밀값 아님).
세 요소는 서로 다른 테이블이라 "가장 최근 가용 날짜"가 어긋날 수 있다 — 그대로 두고
응답에 요소별 date를 그대로 노출해 투명하게 밝힌다.

## GET /api/markets/value-rank/live (PLAN.md §4.7 3단 갱신 주기, 2026-07-20 장중 실측)

장중 실측 결과 quantTop 누적거래대금이 장중에 계속 갱신됨을 확인해 5~10분 캐시로
편입했다 — DB(value_rank)는 여전히 collectors/value_rank.py 일별 배치가 담당하고,
이 엔드포인트는 clients/naver_value_rank.py를 직접 온디맨드 재조회해 **메모리
캐시**로만 감싼다(§3.5 원칙 — DB에 안 씀). breadth/live(routers/markets.py)와
동일한 warm 함수 + TTL + Lock 패턴이다.

value-rank/live는 EOD 배치와 동일하게 시장 전 종목을 완주(코스피 ~2,478개+코스닥
~1,821개, naver_value_rank.py 모듈 docstring)해야 정확한 순위가 나와 호출당
15~30초가 걸린다 — 5~10분 인터벌 잡이 미리 채워두므로 사용자 요청은 대개 캐시
히트다(캐시 미스일 때만 그 요청이 오래 걸린다, breadth/live 등 기존 라이브
엔드포인트와 동일한 트레이드오프). turnover는 quantTop 응답에 시가총액이 이미
포함돼 있어 EOD와 동일하게 계산한다.

**flow-rank/live는 만들지 않는다(2026-07-20 장중 실측 근거)**: sise_deal_rank_iframe
소스를 09:22·09:31 KST(둘 다 오늘 2026-07-20 장중) 두 차례 직접 재호출했지만 두
번 다 "최근 2거래일"이 2026-07-15/07-16으로 고정돼 있었다 — 금요일(07-17)과
오늘(07-20, 진행 중인 세션)이 전혀 반영되지 않는다. DB(flow_rank) 최신 날짜도
동일하게 07-16에 멈춰 있어(배치가 여러 번 재실행돼도 소스 자체가 최소 2영업일
이상 지연) 우연한 샘플링이 아니라 이 소스 고유의 지연이다. 5~10분 주기로 다시
불러도 "가장 최근"이 그대로 며칠 전 값이라 실시간화의 의미가 없으므로 PLAN.md
§4.7 표대로 **1일 배치(EOD `/api/markets/flow-rank`)만 유지**하고 live 엔드포인트는
추가하지 않는다.

## 키움 TR(ka10065/ka90009) 대체 재검토 — 역시 채택 안 함 (2026-07-21 장중 실측)

네이버 소스의 지연 문제(위 절)를 우회하고자 다른 라이브 카드(attention 등)처럼
키움 TR로 대체할 수 있는지 재검토했다. GitHub `younghwan91/kiwoom-rest-api`
`domestic/ranking.py`(`/api/dostk/rkinfo`)에서 후보 2개를 찾아 실전 키로
직접 실호출:

- **`ka10065`(장중투자자별매매상위요청)**: `{"trde_tp": "1", "mrkt_tp": "000",
  "orgn_tp": "9000"}`로 호출하면 `opmr_invsr_trde_upper` 배열(100행)이 오지만
  **수량(`sel_qty`/`buy_qty`/`netslmt`)만 있고 금액 필드가 없다** — 정렬 기준도
  금액이 아니라서 실호출 1위가 시가총액이 작은 흥아해운(순매수 582천주)이었다.
  "외국인 순매수 상위"라는 카드 의미(금액 기준)와 맞지 않아 애초에 부적합
  판정, 장중 갱신 여부는 별도로 확인하지 않았다.
- **`ka90009`(외국인기관매매상위요청)**: `{"mrkt_tp": "000", "amt_qty_tp": "0",
  "qry_dt_tp": "0", "date": <오늘>, "stex_tp": "3"}`로 호출하면
  `frgnr_orgn_trde_upper` 배열(25행)에 외국인 순매도/순매수·기관 순매도/순매수
  4개 랭킹이 병렬 컬럼(`for_netslmt_*`/`for_netprps_*`/`orgn_netslmt_*`/
  `orgn_netprps_*`)으로 온다 — 기존 flow-rank의 investor x side 2x2 토글에
  정확히 대응되고 금액(백만원) 단위도 EOD와 동일해 설계상으로는 이상적이었다.
  **그러나 장중 갱신 실측에서 탈락**: 09:44:59~09:51:00 KST(2026-07-21, 90초
  간격 5회, 총 6분+) 동일 파라미터로 반복 호출했지만 상위 3종목(SK하이닉스
  42264/삼성전자 35624/KODEX 200 11619, 백만원)이 **바이트 단위로 완전히
  동일**했다 — 같은 시간대에 `GET /api/markets/attention`(ka00198)은 75초
  간격으로 재호출했을 때 상위 3종목 등락률이 실제로 바뀌었으므로(장이 멈춰서가
  아니라는 대조군 확인), ka90009 자체가 장중 실시간 갱신을 하지 않는 것으로
  판정. `date` 파라미터도 오늘/어제/생략 세 경우 응답이 완전히 동일해(무시됨)
  이 TR이 애초에 "현재 시각 스냅샷"이 아니라 훨씬 드물게(또는 일 1회) 갱신되는
  소스로 추정된다.

**결론(2026-07-21)**: 두 후보 모두 부적합(ka10065=금액 필드 없음/정렬 기준
불일치, ka90009=필드는 이상적이나 장중 미갱신 실측 확인) — flow-rank/live는
여전히 추가하지 않는다. `clients/kiwoom.py`에 두 TR을 `TR_RESOURCE_URL`에
등록하고 `foreign_institution_trading_top()`(ka90009) 편의 메서드를 남겨
뒀다(ka10063/ka10066과 동일한 관례 — "탐색했으나 미채택" 근거를 코드에 보존).
프런트 "수급 상위" 카드는 그대로 네이버 EOD 소스 + "확정 MM-DD" 라벨을 유지한다.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import naver_rank, naver_value_rank
from ..db import get_session
from ..market_hours import KST, is_market_closed
from ..models import EtfStat, FlowPath, FlowRank, MarketBreadth, Stock, ValueRank
from ..sentiment import breadth_score, compute_sentiment, etf_score, flow_score
from .markets import _warm_breadth_live

router = APIRouter(tags=["markets"])

INVESTORS = {"foreign", "institution"}
SIDES = {"buy", "sell"}
MARKET_FILTERS = {"all", "kospi", "kosdaq"}
FLOW_PATH_DIRECTIONS = {"in", "out"}
# sentiment 요소별 원재료를 찾을 때 "가장 최근 가용 날짜"를 얼마나 과거까지 훑을지.
SENTIMENT_LOOKBACK_DAYS = 30

# 5~10분 장중 라이브 캐시 TTL — collectors/live_refresh.py 신규 인터벌 잡과 맞춘다.
LIVE_TTL_SECONDS = 420  # 7분

_value_rank_live_cache: dict[str, object] = {"ts": 0.0, "data": None}
_value_rank_live_cache_lock = asyncio.Lock()

# 라이브는 EOD보다 서버 부담을 낮추려 요청 간 지연을 조금 줄인다(EOD 0.5초 —
# collectors/value_rank.py는 배치라 시간 제약이 느슨하지만, 이 라이브 경로는
# 5~10분마다 반복 호출되므로 총 소요 시간을 줄이는 쪽을 택했다).
LIVE_REQUEST_DELAY_SECONDS = 0.3


@router.get("/api/markets/flow-rank")
async def flow_rank_series(
    investor: str = Query("foreign", description="foreign 또는 institution"),
    side: str = Query("buy", description="buy(순매수) 또는 sell(순매도) — 기본 buy로 하위호환 유지"),
    days: int = Query(1, ge=1, le=30),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if investor not in INVESTORS:
        raise HTTPException(400, f"investor must be one of {sorted(INVESTORS)}")
    if side not in SIDES:
        raise HTTPException(400, f"side must be one of {sorted(SIDES)}")

    since = dt.date.today() - dt.timedelta(days=days)
    stmt = (
        select(FlowRank)
        .where(FlowRank.investor == investor, FlowRank.side == side, FlowRank.date >= since)
        .order_by(FlowRank.date.desc(), FlowRank.rank.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()

    dates: dict[str, list[dict]] = {}
    for r in rows:
        iso = r.date.isoformat()
        dates.setdefault(iso, []).append(
            {
                "rank": r.rank,
                "code": r.code,
                "name": r.name,
                "net_value": r.net_value,
                "quantity": r.quantity,
                "turnover": float(r.turnover) if r.turnover is not None else None,
                "is_etf": r.is_etf,
                # §4.6 3.6-1: 2026-07-18부터 적재되는 nullable 컬럼(collectors/flow_rank.py
                # 참고) — 그 이전 적재분은 market이 NULL로 온다.
                "market": r.market,
            }
        )

    return {
        "investor": investor,
        "side": side,
        "days": days,
        "dates": [{"date": iso, "rows": entries} for iso, entries in dates.items()],
    }


@router.get("/api/markets/flow-path")
async def flow_path_top(
    days: int = Query(7, ge=1, le=90, description="이 창 안의 가장 최근 flow_path.date만 사용"),
    limit: int = Query(30, ge=1, le=100),
    direction: str = Query(
        "in", description="in(ETF 경유 유입 상위, 기본값·하위호환) 또는 out(유출 상위)"
    ),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """via_etf_net(ETF 경유 유입/유출) 상위 종목 — collectors/flow_path.py가 적재한
    flow_path 중 days 창 안의 가장 최근 날짜 하나를 골라 반환한다. 날짜가 하나도
    없으면(배치 미실행) rows는 빈 배열.

    direction="in"(기본값)은 기존 동작 그대로다(하위호환 — 이 분기는 절대 바꾸지
    않는다): via_etf_net 내림차순 상위 limit개. direction="out"(§4.6 3.6-4)은
    via_etf_net < 0인 행만 오름차순(가장 큰 음수=가장 큰 유출이 1등)으로 정렬해
    상위 limit개를 반환한다.

    이름 해석 순서: (1) stocks 테이블(2026-07-18부터 collectors/value_rank.py가
    코스피+코스닥 전 종목(~4,000+)을 이름 포함으로 upsert하므로 사실상 이 1순위에서
    대부분 해결된다 — 그 전에는 collectors/etf_master.py가 적재하는 ETF ~300개만
    있어서 나머지 종목은 code가 그대로 노출되는 버그가 있었다) -> (2) flow_rank
    (날짜 무관 가장 최근 관측치 — stocks에 아직 없는 신규/이례적 코드에 대한
    폴백, PLAN.md §4.5 지시 "flow_rank name 활용") -> (3) 그래도 없으면 code
    그대로. top_etfs는 collectors/flow_path.py가 이미 상위 5개로 잘라 저장해
    두었으므로 여기서는 그대로 내려준다. flow_path 행에는 ETF 코드가 남지 않는다
    (collectors/flow_path.py의 1단계 재귀 분해 + 최종 result 단계 ETF 코드 제외
    — PLAN.md §4.5 한계 (b) 2026-07-18 해결).
    """
    if direction not in FLOW_PATH_DIRECTIONS:
        raise HTTPException(400, f"direction must be one of {sorted(FLOW_PATH_DIRECTIONS)}")

    since = dt.date.today() - dt.timedelta(days=days)
    latest_date = (
        await session.execute(select(func.max(FlowPath.date)).where(FlowPath.date >= since))
    ).scalar()

    if latest_date is None:
        return {"date": None, "days": days, "direction": direction, "rows": []}

    if direction == "in":
        stmt = (
            select(FlowPath)
            .where(FlowPath.date == latest_date)
            .order_by(FlowPath.via_etf_net.desc())
            .limit(limit)
        )
    else:
        stmt = (
            select(FlowPath)
            .where(FlowPath.date == latest_date, FlowPath.via_etf_net < 0)
            .order_by(FlowPath.via_etf_net.asc())
            .limit(limit)
        )
    rows = (await session.execute(stmt)).scalars().all()

    codes = [r.code for r in rows]
    name_map: dict[str, str] = {}
    if codes:
        name_rows = (
            await session.execute(select(Stock.code, Stock.name).where(Stock.code.in_(codes)))
        ).all()
        name_map = dict(name_rows)

        missing = [c for c in codes if c not in name_map]
        if missing:
            # flow_rank는 날짜별 스냅샷이라 같은 code가 여러 날짜에 걸쳐 나타날 수
            # 있다 -> 가장 최근 날짜의 이름을 쓴다(rank 오름차순은 무관, date desc만).
            fr_rows = (
                await session.execute(
                    select(FlowRank.code, FlowRank.name, FlowRank.date)
                    .where(FlowRank.code.in_(missing), FlowRank.name.isnot(None))
                    .order_by(FlowRank.date.desc())
                )
            ).all()
            for code, name, _date in fr_rows:
                name_map.setdefault(code, name)

    return {
        "date": latest_date.isoformat(),
        "days": days,
        "direction": direction,
        "rows": [
            {
                "code": r.code,
                "name": name_map.get(r.code, r.code),
                "direct_net": r.direct_net,
                "via_etf_net": r.via_etf_net,
                "top_etfs": r.top_etfs or [],
            }
            for r in rows
        ],
    }


@router.get("/api/markets/value-rank")
async def value_rank_top(
    market: str = Query(
        "all", description="kospi/kosdaq/all(코스피+코스닥을 합쳐 거래대금 내림차순으로 재정렬)"
    ),
    days: int = Query(7, ge=1, le=90, description="이 창 안의 가장 최근 value_rank.date만 사용"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """거래대금 상위 종목("돈이 모이는 곳") 스냅샷 — collectors/value_rank.py가
    적재한 value_rank 중 days 창 안의 가장 최근 날짜 하나를 골라 반환한다
    (flow-path 핸들러와 동일 패턴: value_rank도 날짜별 비교 UI가 아직 없는
    단일 스냅샷 표라 days는 "얼마나 과거까지 최근 날짜를 찾아볼지"에만 쓰인다).

    market="all"일 때는 코스피+코스닥 저장된 상위 종목(각 시장 최대 100개,
    collectors/value_rank.py TOP_N)을 합쳐 거래대금(value) 내림차순으로 다시
    정렬하고 새 "표시 순위" 1..N을 매긴다 — 원본 market별 rank와 다를 수 있다
    (collectors/flow_rank.py가 코스피+코스닥을 합칠 때와 동일한 설계 결정).
    market="kospi"/"kosdaq"이면 저장된 시장별 rank를 그대로 쓴다.
    """
    if market not in MARKET_FILTERS:
        raise HTTPException(400, f"market must be one of {sorted(MARKET_FILTERS)}")

    since = dt.date.today() - dt.timedelta(days=days)
    market_clause = [] if market == "all" else [ValueRank.market == market]

    latest_date = (
        await session.execute(
            select(func.max(ValueRank.date)).where(ValueRank.date >= since, *market_clause)
        )
    ).scalar()

    if latest_date is None:
        return {"market": market, "date": None, "days": days, "rows": []}

    stmt = (
        select(ValueRank)
        .where(ValueRank.date == latest_date, *market_clause)
        .order_by(ValueRank.market.asc(), ValueRank.rank.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()

    if market == "all":
        rows = sorted(rows, key=lambda r: r.value if r.value is not None else -1, reverse=True)
        row_ranks = enumerate(rows, start=1)
    else:
        row_ranks = ((r.rank, r) for r in rows)

    return {
        "market": market,
        "date": latest_date.isoformat(),
        "days": days,
        "rows": [
            {
                "rank": rank,
                "market": r.market,
                "code": r.code,
                "name": r.name,
                "value": r.value,
                "change_rate": float(r.change_rate) if r.change_rate is not None else None,
                "is_etf": r.is_etf,
                "turnover": float(r.turnover) if r.turnover is not None else None,
            }
            for rank, r in row_ranks
        ],
    }


async def _load_breadth_component(session: AsyncSession) -> dict:
    """market_breadth DB(EOD 확정치) 기준 breadth 요소 — market_sentiment의
    폴백 경로(라이브를 못 쓸 때: 장 마감이거나 breadth/live 자체가 실패)다.
    §5.5-4 이전에는 이 함수가 유일한 경로였다."""
    since = dt.date.today() - dt.timedelta(days=SENTIMENT_LOOKBACK_DAYS)
    latest_date = (
        await session.execute(
            select(func.max(MarketBreadth.date)).where(MarketBreadth.date >= since)
        )
    ).scalar()

    if latest_date is None:
        return {"score": None, "date": None, "adv": 0, "dec": 0, "flat": 0, "source": "eod"}

    rows = (
        await session.execute(select(MarketBreadth).where(MarketBreadth.date == latest_date))
    ).scalars().all()
    adv = sum(r.adv or 0 for r in rows)
    dec = sum(r.dec or 0 for r in rows)
    flat = sum(r.flat or 0 for r in rows)

    return {
        "score": breadth_score(adv, dec, flat),
        "date": latest_date.isoformat(),
        "adv": adv,
        "dec": dec,
        "flat": flat,
        "source": "eod",
    }


async def _load_breadth_component_live(session: AsyncSession) -> dict | None:
    """breadth/live(routers.markets._warm_breadth_live, 1분 캐시)를 재사용해
    라이브 adv/dec/flat으로 breadth 요소를 계산한다(PLAN.md §5.5-4 — 게이지가
    오늘 등락을 반영하도록). 장 마감이거나 코스피/코스닥 라이브 조회가 둘 다
    실패하면 None을 반환해 호출자(market_sentiment)가 EOD 폴백
    (`_load_breadth_component`)으로 넘어가게 한다 — **완전 대체가 아니라
    "가능하면 라이브 우선" 원칙**(flow/etf 요소는 이번 범위 밖, 그대로 EOD)."""
    live = await _warm_breadth_live(session)
    if live.get("market_closed"):
        return None
    kospi = live.get("kospi")
    kosdaq = live.get("kosdaq")
    if kospi is None and kosdaq is None:
        return None

    adv = (kospi or {}).get("adv", 0) + (kosdaq or {}).get("adv", 0)
    dec = (kospi or {}).get("dec", 0) + (kosdaq or {}).get("dec", 0)
    flat = (kospi or {}).get("flat", 0) + (kosdaq or {}).get("flat", 0)

    return {
        "score": breadth_score(adv, dec, flat),
        "date": dt.datetime.now(KST).date().isoformat(),
        "adv": adv,
        "dec": dec,
        "flat": flat,
        "source": "live",
    }


async def _load_flow_component(session: AsyncSession) -> dict:
    since = dt.date.today() - dt.timedelta(days=SENTIMENT_LOOKBACK_DAYS)
    latest_date = (
        await session.execute(
            select(func.max(FlowRank.date)).where(
                FlowRank.investor.in_(INVESTORS), FlowRank.date >= since
            )
        )
    ).scalar()

    if latest_date is None:
        return {"score": None, "date": None, "buy_sum": 0, "sell_sum": 0}

    # Postgres SUM(bigint) -> numeric(Decimal), not bigint — cast back to int so this
    # mixes cleanly with app.sentiment's float arithmetic (compute_sentiment does
    # raw_score * weight, and Decimal * float raises TypeError).
    buy_sum = int(
        (
            await session.execute(
                select(func.sum(FlowRank.net_value)).where(
                    FlowRank.date == latest_date,
                    FlowRank.investor.in_(INVESTORS),
                    FlowRank.side == "buy",
                )
            )
        ).scalar()
        or 0
    )
    sell_sum = int(
        (
            await session.execute(
                select(func.sum(FlowRank.net_value)).where(
                    FlowRank.date == latest_date,
                    FlowRank.investor.in_(INVESTORS),
                    FlowRank.side == "sell",
                )
            )
        ).scalar()
        or 0
    )

    return {
        "score": flow_score(buy_sum, sell_sum),
        "date": latest_date.isoformat(),
        "buy_sum": buy_sum,
        "sell_sum": sell_sum,
    }


async def _load_etf_component(session: AsyncSession) -> dict:
    since = dt.date.today() - dt.timedelta(days=SENTIMENT_LOOKBACK_DAYS)
    latest_date = (
        await session.execute(
            select(func.max(EtfStat.date)).where(
                EtfStat.net_inflow.isnot(None), EtfStat.date >= since
            )
        )
    ).scalar()

    if latest_date is None:
        return {"score": None, "date": None, "net_inflow_sum": 0, "aum_sum": 0}

    # (see buy_sum/sell_sum comment above — same Postgres numeric->Decimal cast issue)
    net_inflow_sum = int(
        (
            await session.execute(
                select(func.sum(EtfStat.net_inflow)).where(
                    EtfStat.date == latest_date, EtfStat.net_inflow.isnot(None)
                )
            )
        ).scalar()
        or 0
    )
    aum_sum = int(
        (
            await session.execute(
                select(func.sum(EtfStat.aum)).where(
                    EtfStat.date == latest_date, EtfStat.aum.isnot(None)
                )
            )
        ).scalar()
        or 0
    )

    return {
        "score": etf_score(net_inflow_sum, aum_sum),
        "date": latest_date.isoformat(),
        "net_inflow_sum": net_inflow_sum,
        "aum_sum": aum_sum,
    }


@router.get("/api/markets/sentiment")
async def market_sentiment(session: AsyncSession = Depends(get_session)) -> dict:
    """시장 종합 매수세/매도세 게이지(-100~+100) (PLAN.md §4.6 3.6-4).

    breadth(market_breadth 등락 비율)·flow(flow_rank 외인+기관 순매수/순매도 상위 합)·
    etf(etf_stats 순유입 합 ÷ AUM 합) 세 요소를 app/sentiment.py의 순수 함수로 가중평균
    한다. 각 요소는 서로 다른 테이블이라 "가장 최근 가용 날짜"를 독립적으로 찾으므로
    날짜가 어긋날 수 있다 — 그대로 두고 components[*].date에 그대로 노출한다(투명성).
    요소 하나라도 데이터가 없으면(None) 나머지 요소로 가중치를 재정규화한다
    (compute_sentiment 참고). 셋 다 없으면 score도 None.

    approx=True는 항상 고정값이다 — 이 프로젝트의 flow/etf 요소는 상위 랭킹·ETF
    유니버스 기반 근사치이지 시장 전체 정밀값이 아니다(§4.6 한계 절, 정밀값은 향후
    KRX/KIS market_flow 연동 후 대체 예정).

    **breadth 요소는 2026-07-21(PLAN.md §5.5-4)부터 라이브를 우선한다**: 장중이고
    breadth/live(routers.markets._warm_breadth_live, 1분 캐시) 조회가 성공하면 그
    adv/dec/flat으로 계산하고(``components.breadth.source == "live"``), 장
    마감이거나 라이브가 실패하면 기존 market_breadth DB EOD 확정치로 폴백한다
    (``source == "eod"``) — 완전 대체가 아니라 우선순위 추가다. flow(flow_rank)·
    etf(etf_stats) 요소는 이번 범위 밖이라 그대로 EOD 전용이다(flow_rank는 §4.7-4에서
    라이브가 부적합 판정된 상태).
    """
    breadth = await _load_breadth_component_live(session)
    if breadth is None:
        breadth = await _load_breadth_component(session)
    flow = await _load_flow_component(session)
    etf = await _load_etf_component(session)

    score, weights = compute_sentiment(breadth["score"], flow["score"], etf["score"])

    return {
        "score": score,
        "approx": True,
        "components": {
            "breadth": {"weight": weights["breadth"], **breadth},
            "flow": {"weight": weights["flow"], **flow},
            "etf": {"weight": weights["etf"], **etf},
        },
    }


# ---------------------------------------------------------------------------
# value-rank/live — 모듈 docstring "GET /api/markets/value-rank/live" 절 참고
# (PLAN.md §4.7, 2026-07-20).
# ---------------------------------------------------------------------------


def _fetch_value_rank_market_blocking(market: str) -> dict:
    return naver_value_rank.fetch_all(market, sleep_seconds=LIVE_REQUEST_DELAY_SECONDS)


def _fetch_etf_codes_blocking() -> set[str]:
    return naver_rank.fetch_etf_codes()


async def _warm_value_rank_live() -> dict:
    """value-rank/live 캐시를 채우고 payload를 반환한다 — 라우트 핸들러와
    collectors/live_refresh.py의 5~10분 인터벌 잡이 공유한다. 코스피+코스닥을
    합쳐 거래대금 내림차순으로 재정렬한다(EOD `/value-rank?market=all`과 동일한
    관례, collectors/value_rank.py TOP_N=100과 맞춰 시장당 상위 100개만 담는다).

    장 마감이면(``is_market_closed``) 네이버를 아예 호출하지 않는다(2026-07-20,
    신규 5~10분 티어 전체의 기본 원칙) — DB 폴백이 없으므로 마지막 캐시(있으면)를
    ``market_closed: true``로 재사용하고, 캐시조차 없으면 빈 값으로 응답한다."""
    now = time.monotonic()
    async with _value_rank_live_cache_lock:
        cached = _value_rank_live_cache["data"]
        if cached is not None and (now - _value_rank_live_cache["ts"]) < LIVE_TTL_SECONDS:
            return cached

        now_kst = dt.datetime.now(KST)
        if is_market_closed(now_kst):
            if cached is not None:
                payload = {**cached, "market_closed": True}
            else:
                payload = {
                    "date": None,
                    "rows": [],
                    "market_closed": True,
                    "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                }
            _value_rank_live_cache["data"] = payload
            _value_rank_live_cache["ts"] = now
            return payload

        try:
            etf_codes = await asyncio.to_thread(_fetch_etf_codes_blocking)
        except Exception:  # noqa: BLE001 - ETF 태깅 실패는 치명적이지 않다(전부 False로 남을 뿐)
            etf_codes = set()

        rows_all: list[dict] = []
        errors: dict[str, str] = {}
        date_seen: dt.date | None = None
        for market in ("kospi", "kosdaq"):
            try:
                result = await asyncio.to_thread(_fetch_value_rank_market_blocking, market)
            except Exception as e:  # noqa: BLE001
                errors[market] = str(e)[:200]
                continue
            date_seen = date_seen or result.get("date")
            for row in result["rows"][:100]:
                value = row.get("value_million")
                market_value = row.get("market_value_million")
                turnover = round(value / market_value * 100, 4) if value is not None and market_value else None
                rows_all.append(
                    {
                        "market": market,
                        "code": row["code"],
                        "name": row.get("name"),
                        "value": value,
                        "change_rate": row.get("change_rate"),
                        "is_etf": row["code"] in etf_codes,
                        "turnover": turnover,
                    }
                )

        if not rows_all:
            raise HTTPException(502, f"value-rank live fetch failed: {errors}")

        rows_all.sort(key=lambda r: r["value"] if r["value"] is not None else -1, reverse=True)
        for i, row in enumerate(rows_all, start=1):
            row["rank"] = i

        payload = {
            "date": date_seen.isoformat() if date_seen else None,
            "rows": rows_all,
            "market_closed": False,
            "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        _value_rank_live_cache["data"] = payload
        _value_rank_live_cache["ts"] = now
        return payload


@router.get("/api/markets/value-rank/live")
async def value_rank_live() -> dict:
    """거래대금 상위 종목 장중 라이브(PLAN.md §4.7, 2026-07-20 실측 편입).

    코스피+코스닥 전 종목을 온디맨드로 재조회해 7분 메모리 캐시로 감싼다(모듈
    docstring 참고). EOD `/api/markets/value-rank`와 달리 market 파라미터는 없다
    (전체 통합만 제공 — 화면도 항상 "전체" 기준으로 쓴다). **장 마감이면 네이버
    호출을 생략**하고 마지막 캐시를 ``market_closed: true``로 재사용한다
    (`_warm_value_rank_live` docstring 참고).

    Returns ``{"date": iso8601|null, "rows": [{"rank", "market", "code", "name",
    "value", "change_rate", "is_etf", "turnover"}, ...], "market_closed": bool,
    "cached_at": iso8601}``.
    """
    return await _warm_value_rank_live()


# flow-rank/live는 만들지 않는다 — 모듈 docstring "flow-rank/live는 만들지 않는다"
# 절의 2026-07-20 장중 실측 근거 참고(sise_deal_rank_iframe이 2영업일 이상 지연돼
# 장중 재호출이 의미가 없었다). EOD `/api/markets/flow-rank`만 유지한다.
