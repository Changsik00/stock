"""시장(코스피/코스닥)별 투자자 순매수 수집 → market_flow upsert (source='kiwoom').

PLAN.md §6 Phase 1-4. REGISTRY["market_flow"]로 등록된다 (collectors/macro.py와
동일한 패턴 — routers/admin.py가 이 모듈을 import해야 REGISTRY 등록 side effect가
발생하고 POST /api/admin/collect/market_flow 및 스케줄러에서 실행 가능해진다;
admin.py에 이미 그 import 자리 주석이 있다: `# from ..collectors import market_flow
as _market_flow_collector`).

**데이터 소스 (2026-07-19 pykrx → Kiwoom 전환)**: 기존에는 `clients/pykrx_client.py`
(data.krx.co.kr 크롤링)를 썼으나, 2026-02 KRX 데이터 포털 개편으로 `KRX_ID`/`KRX_PW`
(무료 data.krx.co.kr 로그인) 없이는 모든 요청이 거부돼 항상 0행이 적재됐다. 이제는
키움 REST API TR `ka10051`(업종별투자자순매수, `clients/kiwoom.py`의
`sector_investor_net_buy`)로 대체한다 — 이미 유효한 `KIWOOM_APP_KEY`/`SECRET`만
있으면 되고 별도 로그인이 필요 없다. `pykrx_client.py`는 문서화된 대체 소스로 그대로
남겨두되(삭제하지 않음) 이 모듈은 더 이상 그것을 호출하지 않는다. `ka10051`은
`base_dt` 파라미터로 과거 임의 일자를 1콜에 조회할 수 있어 pykrx와 동등하게
백필 가능하다 — 자세한 검증 근거는 `clients/kiwoom.py` 모듈 docstring의
"ka10051(업종별투자자순매수) 추가 검증" 절 참고.

`ka10051` 응답에는 투자자별 순매수 "수량"(`net_volume`)이 없다(금액만 `amt_qty_tp`로
선택 가능하며, 수량까지 받으려면 별도 콜이 필요해 날짜당 호출 수가 2배로 늘어난다).
호출 예산(3년 백필 ≈ 1,500콜, ~1 req/s 기준 ~25분)을 지키기 위해 이 수집기는
금액(`amt_qty_tp="0"`)만 받고 `net_volume`은 항상 `None`으로 적재한다(컬럼 자체는
nullable).

collect_fn 계약(collectors/base.py): 이 함수는 session에 upsert만 수행하고
**commit/rollback은 하지 않는다** — base.run_job이 재시도(3회, 지수 백오프) +
collect_log 기록 + 트랜잭션을 전담한다. 이 모듈 단독으로 검증/백필할 때는
호출자가 직접 session.commit()을 하거나 collectors.base.run_job을 통해 호출해야
한다 (backend/scripts/backfill_market_flow.py 참고).
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients.kiwoom import KiwoomClient
from ..models import MarketFlow
from .base import REGISTRY

logger = logging.getLogger(__name__)

MARKETS = ("kospi", "kosdaq")

SOURCE = "kiwoom"

# mrkt_tp: ka10051 요청 파라미터. "0"=코스피, "1"=코스닥 (clients/kiwoom.py
# 모듈 docstring "ka10051 추가 검증" 절에서 실호출로 확정).
MARKET_TO_MRKT_TP = {"kospi": "0", "kosdaq": "1"}

# inds_netprps 배열에서 시장 전체 합계 행을 고르기 위한 inds_cd. 첫 번째 행이라고
# 가정하지 않고 명시적으로 이 코드를 찾는다(응답 순서가 바뀌어도 방어적으로 동작).
MARKET_TO_SUMMARY_INDS_CD = {"kospi": "001_AL", "kosdaq": "101_AL"}

# ka10051 종합 행의 13개 투자자 필드 -> models.py MarketFlow.investor 표기.
# pykrx 소스(clients/pykrx_client.py)와 이름이 겹치는 항목은 그대로 맞추고,
# 키움에만 있는 3개 분류(기타금융/국가/내국인대우외국인)는 새 값으로 추가한다.
KA10051_FIELD_TO_INVESTOR = {
    "sc_netprps": "금융투자",
    "insrnc_netprps": "보험",
    "invtrt_netprps": "투신",
    "bank_netprps": "은행",
    "jnsinkm_netprps": "연기금",
    "endw_netprps": "기타금융",
    "etc_corp_netprps": "기타법인",
    "ind_netprps": "개인",
    "frgnr_netprps": "외국인",
    "native_trmt_frgnr_netprps": "내국인대우외국인",
    "natn_netprps": "국가",
    "samo_fund_netprps": "사모",
    "orgn_netprps": "기관계",
}


def _parse_int(raw: object) -> int | None:
    """ka10051 숫자 필드는 int로도, (쉼표/부호 포함) 문자열로도 올 수 있어 방어적으로
    파싱한다. None/빈 문자열은 None으로 취급한다."""
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    text = str(raw).strip().replace(",", "")
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        logger.warning("market_flow: ka10051 숫자 필드 파싱 실패, None 처리: %r", raw)
        return None


async def _fetch_kiwoom_flow(
    client: KiwoomClient, market: str, target_date: dt.date
) -> list[dict]:
    """ka10051을 호출해 market의 target_date 시장 전체 종합 행을 13개 투자자별
    {"investor", "net_value", "net_volume"} 딕셔너리 리스트로 변환한다.

    종합 행(`inds_cd` 일치)을 찾지 못하면(예상 밖 응답 형태) 경고만 남기고 빈
    리스트를 반환한다 — 이 저장소 수집기들의 일반적인 "부분 실패는 계속 진행"
    관례(clients/pykrx_client.py 참고)를 따른다.
    """
    data, _headers = await client.sector_investor_net_buy(
        mrkt_tp=MARKET_TO_MRKT_TP[market], base_dt=target_date
    )
    rows = data.get("inds_netprps") or []
    target_inds_cd = MARKET_TO_SUMMARY_INDS_CD[market]
    summary_row = next((row for row in rows if row.get("inds_cd") == target_inds_cd), None)
    if summary_row is None:
        logger.warning(
            "market_flow: ka10051 응답에서 %s(inds_cd=%s) 종합 행을 찾지 못함, "
            "%s 건너뜀 (date=%s)",
            market,
            target_inds_cd,
            market,
            target_date,
        )
        return []

    out: list[dict] = []
    for field, investor in KA10051_FIELD_TO_INVESTOR.items():
        out.append(
            {
                "investor": investor,
                "net_value": _parse_int(summary_row.get(field)),
                "net_volume": None,
            }
        )
    return out


async def collect(session: AsyncSession, target_date: dt.date) -> int:
    """kospi/kosdaq의 target_date 투자자별 순매수를 market_flow에 upsert.

    Returns:
        적재(upsert)한 행 수 (시장 2개 x 투자자 13개 = 최대 26행/일; 휴장일이거나
        ka10051이 종합 행을 못 찾으면 해당 시장은 0행으로 건너뛴다).
    """
    rows_written = 0
    async with KiwoomClient() as client:
        for market in MARKETS:
            flows = await _fetch_kiwoom_flow(client, market, target_date)
            if not flows:
                logger.info("market_flow: no data for %s %s, skipping", market, target_date)
                continue

            for flow in flows:
                stmt = pg_insert(MarketFlow).values(
                    market=market,
                    date=target_date,
                    investor=flow["investor"],
                    net_value=flow["net_value"],
                    net_volume=flow["net_volume"],
                    source=SOURCE,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=[MarketFlow.market, MarketFlow.date, MarketFlow.investor],
                    set_={
                        "net_value": stmt.excluded.net_value,
                        "net_volume": stmt.excluded.net_volume,
                        "source": stmt.excluded.source,
                    },
                )
                await session.execute(stmt)
                rows_written += 1

    return rows_written


REGISTRY["market_flow"] = collect
