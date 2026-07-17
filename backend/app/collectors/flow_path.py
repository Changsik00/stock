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
   합쳐 ``flow_path``에 upsert한다(§4.5 지시 3번 그대로). **단, 그 코드 자체가
   ETF면(= stocks.is_etf 또는 etf_holdings에 자기 구성이 있음) 최종 결과에서
   제외한다** — flow_path는 "개별 종목"의 경로 분해 표이지 ETF 자신의 매매
   기록이 아니다(§4.5 한계 (b) 2026-07-18 해결, 아래 3.5 참고).

3.5. **ETF-in-ETF 1단계 재귀 분해 (§4.5 한계 (b) 2026-07-18 해결)**: 파생형
   ETF(예: KODEX 레버리지)가 top10 구성에 다른 ETF(예: KODEX 200)를 보유하는
   경우, 그 보유를 최종 목적지로 취급하지 않고 **그 내부 ETF 자신의 구성종목
   으로 한 번 더 분해**한다 — 기여액 = outer_inflow × outer_weight/100 ×
   inner_weight/100. 재분배된 기여의 ``top_etfs`` 항목은 **원천 ETF**(예: KODEX
   레버리지) 명의로 기록하되(``code``/``name``이 원천 ETF), 경유를 명시하려고
   ``name``에 "원천ETF명→내부ETF명" 화살표를 붙이고, 별도 ``via``(내부 ETF
   코드)·``via_name`` 필드도 추가한다(재귀가 아닌 일반 기여 항목에는 이 두
   필드가 없다 — 프런트가 옵셔널 체이닝으로 구분).

   안전장치(둘 다 collect_flow_path의 message와 meta에 카운트로 남는다):
   - **(a) 무한 루프 방지**: 재귀는 1단계까지만. 내부 ETF의 구성에 또 ETF가
     나오면(2단계 이상) 그 기여는 드롭하고 ``dropped_depth2``로 센다.
   - **(b) 유니버스 밖 드롭**: 외부에서 보유한 내부 ETF가 ETF는 맞지만
     (stocks.is_etf) etf_holdings에 그 자신의 구성 스냅샷이 없으면(수집
     유니버스 밖 — collectors/etf_master.py TOP_N=300 밖이거나 인버스/선물형이라
     애초에 주식 구성이 없는 경우) 그 기여는 드롭하고 ``dropped_no_holdings``로
     센다.
   - **(c) 검증**: 최종 flow_path 행에는 ETF 코드가 절대 남지 않는다(위 3번
     "제외" 규칙) — `SELECT * FROM flow_path fp JOIN stocks s ON s.code=fp.code
     WHERE s.is_etf` 가 0건이어야 한다.

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

from sqlalchemy import delete, select
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


def _via_name(names: dict[str, str], origin_code: str, via_code: str) -> str:
    """재분배된 기여의 표시명 — "원천ETF명→내부ETF명" (모듈 docstring 3.5 참고)."""
    return f"{names.get(origin_code, origin_code)}→{names.get(via_code, via_code)}"


def compute_flow_path(
    target_date: dt.date,
    holdings: dict[str, list[dict]],
    holdings_date: dt.date,
    stats_by_code: dict[str, list[tuple[dt.date, int]]],
    flow_rank_rows: list[dict],
    names: dict[str, str],
    etf_codes: set[str] | None = None,
) -> tuple[dict[str, dict], dict]:
    """순수 계산부 — DB 세션 없이 픽스처만으로 실행 가능(단위테스트 대상).

    Args:
        target_date: 계산 대상 날짜(flow_path.date로 적재될 값).
        holdings: etf_code -> [{"stock_code", "weight"(%, float|None)}, ...] —
            holdings_date 스냅샷의 구성종목(보통 top10). 이 dict의 key(=자기
            구성이 알려진 ETF)는 재귀 분해 대상 판정에도 쓰인다.
        holdings_date: holdings가 실제로 속한 날짜(target_date와 다를 수 있음 —
            §4.5 "T-1 PDF 원칙", top_etfs에도 이 날짜가 기록된다).
        stats_by_code: etf_code -> [(date, net_inflow_million), ...] — net_inflow가
            non-null인 행만. 정렬 불필요(내부에서 최근접 탐색).
        flow_rank_rows: target_date **정확히 일치**하는 flow_rank 스냅샷 행들
            ({"code", "name", "net_value", "investor", ...}).
        names: code -> 종목/ETF명 (없으면 top_etfs/응답에서 code로 대체됨 — 이
            함수는 fallback을 하지 않고 호출자가 채워서 넘긴다).
        etf_codes: stocks.is_etf=True인 코드 전체(§4.5 한계 (b) 해결 — 자기 구성이
            etf_holdings에 없는 ETF도 "ETF다"라고 판정하려면 holdings.keys()만으로는
            부족하다, 인버스/선물형처럼 구성이 통째로 없는 경우가 그 예). None이면
            holdings.keys()만으로 판정한다(하위호환 — 기존 호출자/테스트 안 깨짐).

    Returns:
        (result, meta)
        - result: code -> {"direct_net": int|None, "via_etf_net": int, "top_etfs": [...]}
          via_etf_net이 0이 아니거나 direct_net이 있는 코드만 포함(§4.5 지시 3번).
          **code 자신이 ETF면 결과에서 제외한다**(§4.5 한계 (b) 2026-07-18 해결 —
          flow_path는 개별 종목 표이지 ETF 자신의 기록이 아니다).
        - meta: 기여 ETF 중 basis별 개수, 재귀 드롭 건수 등 배치 message용 통계.
    """
    known_etf = set(holdings.keys()) | (etf_codes or set())

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
    dropped_depth2 = 0
    dropped_no_holdings = 0

    for etf_code, entries in holdings.items():
        info = inflow_info.get(etf_code)
        if info is None:
            continue
        inflow_value = info["value"]
        for h in entries:
            stock_code = h["stock_code"]
            weight = h.get("weight") or 0.0
            contrib = inflow_value * weight / 100.0

            if stock_code not in known_etf:
                # 일반 케이스: 개별 주식 보유 -> 그대로 기여.
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
                continue

            # ETF-in-ETF: 목적지가 다른 ETF다 -> 1단계 재귀 분해(모듈 docstring 3.5).
            inner_entries = holdings.get(stock_code)
            if not inner_entries:
                # (b) 안전장치: 구성이 유니버스 밖(etf_holdings에 없음) -> 드롭.
                dropped_no_holdings += 1
                continue
            for inner_h in inner_entries:
                inner_code = inner_h["stock_code"]
                if inner_code in known_etf:
                    # (a) 안전장치: 2단계 이상 ETF-in-ETF -> 무한 재귀 방지, 드롭.
                    dropped_depth2 += 1
                    continue
                inner_weight = inner_h.get("weight") or 0.0
                inner_contrib = contrib * inner_weight / 100.0
                via_map[inner_code] = via_map.get(inner_code, 0.0) + inner_contrib
                contrib_map.setdefault(inner_code, []).append(
                    {
                        # top_etfs 명의는 원천 ETF(예: KODEX 레버리지) — 경유지는
                        # name 화살표 표기 + via/via_name 필드로 별도 노출.
                        "code": etf_code,
                        "name": _via_name(names, etf_code, stock_code),
                        "contrib": inner_contrib,
                        "basis": info["basis"],
                        "date": info["date"].isoformat(),
                        "via": stock_code,
                        "via_name": names.get(stock_code, stock_code),
                    }
                )

    result: dict[str, dict] = {}
    for code in set(via_map) | set(direct_map):
        if code in known_etf:
            # (c) 안전장치: code 자신이 ETF면 최종 결과에서 제외 — flow_path에
            # ETF 코드가 남지 않게 보장(모듈 docstring 3번/3.5-(c)).
            continue

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
        "dropped_depth2": dropped_depth2,
        "dropped_no_holdings": dropped_no_holdings,
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


