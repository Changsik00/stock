"""Unit tests for app.collectors.flow_path.compute_flow_path (순수 계산부, DB 무관).

가짜 holdings/etf_stats/flow_rank 픽스처로 §4.5 방법론의 세 가지 핵심 동작을 검증한다:

1. inflow(etf_stats.net_inflow) 우선, 없을 때만 rank(flow_rank 자기자신 net_value)로
   폴백하는 우선순위
2. via_etf_net(S) = Σ inflow(E) × weight(E,S)/100 합산이 여러 ETF에 걸쳐 정확한지
3. direct_net은 flow_rank에 없으면 0이 아니라 None(미관측)이어야 하는 NULL 처리,
   그리고 via_etf_net==0 이면서 direct_net도 없는 코드는 결과에서 아예 빠지는 것
   (§4.5 지시 3번: "via_etf_net이 0이 아닌 모든 구성종목 + direct_net 있는 종목"만 적재)
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
    # KODEX 200 자체도 flow_rank에 있으므로 direct_net을 갖는다.
    assert result["069500"]["direct_net"] == 9999


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
