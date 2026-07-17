"""Unit tests for app.collectors.flow_path.compute_flow_path (순수 계산부, DB 무관).

가짜 holdings/etf_stats/flow_rank 픽스처로 §4.5 방법론의 핵심 동작을 검증한다:

1. inflow(etf_stats.net_inflow) 우선, 없을 때만 rank(flow_rank 자기자신 net_value)로
   폴백하는 우선순위
2. via_etf_net(S) = Σ inflow(E) × weight(E,S)/100 합산이 여러 ETF에 걸쳐 정확한지
3. direct_net은 flow_rank에 없으면 0이 아니라 None(미관측)이어야 하는 NULL 처리,
   그리고 via_etf_net==0 이면서 direct_net도 없는 코드는 결과에서 아예 빠지는 것
   (§4.5 지시 3번: "via_etf_net이 0이 아닌 모든 구성종목 + direct_net 있는 종목"만 적재)
4. **ETF-in-ETF 1단계 재귀 분해**(§4.5 한계 (b) 2026-07-18 해결) — 파생형 ETF가
   다른 ETF를 보유하면 그 ETF의 자기 구성으로 한 번 더 분해하고(weight×weight),
   2단계 이상은 드롭(dropped_depth2), 내부 ETF의 구성이 유니버스 밖이면 드롭
   (dropped_no_holdings), 그리고 **어떤 경우에도 ETF 코드 자신은 최종 result에
   남지 않는다**(direct_net만 있는 ETF 자기 자신의 행도 제외).
"""

from __future__ import annotations

import datetime as dt

from app.collectors.flow_path import _nearest_date, compute_flow_path

D0 = dt.date(2026, 7, 15)
D1 = dt.date(2026, 7, 16)
D_HOLDINGS = dt.date(2026, 7, 18)

NAMES = {
    "069500": "KODEX 200",
    "102110": "TIGER 200",
    "091160": "KODEX 반도체",
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "122630": "KODEX 레버리지",
    "114800": "KODEX 인버스",
}


def test_nearest_date_picks_closest_and_ties_prefer_earlier():
    assert _nearest_date([dt.date(2026, 7, 10), dt.date(2026, 7, 20)], dt.date(2026, 7, 16)) == dt.date(
        2026, 7, 20
    )
    # tie: 2026-07-14 and 2026-07-18 are both 2 days from 07-16 -> earlier wins.
    assert _nearest_date([dt.date(2026, 7, 14), dt.date(2026, 7, 18)], dt.date(2026, 7, 16)) == dt.date(
        2026, 7, 14
    )
    assert _nearest_date([], dt.date(2026, 7, 16)) is None


def test_inflow_basis_preferred_over_rank_when_etf_stats_available():
    """069500(KODEX 200)은 etf_stats에 값이 있으므로 flow_rank에도 나타나지만
    inflow 근사가 우선 적용돼야 한다."""
    holdings = {"069500": [{"stock_code": "005930", "weight": 30.0}]}
    stats_by_code = {"069500": [(D0, 1000)]}  # net_inflow=1000 million
    flow_rank_rows = [
        {"code": "069500", "name": "KODEX 200", "net_value": 9999, "investor": "foreign"},
    ]

    result, meta = compute_flow_path(D0, holdings, D_HOLDINGS, stats_by_code, flow_rank_rows, NAMES)

    assert meta["inflow"] == 1
    assert meta["rank"] == 0
    # via_etf_net = 1000 * 30% = 300 (not 9999 * 30%)
    assert result["005930"]["via_etf_net"] == 300
    assert result["005930"]["top_etfs"][0]["basis"] == "inflow"
    # KODEX 200 자체는 flow_rank에도 있어 direct_net(9999)을 갖지만, 코드 자신이
    # ETF이므로 최종 result에서 제외된다(§4.5 한계 (b) 2026-07-18 해결 —
    # flow_path는 개별 종목 표이지 ETF 자신의 기록이 아니다).
    assert "069500" not in result


def test_rank_basis_fallback_when_no_etf_stats():
    """102110(TIGER 200)은 etf_stats에 값이 없고 flow_rank에만 있으므로 rank 근사."""
    holdings = {"102110": [{"stock_code": "005930", "weight": 25.0}]}
    stats_by_code = {}
    flow_rank_rows = [
        {"code": "102110", "name": "TIGER 200", "net_value": 400, "investor": "institution"},
    ]

    result, meta = compute_flow_path(D0, holdings, D_HOLDINGS, stats_by_code, flow_rank_rows, NAMES)

    assert meta["rank"] == 1
    assert meta["inflow"] == 0
    assert result["005930"]["via_etf_net"] == 100  # 400 * 25%
    assert result["005930"]["top_etfs"][0]["basis"] == "rank"


