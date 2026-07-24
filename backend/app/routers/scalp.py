"""GET /api/markets/scalp-candidates — 스켈핑 후보 종목 스크리닝 (PLAN.md §5.2).

**참고용 스크리닝이지 매매 신호가 아니다** — score는 "지금 거래대금·변동성·
관심도가 몰려 있는 상대적 정도"만 나타낸다(app/quant/screener.py 모듈 docstring
"스코어 산식" 참고, §5 전체 원칙 "관찰 사실만 서술" 그대로 계승).

신규 수집을 하지 않는다(§5.2 지시) — 이미 다른 라이브 캐시가 채워둔 데이터를
그대로 재사용한다:
- 후보군·거래대금 순위·회전율: ``routers.flow_rank._warm_value_rank_live()``
  (§4.7 value-rank/live, 7분 캐시) — 코스피+코스닥 상위 각 100개(최대 200개)
  스냅샷에서 ETF를 뺀 개별주만 후보로 쓴다(§5.2 "ETF는 제외").
- 관심순위 편입 여부·등락률: ``routers.markets._warm_attention(session)``(60초 캐시).
- **종목별 당일 수급(PLAN.md §5.20, 2026-07-23 추가)**: ``stock_flow`` 테이블
  (DB, ``_stock_flow_lookup``이 읽는다) — ``collectors/live_refresh.py``의
  ``_run_stock_flow_scan``(10분 티어)이 위 후보군 코드를 순회하며 키움
  ka10059(``routers/stocks.py``의 기존 파서 재사용)로 채워둔다. 이 라우터는
  그 결과를 읽기만 할 뿐 여기서 직접 키움을 호출하지 않는다.

**change_rate 소스 우선순위(2026-07-21, §5.4-1)**: attention에 그 종목이 있으면
attention의 change_rate(60초 캐시, 더 신선함)를 쓰고, 없으면 value-rank의
change_rate(최대 7분 캐시)로 폴백한다. 처음엔 무조건 value-rank만 썼는데,
"실시간 관심 TOP5"(attention 소스)와 스켈핑 후보에 같은 종목이 겹칠 때 등락률이
서로 다르게 보이는 문제가 있었다 — 캐시 타이밍이 아니라 두 카드가 애초에 다른
소스를 참조하고 있었던 것. 완전한 해법(종목코드별 단일 시세 캐시)은 지금 규모에선
과해 이 우선순위 절충안을 택했다.

**가격제한폭 근접/과도한 하락 처리(2026-07-24, PLAN.md §5.27, 사용자 지적)**:
실 데이터에서 "일승"이 +29.86%(KRX ±30% 가격제한폭에 사실상 도달 — 틱 단위
반올림 탓에 정확히 30.00%로는 안 찍힌다)로 스켈핑 후보 상위에 오른 사례가
있었다. 사용자 지적대로 가격제한폭에 도달/근접한 종목은 그 방향으로 오늘 더
움직일 "room"이 물리적으로 없어(상한가는 매수 체결 자체가 막히고, 하한가는
매수 호가가 사라져 유동성이 증발하는 경우가 흔함) 스켈핑(당일 변동성 포착)의
전제 자체가 성립하지 않는다 — 그래서 ``_scored_candidates``가 이런 종목을
후보 리스트 구성 단계에서 **아예 제외**한다(ETF 제외와 동일한 카테고리의
"애초에 후보 자격 없음" 처리, 스코어 조정이 아니다). 이때 반드시
``_change_rate_lookup``으로 소스 우선순위가 이미 적용된 값을 기준으로 판단해야
한다 — value-rank 원본 값만 보면 attention이 더 신선한 값을 갖고 있을 때 필터가
실제 노출되는 change_rate와 어긋난다. 한편 가격제한폭까지는 아니지만 큰 폭
하락(``change_rate <= -15.0``)인 종목은 상승과 "변동성 크기"는 같아도 리스크
성격이 달라(악재/유동성 위기로 인한 붕괴일 가능성) 배제하지 않되
``at_risk`` 플래그로만 노출한다(``quant/screener.py`` 모듈 docstring "at_risk
플래그" 참고 — 스코어 산식 자체는 건드리지 않는다, 과설계 방지).

이 라우터 자체는 별도 캐시를 두지 않는다 — 두 소스 모두 이미 자체 TTL+락
캐시를 갖고 있어(warm 함수가 캐시 히트면 즉시 반환) 매 요청마다 다시 불러도
비용이 없고, 정렬·스코어링(순수 함수) 자체도 가볍다. ``GET
/api/markets/scalp-candidates`` 자체는 DB에 쓰지 않는다(§5.2 "DB 미저장" —
장중 스냅샷 성격은 attention/value-rank/live와 동일).

**GET /api/markets/scalp-candidates/track-record (PLAN.md §5.7-3, 관찰 기록)**:
위 스크리닝 결과가 실제로 의미 있는지 사후 검증할 근거를 쌓기 위해, 그날 상위
후보에 "처음" 등장한 종목과 이후 고정 호라이즌(5/15/30/60분·당일 마감)
change_rate를 ``scalp_pick`` 테이블에 기록해둔다(``collectors/scalp_tracker.py``,
``collectors/live_refresh.py``의 60초 잡에 배선 — 새 외부 호출 없음). 이 엔드포인트는
최근 N일의 원본 행을 그대로 반환할 뿐 집계·상관계수 계산은 하지 않는다(§5.7
설계 5번 — 데이터가 며칠~몇 주 쌓이기 전엔 표본이 너무 작아 의미가 없다는 게
이미 합의된 제약). **이 역시 관찰 기록이지 매매 신호/추천이 아니다.**
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..market_hours import KST
from ..models import ScalpPick, StockFlow
from ..quant.screener import compute_scalp_scores
from .flow_rank import _warm_value_rank_live
from .markets import _warm_attention

router = APIRouter(tags=["markets"])

# PLAN.md §5.20-2 — 오늘 외국인+기관 순매수만 스코어에 반영한다(개인은 §5.16에서
# 확인했듯 장중 실시간 값이 항상 0으로 스텁돼 있어 신호가 아니다).
_FLOW_INVESTORS = ("외국인", "기관계")

# KRX 정규 종목 가격제한폭은 ±30%(2015-06-15부터) — 틱 단위 반올림 때문에 실제
# 표시값은 정확히 30.00%가 아니라 29.86% 같은 값으로 찍힌다(PLAN.md §5.27 실측:
# "일승" 29.86%). 정확히 30.0만 걸러내면 실질 제한폭 종목을 놓치므로 여유를
# 둔다.
PRICE_LIMIT_THRESHOLD_PCT = 29.5

# 이 값 이하(큰 폭 하락)면 스코어는 그대로 두되 별도 위험 플래그만 추가한다
# (PLAN.md §5.27 — 스코어 산식 자체는 건드리지 않는다, 과설계 방지). quant/
# screener.py에도 동일 값이 있다(의도적 중복, 그 파일의 상수 주석 참고).
LARGE_DECLINE_WARNING_PCT = -15.0


async def _stock_flow_lookup(session: AsyncSession, codes: list[str]) -> dict[str, int]:
    """code -> 오늘 날짜 외국인+기관계 net_value 합계(PLAN.md §5.20-2).

    ``stock_flow``는 ``collectors/live_refresh.py::_run_stock_flow_scan``(10분
    티어)이 후보군을 순회하며 채운다 — 이 함수는 그 결과를 읽기만 한다(새 외부
    호출 없음). net_value가 NULL인 행은 "값이 없다"는 뜻이라 합계에서 제외한다
    (0으로 취급하면 진짜 순매수 0과 구분이 안 된다). 스윕이 아직 그 종목까지
    돌지 않았거나 데이터가 전혀 없으면 그 code는 반환 dict에 아예 없다 — 호출부가
    ``.get(code)``로 자연스럽게 None(중립)을 받도록 한다(change_rate/turnover의
    기존 None 처리 관례와 동일, quant/screener.py 참고).
    """
    if not codes:
        return {}

    today = dt.datetime.now(KST).date()
    stmt = select(StockFlow.code, StockFlow.net_value).where(
        StockFlow.code.in_(codes),
        StockFlow.date == today,
        StockFlow.investor.in_(_FLOW_INVESTORS),
        StockFlow.net_value.isnot(None),
    )
    rows = (await session.execute(stmt)).all()

    totals: dict[str, int] = {}
    for code, net_value in rows:
        totals[code] = totals.get(code, 0) + net_value
    return totals


async def _fetch_live_payloads(session: AsyncSession) -> tuple[dict, dict]:
    """value-rank/live + attention 캐시를 함께 워밍해 반환한다 — 이 라우터의
    ``scalp_candidates``와 ``collectors/scalp_tracker.py``(PLAN.md §5.7 추적
    기록)가 공유하는 유일한 조합 지점이다. 새 외부 호출을 늘리지 않으려면
    항상 이 함수를 통해서만 두 캐시에 접근한다(둘 다 자체 TTL+락 캐시가 있어
    캐시 히트면 즉시 반환)."""
    value_payload = await _warm_value_rank_live()
    attention_payload = await _warm_attention(session)
    return value_payload, attention_payload


def _change_rate_lookup(value_payload: dict, attention_payload: dict) -> dict[str, float]:
    """code -> change_rate 조회 테이블. **change_rate 소스 우선순위(2026-07-21,
    §5.4-1)**: attention(키움, 60초 캐시)이 있으면 그 값을 우선하고, 없는
    종목만 value-rank(네이버, 최대 7분 캐시)의 값으로 폴백한다 — "실시간 관심
    TOP5"와 스켈핑 후보에 같은 종목이 겹칠 때 등락률이 서로 다르게 보이던
    문제(두 카드가 서로 다른 소스를 참조)를 해소한 절충안."""
    rates = {
        row["code"]: row.get("change_rate")
        for row in (value_payload.get("rows") or [])
        if row.get("code") and row.get("change_rate") is not None
    }
    for row in attention_payload.get("rows") or []:
        if row.get("code") and row.get("change_rate") is not None:
            rates[row["code"]] = row["change_rate"]
    return rates


async def _scored_candidates(session: AsyncSession) -> tuple[list[dict[str, Any]], dict]:
    """value-rank/attention 캐시를 조합해 스켈핑 후보를 스코어링한 뒤 내림차순
    정렬해 반환한다(``compute_scalp_scores`` 그대로 적용) — ``scalp_candidates``
    라우트와 ``collectors/scalp_tracker.py``(§5.7)가 공유한다. 반환값은
    ``(scored, value_payload)`` — value_payload는 date/market_closed/cached_at
    메타데이터 노출용으로 호출자에게 그대로 넘겨준다.

    PLAN.md §5.27-1 — 후보 구성 단계에서 가격제한폭(±30%) 근접 종목
    (``abs(change_rate) >= PRICE_LIMIT_THRESHOLD_PCT``)을 ETF 제외와 같은
    필터링 단계에서 함께 걸러낸다(구조적 배제, 스코어 조정이 아니다). 이때
    비교 기준 change_rate는 반드시 ``_change_rate_lookup``으로 소스 우선순위가
    이미 적용된 값이어야 한다 — value-rank 원본 값만 보면 attention이 더 신선한
    값을 갖고 있을 때(§5.4-1) 실제 응답에 노출되는 change_rate와 필터 판단
    기준이 어긋난다. change_rate가 None(데이터 없음)인 종목은 "제한폭 도달"과
    다른 의미이므로 배제하지 않는다."""
    value_payload, attention_payload = await _fetch_live_payloads(session)

    attention_rows = attention_payload.get("rows") or []
    attention_codes = {row["code"] for row in attention_rows if row.get("code")}
    change_rates = _change_rate_lookup(value_payload, attention_payload)

    candidates = [
        {
            "code": row["code"],
            "name": row.get("name") or row["code"],
            "market": row.get("market"),
            "change_rate": resolved_rate,
            "turnover": row.get("turnover"),
            "value_rank": row["rank"],
        }
        for row in (value_payload.get("rows") or [])
        if not row.get("is_etf")
        # PLAN.md §5.27-1 — walrus로 change_rate 소스 우선순위 적용 결과를
        # 한 번만 계산해 위 dict의 change_rate 값과 아래 필터 판단에 동일하게
        # 쓴다(둘이 어긋나면 응답에 노출되는 값과 필터 기준이 달라지는 버그가 됨).
        if (resolved_rate := change_rates.get(row["code"], row.get("change_rate"))) is None
        or abs(resolved_rate) < PRICE_LIMIT_THRESHOLD_PCT
    ]

    # PLAN.md §5.20-2 — 후보군이 정해진 뒤에 그 코드들만 stock_flow에서 조회한다
    # (전체 stock_flow 테이블을 스캔하지 않고 이번 후보군으로 한정). 아직 스윕이
    # 안 돈 종목은 lookup에 없어 .get()이 None을 주고, compute_scalp_scores가
    # 이를 change_rate/turnover의 None과 동일하게 중립(0) 처리한다.
    flow_lookup = await _stock_flow_lookup(session, [c["code"] for c in candidates])
    for c in candidates:
        c["flow_net_value"] = flow_lookup.get(c["code"])

    scored = compute_scalp_scores(candidates, attention_codes)
    return scored, value_payload


@router.get("/api/markets/scalp-candidates")
async def scalp_candidates(
    limit: int = Query(10, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """오늘 데이터 기준 스켈핑 적합도 스코어 상위 N (PLAN.md §5.2).

    Returns ``{"date": iso8601|null, "market_closed": bool, "cached_at":
    iso8601|null, "rows": [{"code", "name", "market", "score", "change_rate",
    "turnover", "in_attention_top", "value_rank_position", "flow_net_value",
    "at_risk"}, ...]}`` — ``flow_net_value``(PLAN.md §5.20)는 오늘 외국인+기관
    순매수 합(백만원 단위, stock_flow.net_value 그대로 — market_flow/종목상세
    수급 차트와 동일 컨벤션, StockDetailModal.jsx 참고), 아직 수급 스윕이 안 돈
    종목은 null. ``at_risk``(PLAN.md §5.27-2)는 change_rate가
    ``LARGE_DECLINE_WARNING_PCT``(-15.0) 이하인 큰 폭 하락 종목에서 true —
    score에는 영향 없는 순수 정보성 플래그다. 가격제한폭 근접 종목
    (``abs(change_rate) >= PRICE_LIMIT_THRESHOLD_PCT``, 29.5 — §5.27-1)은
    아예 rows에 나타나지 않는다(구조적 배제, ETF 제외와 동일 카테고리).

    market_closed는 후보군 소스(value-rank/live)의 값을 그대로 따른다 — 장
    마감이면 마지막 라이브 스냅샷을 그대로 재사용해 표시한다(value-rank/live와
    동일한 관례, 허위로 새로 만들어내지 않는다).
    """
    scored, value_payload = await _scored_candidates(session)

    return {
        "date": value_payload.get("date"),
        "market_closed": value_payload.get("market_closed", False),
        "cached_at": value_payload.get("cached_at"),
        "rows": [
            {
                "code": c["code"],
                "name": c["name"],
                "market": c["market"],
                "score": c["score"],
                "change_rate": c["change_rate"],
                "turnover": c["turnover"],
                "in_attention_top": c["in_attention_top"],
                "value_rank_position": c["value_rank"],
                "flow_net_value": c.get("flow_net_value"),
                "at_risk": c.get("at_risk", False),
            }
            for c in scored[:limit]
        ],
    }


def _decimal_to_float(value: Any) -> float | None:
    return float(value) if value is not None else None


@router.get("/api/markets/scalp-candidates/track-record")
async def scalp_candidates_track_record(
    days: int = Query(7, ge=1, le=90),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """최근 N일(기본 7일)의 스켈핑 후보 추적 기록 원본 행을 그대로 반환한다
    (PLAN.md §5.7-3). **관찰 기록이지 매매 신호/추천이 아니다** — 진입/청산가나
    매매 수수료·슬리피지는 반영하지 않은, "이 시점에 이 종목이 후보였다 → 이후
    등락률이 이렇게 흘러갔다"는 사실만 담은 표다. 집계·상관계수 계산은 하지
    않는다(모듈 docstring 참고 — 표본이 쌓이기 전엔 의미가 없다는 합의된 제약).

    Returns ``{"since": iso8601, "days": int, "rows": [{"date", "code", "name",
    "market", "entry_time", "entry_rank", "entry_score", "entry_change_rate",
    "entry_turnover", "in_attention_top_at_entry", "change_rate_5m",
    "change_rate_15m", "change_rate_30m", "change_rate_60m",
    "change_rate_eod"}, ...]}`` — 날짜 내림차순, 같은 날짜 내에서는 entry_rank
    오름차순. 호라이즌 컬럼은 아직 그 시각이 안 됐거나 못 채웠으면 null.
    """
    since = dt.datetime.now(KST).date() - dt.timedelta(days=days - 1)
    stmt = (
        select(ScalpPick)
        .where(ScalpPick.date >= since)
        .order_by(ScalpPick.date.desc(), ScalpPick.entry_rank.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()

    return {
        "since": since.isoformat(),
        "days": days,
        "rows": [
            {
                "date": r.date.isoformat(),
                "code": r.code,
                "name": r.name,
                "market": r.market,
                "entry_time": r.entry_time.isoformat() if r.entry_time else None,
                "entry_rank": r.entry_rank,
                "entry_score": _decimal_to_float(r.entry_score),
                "entry_change_rate": _decimal_to_float(r.entry_change_rate),
                "entry_turnover": _decimal_to_float(r.entry_turnover),
                "in_attention_top_at_entry": r.in_attention_top_at_entry,
                "change_rate_5m": _decimal_to_float(r.change_rate_5m),
                "change_rate_15m": _decimal_to_float(r.change_rate_15m),
                "change_rate_30m": _decimal_to_float(r.change_rate_30m),
                "change_rate_60m": _decimal_to_float(r.change_rate_60m),
                "change_rate_eod": _decimal_to_float(r.change_rate_eod),
            }
            for r in rows
        ],
    }