async def _load_is_etf_codes(session: AsyncSession) -> set[str]:
    """stocks.is_etf=True인 코드 전체 — ETF-in-ETF 재귀 분해 판정용(§4.5 한계 (b)).
    holdings.keys()(자기 구성이 알려진 ETF)만으로는 부족한 이유: 인버스/선물형
    ETF는 애초에 주식 구성이 없어 etf_holdings에 행이 없지만(collectors/etf_master.py
    "자연 탈락" 참고) 여전히 stocks.is_etf=True다 — 그런 코드가 다른 ETF의 top10에
    보유돼 있으면(드묾) "ETF다"라고 판정해 드롭해야지 개별 종목처럼 취급하면 안 된다."""
    stmt = select(Stock.code).where(Stock.is_etf.is_(True))
    rows = (await session.execute(stmt)).scalars().all()
    return set(rows)


async def _delete_stale_flow_path_rows(
    session: AsyncSession, target_date: dt.date, keep_codes: set[str]
) -> int:
    """target_date의 기존 flow_path 행 중 이번 계산 결과(``result``)에 더 이상
    없는 코드는 삭제한다 (2026-07-18 버그 수정) — upsert만으로는 "이전 실행이
    남긴 stale 행"이 영원히 남는다. 실제로 이 버그 때문에 §4.5 한계 (b)를 고쳐서
    compute_flow_path가 더는 ETF 코드를 결과에 내지 않게 만든 뒤에도, 그 전에
    이미 upsert돼 있던 ETF 코드 행이 테이블에 그대로 남아 "flow_path에 ETF 코드
    0건" 요건을 만족하지 못했다 — collectors/etf_master.py의 ``_replace_holdings``
    (delete-then-insert)와 동일한 필요성이다."""
    stmt = delete(FlowPath).where(FlowPath.date == target_date)
    if keep_codes:
        stmt = stmt.where(FlowPath.code.notin_(keep_codes))
    exec_result = await session.execute(stmt)
    return exec_result.rowcount or 0


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
    etf_codes = await _load_is_etf_codes(session)

    all_codes: set[str] = set(holdings.keys())
    for entries in holdings.values():
        all_codes.update(h["stock_code"] for h in entries)
    all_codes.update(r["code"] for r in flow_rank_rows)
    all_codes.update(etf_codes)
    names = await _load_names(session, all_codes)
    for r in flow_rank_rows:
        # flow_rank는 이름을 자체적으로 갖고 있으므로(네이버 파싱 시점 값) stocks
        # 테이블에 아직 없는 코드에 대한 폴백으로 사용한다.
        names.setdefault(r["code"], r.get("name"))

    result, meta = compute_flow_path(
        target_date, holdings, holdings_date, stats_by_code, flow_rank_rows, names, etf_codes
    )

    deleted = await _delete_stale_flow_path_rows(session, target_date, set(result.keys()))
    count = await _upsert_flow_path(session, target_date, result)

    message = (
        f"holdings={meta['holdings_date']}, ETF {meta['etf_count']}개 중 "
        f"inflow기준 {meta['inflow']} / rank근사 {meta['rank']} / 미관측(skip) "
        f"{meta['skipped']}, flow_rank 행 {len(flow_rank_rows)}개, 결과 {count}종목"
        f" (ETF-in-ETF 재분배 드롭: 2단계이상 {meta['dropped_depth2']}건 / "
        f"유니버스밖(구성 미상) {meta['dropped_no_holdings']}건, stale 행 삭제 "
        f"{deleted}건)"
    )
    return count, message


REGISTRY["flow_path"] = collect_flow_path
