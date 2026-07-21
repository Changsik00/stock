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

**change_rate 소스 우선순위(2026-07-21, §5.4-1)**: attention에 그 종목이 있으면
attention의 change_rate(60초 캐시, 더 신선함)를 쓰고, 없으면 value-rank의
change_rate(최대 7분 캐시)로 폴백한다. 처음엔 무조건 value-rank만 썼는데,
"실시간 관심 TOP5"(attention 소스)와 스켈핑 후보에 같은 종목이 겹칠 때 등락률이
서로 다르게 보이는 문제가 있었다 — 캐시 타이밍이 아니라 두 카드가 애초에 다른
소스를 참조하고 있었던 것. 완전한 해법(종목코드별 단일 시세 캐시)은 지금 규모에선
과해 이 우선순위 절충안을 택했다.

이 라우터 자체는 별도 캐시를 두지 않는다 — 두 소스 모두 이미 자체 TTL+락
캐시를 갖고 있어(warm 함수가 캐시 히트면 즉시 반환) 매 요청마다 다시 불러도
비용이 없고, 정렬·스코어링(순수 함수) 자체도 가볍다. DB에는 쓰지 않는다(§5.2
"DB 미저장" — 장중 스냅샷 성격은 attention/value-rank/live와 동일).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..quant.screener import compute_scalp_scores
from .flow_rank import _warm_value_rank_live
from .markets import _warm_attention

router = APIRouter(tags=["markets"])


@router.get("/api/markets/scalp-candidates")
async def scalp_candidates(
    limit: int = Query(10, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """오늘 데이터 기준 스켈핑 적합도 스코어 상위 N (PLAN.md §5.2).

    Returns ``{"date": iso8601|null, "market_closed": bool, "cached_at":
    iso8601|null, "rows": [{"code", "name", "market", "score", "change_rate",
    "turnover", "in_attention_top", "value_rank_position"}, ...]}``.

    market_closed는 후보군 소스(value-rank/live)의 값을 그대로 따른다 — 장
    마감이면 마지막 라이브 스냅샷을 그대로 재사용해 표시한다(value-rank/live와
    동일한 관례, 허위로 새로 만들어내지 않는다).
    """
    value_payload = await _warm_value_rank_live()
    attention_payload = await _warm_attention(session)

    attention_rows = attention_payload.get("rows") or []
    attention_codes = {row["code"] for row in attention_rows if row.get("code")}
    # 2026-07-21 수정: change_rate를 attention(키움, 60초 캐시)에서 우선 가져온다 —
    # value-rank(네이버, 7분 캐시)만 쓰면 "실시간 관심 TOP5"와 같은 종목이 스켈핑
    # 후보에도 뜰 때 등락률이 서로 다르게 보이는 문제가 있었다(사용자 지적 — 두
    # 카드가 같은 종목을 "서로 다른 우물"에서 긷고 있었음). attention에 없는
    # 종목만 value-rank의 (최대 7분 묵은) change_rate로 폴백한다.
    attention_change_rate = {
        row["code"]: row.get("change_rate") for row in attention_rows if row.get("code")
    }

    candidates = [
        {
            "code": row["code"],
            "name": row.get("name") or row["code"],
            "market": row.get("market"),
            "change_rate": (
                attention_change_rate[row["code"]]
                if attention_change_rate.get(row["code"]) is not None
                else row.get("change_rate")
            ),
            "turnover": row.get("turnover"),
            "value_rank": row["rank"],
        }
        for row in (value_payload.get("rows") or [])
        if not row.get("is_etf")
    ]

    scored = compute_scalp_scores(candidates, attention_codes)

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
            }
            for c in scored[:limit]
        ],
    }
