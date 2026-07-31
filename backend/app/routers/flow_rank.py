"""GET /api/markets/flow-rank — 투자자별 순매수/순매도 상위 종목 스냅샷 (PLAN.md §4.5/§6 3.5-2b).
GET /api/markets/flow-path — ETF look-through 수급 경로 분해 상위, direction=in(유입)/
out(유출) 토글 (PLAN.md §4.5/§6 3.5-3, 유출 확장은 §4.6 3.6-4).
GET /api/markets/value-rank — 거래대금 상위 종목("돈이 모이는 곳") 스냅샷 (PLAN.md §4.6 3.6-1).
GET /api/markets/sentiment — 시장 종합 매수세/매도세 게이지(-100~+100) (PLAN.md §4.6 3.6-4).
GET /api/markets/etf-weight-changes — ETF 비중 변화 감별 스크리너 (PLAN.md §5.25, 알테오젠 사례).
이 라우터가 이미 "ETF 관련 시장 스크리너"의 홈이라(flow-path·sentiment의 etf 요소 등)
별도 라우터 파일을 새로 만들지 않고 여기에 추가했다(작업 지시가 남긴 판단 재량).

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

sentiment는 breadth(등락 비율)·flow(현물 수급)·futures(선물 수급, §5.43 신규)·
etf(ETF 순유입 합 ÷ AUM 합) 네 요소를 app/sentiment.py의 순수 함수로 가중평균한
게이지다. breadth/flow/futures 셋은 라이브 우선(장중이면 오늘 실측, 실패/마감이면
EOD 폴백)이고 etf만 EOD 전용이다(§5.43 설계 노트 — ETF 순유입/AUM 라이브 소스가
확인되지 않음). flow는 §5.43 이전에는 flow_rank(외인+기관 순매수/순매도 상위 합,
상위 랭킹 근사치)만 썼지만, 이제 라이브가 가능하면 코스피+코스닥 전체 투자자별
net_value 합산(근사가 아닌 시장 전체 값)으로 계산하고, 라이브가 없을 때만 옛
flow_rank 근사치로 폴백한다. 요소마다 "가장 최근 가용 날짜"·소스가 다를 수 있다 —
그대로 두고 응답에 요소별 date/source를 그대로 노출해 투명하게 밝힌다.

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
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import naver_rank, naver_value_rank
from ..db import get_session
from ..market_hours import KST, is_nxt_closed
from ..models import EtfStat, FlowPath, FlowRank, MarketBreadth, MarketFlow, Stock, ValueRank
from ..quant import etf_weight_changes
from ..quant.flow_baseline import compute_flow_market_baseline
from ..sentiment import breadth_score, compute_sentiment, etf_score, flow_live_score, flow_score
from .markets import _warm_breadth_live, _warm_flow_live, _warm_futures_flow_live

logger = logging.getLogger(__name__)

router = APIRouter(tags=["markets"])

INVESTORS = {"foreign", "institution"}
SIDES = {"buy", "sell"}
MARKET_FILTERS = {"all", "kospi", "kosdaq"}
FLOW_PATH_DIRECTIONS = {"in", "out"}
ETF_WEIGHT_CHANGE_EVENTS = {
    etf_weight_changes.EVENT_NEW,
    etf_weight_changes.EVENT_EXPAND,
    etf_weight_changes.EVENT_SHRINK,
    etf_weight_changes.EVENT_REMOVED,
}
# sentiment 요소별 원재료를 찾을 때 "가장 최근 가용 날짜"를 얼마나 과거까지 훑을지.
SENTIMENT_LOOKBACK_DAYS = 30

# sentiment futures 요소(§5.43 신규)의 EOD 폴백이 읽는 market_flow.market 값
# (collectors/futures_flow.py MARKET 상수와 동일한 문자열).
FUTURES_MARKET = "k200_futures"

# flow_live_score에 넘길 3분류 투자자 라벨 — routers.markets._warm_flow_live/
# _warm_futures_flow_live의 investors dict와 market_flow.investor 컬럼 모두 이
# 정확한 한국어 문자열을 키로 쓴다(collectors/market_flow.py 참고).
_INVESTOR_INDIVIDUAL = "개인"
_INVESTOR_FOREIGN = "외국인"
_INVESTOR_INSTITUTION = "기관계"

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


@router.get("/api/markets/etf-weight-changes")
async def etf_weight_changes_top(
    code: str | None = Query(
        None, description="특정 종목 코드로 필터(예: 196170) — 없으면 시장 전체 스크리닝"
    ),
    active_only: bool = Query(False, description="액티브 펀드(이름에 '액티브' 포함)의 변화만"),
    exclude_leveraged: bool = Query(
        False, description="레버리지/인버스 ETF(이름 기반 판정)의 변화는 제외"
    ),
    event: str | None = Query(
        None, description="신규편입/비중확대/비중축소/편출 중 하나로 필터 — 생략 시 4종 전부"
    ),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """ETF 비중 변화 감별 스크리너 (PLAN.md §5.25/§5.26) — `etf_holdings`(ETF별
    일별 top10 구성 스냅샷)에서 실제로 존재하는 날짜 중 가장 최근 2개를 비교해
    (ETF, 종목) 쌍별 비중 변화를 신규편입/비중확대/비중축소/편출 4종으로
    분류한다(계산 자체는 `app.quant.etf_weight_changes.compute_etf_weight_changes`,
    순수 DB 조회 — 이 라우터에서 새로 수집하지 않는다).

    **핵심 한계(반드시 함께 읽을 것)**: `etf_holdings`는 각 ETF의 **top10
    구성종목만** 매일 스냅샷한다(전체 포트폴리오가 아니다 —
    collectors/etf_master.py 모듈독스트링 참고). 그래서 "신규편입"은 "방금
    처음 산 것"이 아니라 "이미 보유하던 종목이 비중이 커져 top10 안으로
    올라온 것"일 수 있고, "편출"도 "판 것"이 아니라 "다른 보유 종목이 커져
    순위가 밀려난 것"일 수 있다 — 이 데이터만으로는 두 경우를 구분할 수
    없다. 응답의 event 필드는 항상 이 중립적인 이름(신규편입/편출)만 쓰고
    "매수"/"매도"라고 단정하지 않는다.

    `code`를 지정하면 그 종목을 담은 ETF들의 변화만(§5.25 알테오젠 196170
    사례가 검증에 쓰인 예), 생략하면 시장 전체 스크리닝(사용자의 "감별" 요청
    그대로 — 어떤 종목이 최근 ETF 비중 변화를 겪었는지 찾는 용도)이다.
    `active_only=true`면 이름에 "액티브"가 포함된 펀드(한국 ETF 네이밍
    규정상 액티브 펀드는 반드시 이 문자열을 포함 — §5.25 실측: 추적 358개
    중 65개)의 변화만 남긴다. 액티브 펀드는 지수 추종 패시브 ETF의 기계적
    리밸런싱과 달리 펀드매니저 재량을 반영할 가능성이 커서 별도로 구분해
    보여줄 가치가 있다.

    **§5.26 확장 — 레버리지/인버스 플래그 + 실제 수급·가격 컨텍스트**: 사용자가
    SK하이닉스(000660)에서 "KODEX 반도체레버리지"의 비중이 하루 만에
    46.87%→53.41%(+6.54%p)로 급등한 사례를 지적했다 — 레버리지 ETF는 목표
    배율(예: 2배)을 유지하려고 매일 기계적으로 리밸런싱하므로, 이런 변화는
    "의미 있는 자금 유입"이 아니라 레버리지 구조 자체의 부산물일 가능성이
    크다. 응답의 각 변화 행에는 이제 `is_leveraged`(이름에 "레버리지" 또는
    "인버스" 포함 — §5.26 실측: 추적 358개 중 48개), `stock_flow_delta`(그
    종목의 prev_date~curr_date 구간 외국인+기관계 net_value 합), `price_change_pct`
    (같은 구간 stock_ohlcv 종가 등락률)가 추가로 들어있다. **이 두 수치는
    통계적 검증이 아니라 사람이 눈으로 대조 판단하기 위한 참고 자료일 뿐이다**
    — ETF 비중 변화가 실제 수급/가격에 반영됐는지 증명하지 않는다(엄밀한
    상관관계 검증은 `app.quant.etf_weight_backtest`가 별도로 인프라만 갖춰뒀고,
    현재 `etf_holdings` 스냅샷이 4일치뿐이라 아직 신뢰할 수 있는 결과를 낼 수
    없어 이 엔드포인트에는 노출하지 않는다). 둘 다 결측이면 `null`이지 `0`이
    아니다(해당 구간에 데이터가 아예 없다는 뜻). `exclude_leveraged=true`면
    이 플래그가 true인 행을 아예 결과에서 뺀다(기본 false — `active_only`와
    동일한 관례로, 숨기지 않고 플래그로 노출하는 쪽을 기본으로 한다).

    Returns ``{"prev_date": iso|null, "curr_date": iso|null, "changes": [
    {"code", "name", "etf_code", "etf_name", "is_active", "is_leveraged", "event",
    "prev_weight", "curr_weight", "delta", "stock_flow_delta", "price_change_pct"},
    ...]}``. prev_weight/curr_weight/delta는 신규편입/편출이면 한쪽이 null(비교
    대상이 없다는 뜻, 0이 아니다). `etf_holdings` 스냅샷이 아직 2개 미만이면
    (수집 이력 부족) 에러가 아니라 ``prev_date/curr_date: null, changes: []``로
    응답한다.
    """
    if event is not None and event not in ETF_WEIGHT_CHANGE_EVENTS:
        raise HTTPException(400, f"event must be one of {sorted(ETF_WEIGHT_CHANGE_EVENTS)}")

    return await etf_weight_changes.compute_etf_weight_changes(
        session,
        code=code,
        active_only=active_only,
        exclude_leveraged=exclude_leveraged,
        event=event,
        limit=limit,
    )


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
    """flow_rank(외인+기관 순매수/순매도 상위 랭킹 합, 상위 N 근사치) 기준 flow
    요소 — market_sentiment의 폴백 경로(라이브를 못 쓸 때: 장 마감이거나 flow/live
    자체가 실패)다. §5.43 이전에는 이 함수가 유일한 경로였다(breadth의
    _load_breadth_component와 동일한 위치)."""
    since = dt.date.today() - dt.timedelta(days=SENTIMENT_LOOKBACK_DAYS)
    latest_date = (
        await session.execute(
            select(func.max(FlowRank.date)).where(
                FlowRank.investor.in_(INVESTORS), FlowRank.date >= since
            )
        )
    ).scalar()

    if latest_date is None:
        return {"score": None, "date": None, "buy_sum": 0, "sell_sum": 0, "source": "eod"}

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
        "source": "eod",
    }


def _sum_investor_net_values(*investor_dicts: dict | None) -> tuple[float, float, float]:
    """investors 딕셔너리(들 — routers.markets._warm_flow_live/
    _warm_futures_flow_live 응답의 "investors" 값, {투자자명: {"net_value", ...}})
    에서 개인/외국인/기관계 net_value를 뽑아 합산한다. flow(현물) 요소는 코스피+
    코스닥 두 시장 investors를 함께 넘겨 합산하고, futures(선물) 요소는 단일
    시장이라 하나만 넘긴다. 키가 없으면 0(그 투자자 분류가 이번 응답에 없다는
    뜻 — 예: 한쪽 시장 조회 실패, 개장 초반 일부 분류 결측 등)."""
    individual = foreign = institution = 0.0
    for d in investor_dicts:
        if not d:
            continue
        individual += (d.get(_INVESTOR_INDIVIDUAL) or {}).get("net_value") or 0
        foreign += (d.get(_INVESTOR_FOREIGN) or {}).get("net_value") or 0
        institution += (d.get(_INVESTOR_INSTITUTION) or {}).get("net_value") or 0
    return individual, foreign, institution


async def _flow_market_detail(session: AsyncSession, market: str, investors: dict | None) -> dict:
    """단일 시장(코스피 또는 코스닥) investors dict 하나로 flow_live_score +
    과거 대비 baseline을 함께 계산한다(PLAN.md §5.44-1/5.44-2) —
    ``_load_flow_component_live``의 ``by_market.{kospi,kosdaq}`` 원소를 만드는
    헬퍼. baseline은 quant/flow_baseline.py::compute_flow_market_baseline이
    담당한다(그 모듈 docstring 참고 — 관찰 지표일 뿐 예측 아님)."""
    individual, foreign, institution = _sum_investor_net_values(investors)
    score = flow_live_score(individual, foreign, institution)
    baseline = await compute_flow_market_baseline(session, market, score)
    return {
        "score": score,
        "individual": individual,
        "foreign": foreign,
        "institution": institution,
        "baseline": baseline,
    }


async def _load_flow_component_live(session: AsyncSession) -> dict | None:
    """flow/live(routers.markets._warm_flow_live, 코스피+코스닥 키움 라이브 +
    DB 확정치 내부 폴백)를 재사용해 코스피+코스닥 전체 투자자별(개인/외국인/
    기관계) net_value를 합산하고 sentiment.flow_live_score로 계산한다
    (PLAN.md §5.43-2) — 기존 flow_rank 상위 랭킹 근사치(_load_flow_component)를
    대체하는 시장 전체 계산이라 더는 근사치가 아니다.

    _load_breadth_component_live와 완전히 동일한 게이트 원칙(§5.5-4)을 쓴다:
    market_closed면 무조건 None을 반환해 호출자가 기존 EOD 경로
    (_load_flow_component)로 넘어가게 한다. _warm_flow_live 자체는 내부적으로
    (장중 키움 라이브 -> 실패 시 DB 확정치) 폴백을 이미 갖고 있어 market_closed
    상황에서도 값을 채워 돌려줄 수 있지만, 이 게이트는 breadth와 동일하게
    "장 마감이면 옛 EOD 경로로" 원칙을 우선한다 — 두 폴백 소스가 다르다는
    비일관성보다 게이트 동작 자체의 일관성(breadth/flow가 같은 규칙)을 택했다.

    **by_market(§5.44 신규)**: 종합 게이지에 들어가는 ``score``(코스피+코스닥
    합산)는 그대로 두고, 그 옆에 시장별 개별 flow_live_score + 과거 20거래일
    대비 percentile을 ``by_market: {"kospi": {...}, "kosdaq": {...}}``으로
    추가 노출한다(§5.44 스코프 — 종합 산식 자체는 건드리지 않음, 상세 표시용
    부가 정보). 라이브 경로에서만 계산 가능하다 — EOD 폴백(_load_flow_component)은
    코스피/코스닥을 분리한 투자자별 원재료 자체가 없으므로(flow_rank 상위
    랭킹 합계만 있음) by_market을 만들 수 없다. 그래서 by_market은 이 라이브
    함수에만 있고, EOD 응답 dict에는 아예 키 자체가 없다 — market_sentiment가
    ``flow.get("by_market")``로 안전하게 접근해 없으면 프런트도 조용히
    생략한다(house "표본/소스 없음은 조용히 생략" 관례).

    **2026-07-31 버그 수정 — 라이브+DB 폴백이 둘 다 실패하면 예외가 아니라
    None**: ``_warm_flow_live``는 장중인데 키움 라이브 호출도 실패하고
    (예: 8050 지정단말기 인증 실패 — CI 러너처럼 키움에 등록 안 된 IP에서
    호출할 때 항상 발생) ``market_flow`` DB 확정치도 없으면(예: export_static.py가
    쓰는 CI 빌드 DB에 kospi/kosdaq market_flow가 아직 한 번도 안 쌓인 경우)
    ``HTTPException(502)``를 던진다 — `/api/markets/flow/live`처럼 "진짜 실패를
    호출자에게 그대로 보여줘야 하는" 엔드포인트에는 맞는 동작이지만, 여기서는
    한 요소 실패가 종합 게이지 전체를 죽이면 안 된다(다른 세 요소만으로도
    ``compute_sentiment``가 정상적으로 재정규화한다). market_closed 게이트와
    똑같이 "이 요소를 쓸 수 없으면 호출자가 옛 EOD 근사치 경로로 넘어가게"
    처리하기 위해 이 예외를 잡아 None으로 변환한다."""
    try:
        live = await _warm_flow_live(session)
    except HTTPException:
        return None
    if live.get("market_closed"):
        return None
    kospi = live.get("kospi")
    kosdaq = live.get("kosdaq")
    if kospi is None and kosdaq is None:
        return None

    individual, foreign, institution = _sum_investor_net_values(
        (kospi or {}).get("investors"), (kosdaq or {}).get("investors")
    )

    by_market = {
        "kospi": await _flow_market_detail(session, "kospi", (kospi or {}).get("investors")),
        "kosdaq": await _flow_market_detail(session, "kosdaq", (kosdaq or {}).get("investors")),
    }

    return {
        "score": flow_live_score(individual, foreign, institution),
        "date": dt.datetime.now(KST).date().isoformat(),
        "individual": individual,
        "foreign": foreign,
        "institution": institution,
        "source": "live",
        "by_market": by_market,
    }


async def _load_futures_component_live() -> dict | None:
    """futures-flow/live(routers.markets._warm_futures_flow_live, K200 선물
    네이버 라이브)를 재사용해 선물 투자자별(개인/외국인/기관계) net_value로
    sentiment.flow_live_score를 계산한다(PLAN.md §5.43-2, 신규 요소).

    market_closed면(또는 investors가 비어 있으면 — 캐시조차 없는 기동 직후 등)
    None을 반환해 호출자가 EOD 폴백(_load_futures_component_eod, market_flow
    market='k200_futures' 확정치)으로 넘어가게 한다 — breadth/flow와 동일한
    게이트. _warm_futures_flow_live는 session 인자를 받지 않는다(flow와 달리
    자체 DB 폴백이 없다 — 아래 _load_futures_component_eod docstring 참고)."""
    live = await _warm_futures_flow_live()
    if live.get("market_closed"):
        return None
    investors = live.get("investors") or {}
    if not investors:
        return None

    individual, foreign, institution = _sum_investor_net_values(investors)

    return {
        "score": flow_live_score(individual, foreign, institution),
        "date": live.get("date") or dt.datetime.now(KST).date().isoformat(),
        "individual": individual,
        "foreign": foreign,
        "institution": institution,
        "source": "live",
    }


async def _load_futures_component_eod(session: AsyncSession) -> dict:
    """K200 선물 EOD 폴백 — collectors/futures_flow.py가 매일 적재하는
    market_flow(market='k200_futures') 확정치를 읽어 sentiment.flow_live_score로
    계산한다(PLAN.md §5.43-2).

    **설계 노트**: _warm_futures_flow_live 자체는 flow(_warm_flow_live)와 달리
    DB 폴백이 없다 — 장 마감이면 마지막 메모리 캐시만 재사용하고(없으면
    investors={}), market_flow 테이블에 쓰지도 읽지도 않는다(routers/markets.py
    모듈 docstring "market_flow DB에는 쓰지 않는다" 절 참고). PLAN.md §5.43
    태스크 표는 "EOD 폴백 없이 라이브 불가 시 None"도 허용했지만, market_flow에는
    이미 collectors/futures_flow.py 일별 배치가 채워둔 k200_futures 확정치가
    존재하므로(§4.5 4.5-2) 이를 그대로 재사용해 breadth/flow와 대등하게 "장
    마감 시간대에도 futures 요소가 항상 None이 되지는 않도록" 만들었다 — 이
    함수가 없다면 밤/주말에는 게이지가 영구히 3요소로만 재정규화된다."""
    since = dt.date.today() - dt.timedelta(days=SENTIMENT_LOOKBACK_DAYS)
    latest_date = (
        await session.execute(
            select(func.max(MarketFlow.date)).where(
                MarketFlow.market == FUTURES_MARKET, MarketFlow.date >= since
            )
        )
    ).scalar()

    if latest_date is None:
        return {
            "score": None,
            "date": None,
            "individual": 0,
            "foreign": 0,
            "institution": 0,
            "source": "eod",
        }

    rows = (
        await session.execute(
            select(MarketFlow).where(
                MarketFlow.market == FUTURES_MARKET, MarketFlow.date == latest_date
            )
        )
    ).scalars().all()
    by_investor = {r.investor: (r.net_value or 0) for r in rows}
    individual = by_investor.get(_INVESTOR_INDIVIDUAL, 0)
    foreign = by_investor.get(_INVESTOR_FOREIGN, 0)
    institution = by_investor.get(_INVESTOR_INSTITUTION, 0)

    return {
        "score": flow_live_score(individual, foreign, institution),
        "date": latest_date.isoformat(),
        "individual": individual,
        "foreign": foreign,
        "institution": institution,
        "source": "eod",
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
        return {"score": None, "date": None, "net_inflow_sum": 0, "aum_sum": 0, "source": "eod"}

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
        "source": "eod",
    }


@router.get("/api/markets/sentiment")
async def market_sentiment(session: AsyncSession = Depends(get_session)) -> dict:
    """시장 종합 매수세/매도세 게이지(-100~+100) (PLAN.md §4.6 3.6-4, §5.43).

    breadth(등락 비율)·flow(현물 수급)·futures(선물 수급, §5.43 신규)·etf(ETF 순유입
    합 ÷ AUM 합) 네 요소를 app/sentiment.py의 순수 함수로 가중평균한다. 각 요소는
    서로 다른 소스라 "가장 최근 가용 날짜"를 독립적으로 찾으므로 날짜가 어긋날 수
    있다 — 그대로 두고 components[*].date에 그대로 노출한다(투명성). 요소 하나라도
    데이터가 없으면(None) 나머지 요소로 가중치를 재정규화한다(compute_sentiment
    참고). 넷 다 없으면 score도 None.

    approx=True는 항상 고정값이다 — etf 요소는 여전히 ETF 유니버스 기반 근사치이고
    (§4.6 한계 절), flow/futures도 라이브가 아닐 때는 flow_rank 상위 랭킹 근사치로
    폴백할 수 있다(아래 flow 절 참고). breadth/flow/futures 셋 다 라이브일 때는
    시장 전체 값이라 근사가 아니지만, 요소 구성 자체가 상황에 따라 바뀌므로
    approx 플래그는 단순화를 위해 항상 True로 고정한다.

    **breadth 요소는 2026-07-21(PLAN.md §5.5-4)부터 라이브를 우선한다**: 장중이고
    breadth/live(routers.markets._warm_breadth_live, 1분 캐시) 조회가 성공하면 그
    adv/dec/flat으로 계산하고(``components.breadth.source == "live"``), 장
    마감이거나 라이브가 실패하면 기존 market_breadth DB EOD 확정치로 폴백한다
    (``source == "eod"``) — 완전 대체가 아니라 우선순위 추가다.

    **flow 요소는 2026-07-30(PLAN.md §5.43)부터 라이브를 우선한다**: 장중이고
    flow/live(routers.markets._warm_flow_live, 코스피+코스닥 키움 라이브 + DB
    확정치 내부 폴백) 조회가 성공하면 두 시장 투자자별(개인/외국인/기관계)
    net_value를 합산해 sentiment.flow_live_score로 계산하고(``source == "live"``,
    ``individual``/``foreign``/``institution`` 필드로 원재료 노출), 장 마감이거나
    라이브가 실패하면 **기존 flow_rank 상위 랭킹 근사치**(``source == "eod"``,
    ``buy_sum``/``sell_sum`` 필드)로 폴백한다. §5.43 이전에는 flow_rank 근사치만
    있었다(§4.6 한계 — 상위 N 종목의 매수/매도 합만 볼 뿐 시장 전체를 못 봄) — 이제
    라이브 경로는 시장 전체 투자자 합계를 그대로 쓰므로 더는 근사치가 아니다. 라이브/
    EOD 두 경로가 서로 다른 필드 셋을 노출하는 비대칭이 있다 — 두 소스의 원재료
    자체가 다르기 때문에(투자자별 net_value vs 랭킹 매수/매도 합) 억지로 통일하지
    않았다.

    **flow.by_market은 2026-07-30(PLAN.md §5.44)부터 추가된다**: 사용자가
    "코스피만 따로 보면 지금 수급이 평소보다 높은지 낮은지 알 수 있냐"고 물은
    데서 나왔다 — 종합 게이지에 들어가는 ``components.flow.score``(코스피+코스닥
    합산)는 그대로 두고(§5.44 스코프 — 종합 산식 변경 아님), flow가 라이브
    경로일 때만 그 옆에 ``components.flow.by_market: {"kospi": {...},
    "kosdaq": {...}}``을 추가로 채운다. 각 시장 원소는
    ``{"score": flow_live_score, "individual"/"foreign"/"institution": 원재료,
    "baseline": {"reason", "mean_score", "percentile", "lookback_days_requested",
    "lookback_days_used"}}`` 형태다(baseline 산식은
    quant/flow_baseline.py::compute_flow_market_baseline — 그 시장 자신의 과거
    확정 거래일 대비 percentile, 관찰 지표일 뿐 예측 아님). flow가 EOD 폴백일
    때는 코스피/코스닥을 분리한 투자자별 원재료 자체가 없으므로(flow_rank 상위
    랭킹 합계만 있음) ``by_market`` 키가 응답에 아예 없다 — 프런트가
    ``components.flow.by_market``의 존재 여부로 분기해 없으면 조용히 생략한다.

    **futures 요소는 2026-07-30(PLAN.md §5.43)부터 신규 추가된다**: 이 프로젝트가
    이미 "외인 양손"(현물+선물 동시 수급)을 중요한 신호로 다뤄왔는데(§4.7 외인 양손
    절 등) 기존 게이지는 선물을 전혀 반영하지 않았다는 사용자 지적("반쪽
    정보였다")에서 나왔다. 장중이고 futures-flow/live(routers.markets.
    _warm_futures_flow_live, K200 선물 네이버 라이브) 조회가 성공하면 선물
    투자자별 net_value로 flow_live_score를 계산하고(``source == "live"``), 장
    마감이거나 라이브가 실패하면 market_flow(market='k200_futures') EOD 확정치로
    폴백한다(``source == "eod"``, collectors/futures_flow.py 일별 배치가 채워둠).
    두 경로 다 없으면(배치도 미실행) score는 None이고 나머지 요소로 재정규화된다.

    **etf 요소는 라이브 소스가 없다(EOD 전용, §5.43 설계 노트)**: ETF 순유입/AUM은
    펀드 보고 특성상 일단위로만 확정되고, 이 프로젝트가 조사한 범위에서 장중
    실시간으로 갱신되는 소스를 찾지 못했다 — breadth/flow/futures 세 요소와 달리
    라이브 우선 게이트 자체가 없고, ``_load_etf_component``가 유일한 경로다.
    """
    breadth = await _load_breadth_component_live(session)
    if breadth is None:
        breadth = await _load_breadth_component(session)

    flow = await _load_flow_component_live(session)
    if flow is None:
        flow = await _load_flow_component(session)

    futures = await _load_futures_component_live()
    if futures is None:
        futures = await _load_futures_component_eod(session)

    etf = await _load_etf_component(session)

    score, weights = compute_sentiment(
        breadth["score"], flow["score"], futures["score"], etf["score"]
    )

    return {
        "score": score,
        "approx": True,
        "components": {
            "breadth": {"weight": weights["breadth"], **breadth},
            "flow": {"weight": weights["flow"], **flow},
            "futures": {"weight": weights["futures"], **futures},
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

    개별 종목 거래대금 목록이라(§4.7-3 원칙, 2026-07-21 NXT 실측 — market_hours.py
    모듈 docstring 참고) 장 마감 판정은 ``is_market_closed``(KRX 정규장 09:00~15:30)가
    아니라 ``is_nxt_closed``(NXT 확장세션 08:00~20:00)를 쓴다 — 정규장 마감 후에도
    NXT에서 개별 종목이 계속 거래되므로 이 라우트도 20:00까지 계속 조회한다.
    그 시간대까지도 마감이면 네이버를 아예 호출하지 않는다 — DB 폴백이 없으므로
    마지막 캐시(있으면)를 ``market_closed: true``로 재사용하고, 캐시조차 없으면
    빈 값으로 응답한다.

    **2026-07-22 추가 수정(NXT 프리마켓 공백)**: 08:00~08:50 NXT 프리마켓은
    ``is_nxt_closed`` 기준 "장중"이라 위 조기 반환을 안 타는데, 이 소스(네이버
    거래대금 순위)는 정규장(09:00) 거래량 기반이라 정규장 시작 전엔 늘
    빈 응답(totalCount=0)이다 — 사용자가 매일 아침 이 카드가 502로 깨져
    보인다고 지적해 발견했다. 소스가 빈 응답이면(양쪽 시장 다 실패) 더는
    502를 던지지 않고, 위와 동일한 "마지막 캐시 재사용, 없으면 빈 값" 폴백을
    쓴다(아래 ``if not rows_all`` 분기 참고) — "장 마감"과 "정규장 아직 안 열림"을
    프런트 입장에서 구분할 필요가 없어 같은 ``market_closed`` 플래그로 충분하다."""
    now = time.monotonic()
    async with _value_rank_live_cache_lock:
        cached = _value_rank_live_cache["data"]
        if cached is not None and (now - _value_rank_live_cache["ts"]) < LIVE_TTL_SECONDS:
            return cached

        now_kst = dt.datetime.now(KST)
        if is_nxt_closed(now_kst):
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
            # 2026-07-22 수정 — NXT 프리마켓(08:00~08:50)은 is_nxt_closed 기준
            # "장중"이라 위 조기 반환을 안 타는데, 이 소스(네이버 거래대금 순위)는
            # 정규장(09:00) 거래량 기반이라 정규장 시작 전엔 totalCount=0으로
            # 아무 것도 안 준다 — 사용자 실측 지적("NXT 프리장을 소화 못 한다,
            # 502로 카드가 아예 깨짐"). 캐시가 있으면 어제 마지막 값을
            # market_closed=True로 재사용(위 is_nxt_closed 분기와 동일한 폴백),
            # 캐시조차 없으면(기동 직후) 빈 값으로 응답한다 — 정규장이 열리는
            # 순간 다음 폴링에서 자동으로 정상 데이터로 갱신된다. 소스가 진짜
            # 죽었을 때(양쪽 다 실패)와 "아직 정규장 전이라 없음"을 여기서는
            # 구분하지 않는다 — 둘 다 "지금은 신선한 값이 없다"는 같은 사용자
            # 경험이라 프런트가 이미 아는 market_closed 플래그 하나로 충분하다.
            logger.warning("value-rank live: 소스 빈 응답(정규장 전일 가능성) — %s", errors)
            cached = _value_rank_live_cache["data"]
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