def test_etf_with_no_inflow_data_is_skipped_and_contributes_nothing():
    holdings = {"091160": [{"stock_code": "000660", "weight": 40.0}]}
    result, meta = compute_flow_path(D0, holdings, D_HOLDINGS, {}, [], NAMES)

    assert meta["skipped"] == 1
    # No via contribution and no direct_net -> excluded entirely from the result.
    assert "000660" not in result
    assert result == {}


def test_direct_net_is_null_not_zero_when_stock_absent_from_flow_rank():
    """005930이 flow_rank 랭킹에 없으면 direct_net은 None이어야 한다(0이 아님) —
    via_etf_net만으로 결과에 포함된다."""
    holdings = {"069500": [{"stock_code": "005930", "weight": 30.0}]}
    stats_by_code = {"069500": [(D0, 1000)]}
    flow_rank_rows: list[dict] = []  # 005930도 069500도 랭킹에 없음

    result, _meta = compute_flow_path(D0, holdings, D_HOLDINGS, stats_by_code, flow_rank_rows, NAMES)

    assert result["005930"]["direct_net"] is None
    assert result["005930"]["via_etf_net"] == 300


def test_via_etf_net_sums_contributions_across_multiple_etfs():
    """삼성전자가 KODEX 200 + TIGER 200 두 ETF에 모두 들어있으면 기여분이 합산된다
    (손계산 검증에 대응하는 케이스 — report의 §손계산 대조와 동일한 형태)."""
    holdings = {
        "069500": [{"stock_code": "005930", "weight": 30.0}, {"stock_code": "000660", "weight": 10.0}],
        "102110": [{"stock_code": "005930", "weight": 28.0}],
    }
    stats_by_code = {"069500": [(D0, 1000)]}
    flow_rank_rows = [
        {"code": "102110", "name": "TIGER 200", "net_value": 500, "investor": "foreign"},
    ]

    result, meta = compute_flow_path(D0, holdings, D_HOLDINGS, stats_by_code, flow_rank_rows, NAMES)

    # 069500: inflow=1000 -> 005930 contributes 300, 000660 contributes 100
    # 102110: rank=500 -> 005930 contributes 140
    assert result["005930"]["via_etf_net"] == 300 + 140
    assert result["000660"]["via_etf_net"] == 100
    assert len(result["005930"]["top_etfs"]) == 2
    top_codes = {t["code"] for t in result["005930"]["top_etfs"]}
    assert top_codes == {"069500", "102110"}
    assert meta["etf_count"] == 2


def test_top_etfs_truncated_to_top_5_by_absolute_contribution():
    holdings = {f"ETF{i}": [{"stock_code": "005930", "weight": 10.0}] for i in range(7)}
    stats_by_code = {f"ETF{i}": [(D0, (i + 1) * 100)] for i in range(7)}
    names = {**NAMES, **{f"ETF{i}": f"ETF{i}이름" for i in range(7)}}

    result, _meta = compute_flow_path(D0, holdings, D_HOLDINGS, stats_by_code, [], names)

    top = result["005930"]["top_etfs"]
    assert len(top) == 5
    # ETF6 (contrib 70) should rank first, ETF0 (contrib 10) should be dropped.
    assert top[0]["code"] == "ETF6"
    assert all(t["code"] != "ETF0" for t in top)


def test_missing_weight_treated_as_zero_contribution():
    holdings = {"069500": [{"stock_code": "005930", "weight": None}]}
    stats_by_code = {"069500": [(D0, 1000)]}

    result, _meta = compute_flow_path(D0, holdings, D_HOLDINGS, stats_by_code, [], NAMES)

    # via_etf_net rounds to 0 and there's no direct_net -> excluded from the result.
    assert result == {}


def test_nearest_stats_date_used_when_target_date_has_no_exact_match():
    """etf_stats에 target_date(D1)와 정확히 일치하는 행이 없으면 가장 가까운 날짜
    (D0)를 쓰고, 그 날짜가 top_etfs에 기록된다."""
    holdings = {"069500": [{"stock_code": "005930", "weight": 50.0}]}
    stats_by_code = {"069500": [(D0, 200)]}

    result, _meta = compute_flow_path(D1, holdings, D_HOLDINGS, stats_by_code, [], NAMES)

    assert result["005930"]["via_etf_net"] == 100  # 200 * 50%
    assert result["005930"]["top_etfs"][0]["date"] == D0.isoformat()


