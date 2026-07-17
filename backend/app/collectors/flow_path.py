"""ETF look-through 배치 — 종목별 수급 경로 분해(직접 vs ETF 경유) (PLAN.md §4.5/§6 3.5-3).

방법론(§4.5 그대로):

1. **via_etf_net(S)** = Σ_E [ inflow(E) × weight(E,S)/100 ]. ETF E의 순유입 inflow(E)는
   우선순위로 구한다: (a) ``etf_stats.net_inflow`` — target_date에 정확히 있으면 그
   값, 없으면 그 ETF에 대해 net_inflow가 non-null인 날짜 중 **가장 가까운 날짜**를
   골라 쓴다(§4.5 "T-1 PDF 원칙", 날짜 어긋남 허용). (b) etf_stats에 그 ETF의
   non-null net_inflow가 전혀 없으면, target_date의 ``flow_rank``에서 그 ETF 코드의
   외인+기관 net_value 합을 2차 근사로 쓴다(ETF도 유통시장에서 매매되는 종목이라
   flow_rank에 직접 잡힐 수 있음 — 그 경우에만 해당). (c) 둘 다 없으면 그 ETF는
   기여분 없이(inflow=0 취급, 실제로는 "미관측") 건너뛴다.
   두 근사가 섞이므로 각 기여 ETF마다 ``top_etfs``에 ``basis: "inflow"|"rank"``와
   실제 사용한 날짜(``date``)를 기록해 어떻게 매칭했는지 추적 가능하게 한다.

2. **direct_net(S)** = flow_rank에서 개별주(또는 ETF 자기 자신) S의 외인+기관
   net_value 합(target_date **정확히 일치**하는 스냅샷만 사용 — flow_rank는 날짜별
   랭킹 스냅샷이라 다른 날짜로 대체하면 의미가 없다). flow_rank 랭킹에 없으면
   NULL — 진짜 순매수가 0이 아니라 "미관측"이라는 뜻이다(§4.5 지시).
   ka10059(키움, 종목별 전체 투자자 수급)가 붙으면 이 NULL 자리가 실측치로
   대체될 예정이다(현재는 상위 40위 밖 종목은 전부 NULL).

3. 날짜별로 via_etf_net이 0이 아닌 모든 구성종목 + direct_net이 있는 모든 종목을
   합쳐 ``flow_path``에 upsert한다(§4.5 지시 3번 그대로).

4. holdings(etf_holdings)·stats(etf_stats) 스냅샷은 날짜가 성긴(현재 각각 단일
   스냅샷) 데이터라 target_date에 정확히 맞는 행이 없는 경우가 대부분이다 —
   **holdings는 전체적으로 가장 가까운 스냅샷 날짜 하나**(모든 ETF가 같은 날 한 번에
   수집되므로, PLAN.md §4.5 "T-1 PDF 원칙" 준수)를 골라 그 날의 구성 전체를 쓰고,
   **stats는 ETF별로 개별적으로** 가장 가까운 non-null net_inflow 날짜를 고른다
   (ETF마다 관측 가능한 날짜가 다를 수 있으므로).

이 모듈은 순수 계산부(``compute_flow_path``, DB 무관 — 픽스처로 단위테스트 가능,
tests/test_flow_path_collector.py 참고)와 DB I/O부(``collect_flow_path`` 및
``_load_*`` 헬퍼)를 분리한다(collectors/ohlcv.py의 fetch/collect 분리 패턴과 동일).

REGISTRY["flow_path"]로 등록된다 — routers/admin.py에서 import해야 실제로
POST /api/admin/collect/flow_path 및 스케줄러에서 실행 가능해진다(admin.py에 import
한 줄 추가는 이 작업 범위에 포함).
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import EtfHolding, EtfStat, FlowPath, FlowRank, Stock
from .base import REGISTRY

logger = logging.getLogger(__name__)

# top_etfs JSONB에 저장할 기여 ETF 최대 개수 (per-stock, contrib 절대값 내림차순).
TOP_ETFS_LIMIT = 5


def _nearest_date(available: list[dt.date], target: dt.date) -> dt.date | None:
    """available 중 target에 가장 가까운 날짜. 동률이면 더 이른(과거) 날짜 우선
    (§4.5 "T-1 PDF 원칙" — 미래 스냅샷보다 과거 스냅샷을 약하게 선호)."""
    if not available:
        return None
    return min(available, key=lambda d: (abs((d - target).days), d))


def compute_flow_path(
    target_date: dt.date,
    holdings: dict[str, list[dict]],
    holdings_date: dt.date,
    stats_by_code: dict[str, list[tuple[dt.date, int]]],
    flow_rank_rows: list[dict],
    names: dict[str, str],
) -> tuple[dict[str, dict], dict]:
    """순수 계산부 — DB 세션 없이 픽스처만으로 실행 가능(단위테스트 대상).

    Args:
        target_date: 계산 대상 날짜(flow_path.date로 적재될 값).
        holdings: etf_code -> [{"stock_code", "weight"(%, float|None)}, ...] —
            holdings_date 스냅샷의 구성종목(보통 top10).
        holdings_date: holdings가 실제로 속한 날짜(target_date와 다를 수 있음 —
            §4.5 "T-1 PDF 원칙", top_etfs에도 이 날짜가 기록된다).
        stats_by_code: etf_code -> [(date, net_inflow_million), ...] — net_inflow가
            non-null인 행만. 정렬 불필요(내부에서 최근접 탐색).
        flow_rank_rows: target_date **정확히 일치**하는 flow_rank 스냅샷 행들
            ({"code", "name", "net_value", "investor", ...}).
        names: code -> 종목/ETF명 (없으면 top_etfs/응답에서 code로 대체됨 — 이
            함수는 fallback을 하지 않고 호출자가 채워서 넘긴다).

    Returns:
        (result, meta)
        - result: code -> {"direct_net": int|None, "via_etf_net": int, "top_etfs": [...]}
          via_etf_net이 0이 아니거나 direct_net이 있는 코드만 포함(§4.5 지시 3번).
        - meta: 기여 ETF 중 basis별 개수 등 배치 message용 통계.
    """
    # direct_net: 같은 코드가 investor(foreign/institution) 여러 행으로 나타날 수
    # 있으므로 합산한다. 이 값은 (a) 개별주의 직접 순매수 근사치로도, (b) 그 코드가
    # ETF일 때 inflow(E)의 "rank" 근사치로도 재사용된다.
    direct_map: dict[str, int] = {}
    for r in flow_rank_rows:
        net_value = r.get("net_value")
        if net_value is None:
            continue
        direct_map[r["code"]] = direct_map.get(r["code"], 0) + net_value

    inflow_info: dict[str, dict] = {}  # etf_code -> {"value", "basis", "date"}
    basis_counts = {"inflow": 0, "rank": 0, "skipped": 0}

    for etf_code in holdings:
        stats_rows = stats_by_code.get(etf_code)
        if stats_rows:
            available_dates = [d for d, _ in stats_rows]
            nearest = _nearest_date(available_dates, target_date)
            value = next(v for d, v in stats_rows if d == nearest)
            inflow_info[etf_code] = {"value": value, "basis": "inflow", "date": nearest}
            basis_counts["inflow"] += 1
            continue

        rank_value = direct_map.get(etf_code)
        if rank_value is not None:
            inflow_info[etf_code] = {"value": rank_value, "basis": "rank", "date": target_date}
            basis_counts["rank"] += 1
            continue

        basis_counts["skipped"] += 1
        # inflow_info에 항목을 넣지 않음 -> 아래 루프에서 자동으로 기여분 0 처리

    via_map: dict[str, float] = {}
    contrib_map: dict[str, list[dict]] = {}

    for etf_code, entries in holdings.items():
        info = inflow_info.get(etf_code)
        if info is None:
            continue
        inflow_value = info["value"]
        for h in entries:
            stock_code = h["stock_code"]
            weight = h.get("weight") or 0.0
            contrib = inflow_value * weight / 100.0
            via_map[stock_code] = via_map.get(stock_code, 0.0) + contrib
            contrib_map.setdefault(stock_code, []).append(
                {
                    "code": etf_code,
                    "name": names.get(etf_code, etf_code),
                    "contrib": contrib,
                    "basis": info["basis"],
                    "date": info["date"].isoformat(),
                }
            )

    result: dict[str, dict] = {}
    for code in set(via_map) | set(direct_map):
        via_raw = via_map.get(code, 0.0)
        via_rounded = int(round(via_raw))
        direct_net = direct_map.get(code)
        if via_rounded == 0 and direct_net is None:
            continue

        top = sorted(contrib_map.get(code, []), key=lambda c: abs(c["contrib"]), reverse=True)[
            :TOP_ETFS_LIMIT
        ]
        for t in top:
            t["contrib"] = int(round(t["contrib"]))

        result[code] = {
            "direct_net": direct_net,
            "via_etf_net": via_rounded,
            "top_etfs": top,
        }

    meta = {
        "target_date": target_date.isoformat(),
        "holdings_date": holdings_date.isoformat(),
        "etf_count": len(holdings),
        **basis_counts,
    }
    return result, meta


# ---------------------------------------------------------------------------
# DB I/O
# ---------------------------------------------------------------------------


async def _load_flow_rank_rows(session: AsyncSession, target_date: dt.date) -> list[dict]:
    # direct_net은 순매수 기준을 유지한다(PLAN.md §6 3.5-2b — flow_rank가 side(buy/sell)
    # 로 확장된 뒤에도 look-through 계산은 그대로 매수 랭킹만 쓴다. side='sell' 행까지
    # 합치면 "직접 순매수"가 아니라 "매수+매도 합계"가 돼 의미가 달라진다).
    stmt = select(FlowRank).where(FlowRank.date == target_date, FlowRank.side == "buy")
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "code": r.code,
            "name": r.name,
            "net_value": r.net_value,
            "investor": r.investor,
            "is_etf": r.is_etf,
        }
        for r in rows
    ]


async def _load_holdings_nearest(
    session: AsyncSession, target_date: dt.date
) -> tuple[dt.date | None, dict[str, list[dict]]]:
    dates = (await session.execute(select(EtfHolding.date).distinct())).scalars().all()
    nearest = _nearest_date(list(dates), target_date)
    if nearest is None:
        return None, {}

    stmt = select(EtfHolding).where(EtfHolding.date == nearest)
    rows = (await session.execute(stmt)).scalars().all()
    holdings: dict[str, list[dict]] = {}
    for r in rows:
        holdings.setdefault(r.etf_code, []).append(
            {
                "stock_code": r.stock_code,
                "weight": float(r.weight) if r.weight is not None else None,
            }
        )
    return nearest, holdings


async def _load_stats_by_code(
    session: AsyncSession, etf_codes: list[str]
) -> dict[str, list[tuple[dt.date, int]]]:
    if not etf_codes:
        return {}
    stmt = select(EtfStat).where(
        EtfStat.code.in_(etf_codes), EtfStat.net_inflow.isnot(None)
    )
    rows = (await session.execute(stmt)).scalars().all()
    out: dict[str, list[tuple[dt.date, int]]] = {}
    for r in rows:
        out.setdefault(r.code, []).append((r.date, r.net_inflow))
    return out


async def _load_names(session: AsyncSession, codes: set[str]) -> dict[str, str]:
    if not codes:
        return {}
    stmt = select(Stock.code, Stock.name).where(Stock.code.in_(codes))
    rows = (await session.execute(stmt)).all()
    return dict(rows)


async def _upsert_flow_path(
    session: AsyncSession, target_date: dt.date, result: dict[str, dict]
) -> int:
    count = 0
    for code, data in result.items():
        stmt = pg_insert(FlowPath).values(
            code=code,
            date=target_date,
            direct_net=data["direct_net"],
            via_etf_net=data["via_etf_net"],
            top_etfs=data["top_etfs"] or None,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[FlowPath.code, FlowPath.date],
            set_={
                "direct_net": stmt.excluded.direct_net,
                "via_etf_net": stmt.excluded.via_etf_net,
                "top_etfs": stmt.excluded.top_etfs,
            },
        )
        await session.execute(stmt)
        count += 1
    return count


async def collect_flow_path(session: AsyncSession, target_date: dt.date) -> tuple[int, str | None]:
    """target_date 하루치 flow_path를 계산·upsert한다.

    holdings/etf_stats 스냅샷이 성긴 현재 데이터 상태에서는 target_date 자체보다
    "가장 가까운 가용 스냅샷"을 쓰는 경우가 대부분이다(§4.5 "T-1 PDF 원칙") — 실제로
    어떤 날짜를 매칭했는지는 반환 message와 각 flow_path 행의 top_etfs[].date에
    남는다.
    """
    holdings_date, holdings = await _load_holdings_nearest(session, target_date)
    if holdings_date is None:
        return 0, "etf_holdings 스냅샷이 비어 있어 via_etf_net을 계산할 수 없음"

    flow_rank_rows = await _load_flow_rank_rows(session, target_date)
    stats_by_code = await _load_stats_by_code(session, list(holdings.keys()))

    all_codes: set[str] = set(holdings.keys())
    for entries in holdings.values():
        all_codes.update(h["stock_code"] for h in entries)
    all_codes.update(r["code"] for r in flow_rank_rows)
    names = await _load_names(session, all_codes)
    for r in flow_rank_rows:
        # flow_rank는 이름을 자체적으로 갖고 있으므로(네이버 파싱 시점 값) stocks
        # 테이블에 아직 없는 코드에 대한 폴백으로 사용한다.
        names.setdefault(r["code"], r.get("name"))

    result, meta = compute_flow_path(
        target_date, holdings, holdings_date, stats_by_code, flow_rank_rows, names
    )

    count = await _upsert_flow_path(session, target_date, result)

    message = (
        f"holdings={meta['holdings_date']}, ETF {meta['etf_count']}개 중 "
        f"inflow기준 {meta['inflow']} / rank근사 {meta['rank']} / 미관측(skip) "
        f"{meta['skipped']}, flow_rank 행 {len(flow_rank_rows)}개, 결과 {count}종목"
    )
    return count, message


REGISTRY["flow_path"] = collect_flow_path