# ---------------------------------------------------------------------------
# ETF-in-ETF 1단계 재귀 분해 (§4.5 한계 (b) 2026-07-18 해결)
# ---------------------------------------------------------------------------


def test_etf_in_etf_one_level_redistribution():
    """KODEX 레버리지(122630)가 top10에 KODEX 200(069500)을 보유 -> 목적지로
    취급하지 않고 KODEX 200 자신의 구성(005930)까지 한 번 더 분해한다.
    기여액은 outer_weight × inner_weight로 곱해진다."""
    holdings = {
        "122630": [{"stock_code": "069500", "weight": 100.0}],
        "069500": [{"stock_code": "005930", "weight": 30.0}],
    }
    stats_by_code = {"122630": [(D0, 1000)]}  # 내부 ETF(069500)는 자체 inflow 없음

    result, meta = compute_flow_path(D0, holdings, D_HOLDINGS, stats_by_code, [], NAMES)

    # 1000(레버리지 inflow) * 100%(069500 비중) * 30%(005930 비중) = 300
    assert result["005930"]["via_etf_net"] == 300
    assert "069500" not in result  # 중간 ETF는 목적지가 아니다
    assert "122630" not in result  # 원천 ETF도 최종 결과에 남지 않는다

    top = result["005930"]["top_etfs"][0]
    assert top["code"] == "122630"  # top_etfs 명의는 원천 ETF
    assert top["name"] == "KODEX 레버리지→KODEX 200"  # 경유 화살표 표기
    assert top["via"] == "069500"
    assert top["via_name"] == "KODEX 200"
    assert top["contrib"] == 300

    assert meta["dropped_depth2"] == 0
    assert meta["dropped_no_holdings"] == 0


def test_etf_in_etf_depth2_is_dropped_not_recursed_further():
    """A -> B(ETF) -> C(ETF) -> 005930 처럼 2단계 이상 체인이면, 1단계(B)까지만
    보고 그 다음(C)은 드롭한다(무한 재귀 방지 안전장치 (a))."""
    holdings = {
        "A": [{"stock_code": "B", "weight": 100.0}],
        "B": [{"stock_code": "C", "weight": 100.0}],
        "C": [{"stock_code": "005930", "weight": 50.0}],
    }
    stats_by_code = {"A": [(D0, 1000)]}
    names = {**NAMES, "A": "펀드A", "B": "펀드B", "C": "펀드C"}

    result, meta = compute_flow_path(D0, holdings, D_HOLDINGS, stats_by_code, [], names)

    assert result == {}  # 005930까지 기여가 도달하지 않는다
    assert meta["dropped_depth2"] == 1
    assert meta["dropped_no_holdings"] == 0


def test_inner_etf_outside_universe_is_dropped():
    """외부 ETF가 보유한 내부 코드가 stocks.is_etf=True(ETF는 맞음)이지만
    etf_holdings에 그 자신의 구성 스냅샷이 없으면(유니버스 밖 — 예: 인버스/선물형이라
    애초에 주식 구성이 없거나 top300 밖) 그 기여를 드롭한다(안전장치 (b))."""
    holdings = {"122630": [{"stock_code": "114800", "weight": 50.0}]}
    stats_by_code = {"122630": [(D0, 1000)]}

    result, meta = compute_flow_path(
        D0, holdings, D_HOLDINGS, stats_by_code, [], NAMES, etf_codes={"114800"}
    )

    assert result == {}
    assert meta["dropped_no_holdings"] == 1
    assert meta["dropped_depth2"] == 0


def test_etf_codes_param_excludes_pure_direct_net_etf_row():
    """holdings에 등장하지 않는 ETF라도(자기 구성 top10을 안 보유해 origin으로
    한 번도 안 나옴) stocks.is_etf=True로 알려져 있고 flow_rank에 자기 매매
    기록(direct_net)만 있는 경우에도 최종 result에서 제외돼야 한다 — flow_path에
    ETF 코드가 남지 않게 보장하는 규칙은 via_etf_net 유무와 무관하다."""
    flow_rank_rows = [
        {"code": "114800", "name": "KODEX 인버스", "net_value": 500, "investor": "foreign"},
    ]

    result, _meta = compute_flow_path(
        D0, {}, D_HOLDINGS, {}, flow_rank_rows, NAMES, etf_codes={"114800"}
    )

    assert result == {}
