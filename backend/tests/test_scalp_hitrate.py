"""Unit tests for app.quant.scalp_hitrate (PLAN.md §5.40-1, 스켈핑 스크리너
적중률 사후 검증 집계).

이 모듈이 집계하는 ``scalp_pick``은 market/code 같은 격리 가능한 키로
필터링하지 않고 테이블 전체를 읽는다(질문 자체가 "스크리너 전체의 적중률"이라
특정 종목/시장 한정이 아니기 때문 — scalp_hitrate.py 모듈 docstring 참고).
그래서 tests/test_regime_backtest.py처럼 실 dev Postgres에 먼 미래 날짜로
테스트 행을 심는 하우스 패턴을 쓰면 실제 프로덕션 scalp_pick 데이터가 항상
함께 집계되어 assert가 불가능하다 — 대신 ``aggregate_scalp_hitrate``(순수
함수)를 synthetic ScalpPick-shaped 객체(SimpleNamespace)만으로 완전히
격리해 검증한다. DB/세션은 전혀 건드리지 않는다.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from app.quant import scalp_hitrate


def _row(
    *,
    date=dt.date(2099, 1, 5),
    entry_rank=1,
    change_rate_5m=None,
    change_rate_15m=None,
    change_rate_30m=None,
    change_rate_60m=None,
    change_rate_eod=None,
):
    return SimpleNamespace(
        date=date,
        entry_rank=entry_rank,
        change_rate_5m=change_rate_5m,
        change_rate_15m=change_rate_15m,
        change_rate_30m=change_rate_30m,
        change_rate_60m=change_rate_60m,
        change_rate_eod=change_rate_eod,
    )


def test_empty_rows_returns_honest_no_data_result():
    result = scalp_hitrate.aggregate_scalp_hitrate([])

    assert result["total_picks"] == 0
    assert result["distinct_days"] == 0
    assert result["date_from"] is None
    assert result["date_to"] is None
    for label, _column in scalp_hitrate.HORIZONS:
        assert result["horizons"][label] == {
            "n": 0,
            "win_rate": None,
            "avg_change_rate": None,
            "median_change_rate": None,
        }
    for bucket, _lo, _hi in scalp_hitrate.RANK_BUCKETS:
        for label, _column in scalp_hitrate.HORIZONS:
            assert result["rank_buckets"][bucket][label] == {
                "n": 0,
                "win_rate": None,
                "avg_change_rate": None,
            }


def test_null_horizon_excluded_from_sample_not_treated_as_zero():
    # 3건 중 2건만 change_rate_5m이 채워짐(하나는 아직 5분이 안 지나 NULL) —
    # 표본수는 2여야지 3(NULL을 0으로 취급)이면 안 된다.
    rows = [
        _row(entry_rank=1, change_rate_5m=2.0),
        _row(entry_rank=2, change_rate_5m=-1.0),
        _row(entry_rank=3, change_rate_5m=None),
    ]
    result = scalp_hitrate.aggregate_scalp_hitrate(rows)

    stat = result["horizons"]["5m"]
    assert stat["n"] == 2
    # 다른 호라이즌은 전부 NULL이라 표본 0.
    assert result["horizons"]["15m"]["n"] == 0


def test_win_rate_and_average_hand_verified():
    # change_rate_5m = [2.0, -1.0, 4.0, -3.0] -> 승(>0) 2건/4건 = 50.0%,
    # 평균 = (2 - 1 + 4 - 3) / 4 = 0.5, 중앙값(정렬 [-3,-1,2,4]) = (-1+2)/2 = 0.5
    rows = [
        _row(entry_rank=1, change_rate_5m=2.0),
        _row(entry_rank=2, change_rate_5m=-1.0),
        _row(entry_rank=3, change_rate_5m=4.0),
        _row(entry_rank=4, change_rate_5m=-3.0),
    ]
    result = scalp_hitrate.aggregate_scalp_hitrate(rows)

    stat = result["horizons"]["5m"]
    assert stat["n"] == 4
    assert stat["win_rate"] == 50.0
    assert stat["avg_change_rate"] == 0.5
    assert stat["median_change_rate"] == 0.5


def test_sample_metadata_total_picks_distinct_days_date_range():
    rows = [
        _row(date=dt.date(2099, 1, 5), entry_rank=1, change_rate_5m=1.0),
        _row(date=dt.date(2099, 1, 5), entry_rank=2, change_rate_5m=1.0),
        _row(date=dt.date(2099, 1, 6), entry_rank=1, change_rate_5m=1.0),
    ]
    result = scalp_hitrate.aggregate_scalp_hitrate(rows)

    assert result["total_picks"] == 3
    assert result["distinct_days"] == 2
    assert result["date_from"] == "2099-01-05"
    assert result["date_to"] == "2099-01-06"


def test_rank_bucket_breakdown_top3_vs_rank4_10():
    rows = [
        # top3: rank 1,2,3 -> change_rate_5m = [10.0, -2.0, 4.0] (승 2/3)
        _row(entry_rank=1, change_rate_5m=10.0),
        _row(entry_rank=2, change_rate_5m=-2.0),
        _row(entry_rank=3, change_rate_5m=4.0),
        # rank4_10: rank 4,5 -> change_rate_5m = [-1.0, -1.0] (승 0/2)
        _row(entry_rank=4, change_rate_5m=-1.0),
        _row(entry_rank=5, change_rate_5m=-1.0),
    ]
    result = scalp_hitrate.aggregate_scalp_hitrate(rows)

    top3 = result["rank_buckets"]["top3"]["5m"]
    assert top3["n"] == 3
    assert top3["win_rate"] == round(2 / 3 * 100, 1)
    assert top3["avg_change_rate"] == round((10.0 - 2.0 + 4.0) / 3, 3)
    # 버킷 통계는 median을 포함하지 않는다(더 작은 표본 — docstring 참고).
    assert "median_change_rate" not in top3

    rank4_10 = result["rank_buckets"]["rank4_10"]["5m"]
    assert rank4_10["n"] == 2
    assert rank4_10["win_rate"] == 0.0
    assert rank4_10["avg_change_rate"] == -1.0


def test_rows_without_entry_rank_excluded_from_bucket_breakdown_but_kept_in_headline():
    rows = [
        _row(entry_rank=None, change_rate_5m=5.0),
        _row(entry_rank=1, change_rate_5m=3.0),
    ]
    result = scalp_hitrate.aggregate_scalp_hitrate(rows)

    # headline은 entry_rank 유무와 무관하게 둘 다 포함.
    assert result["horizons"]["5m"]["n"] == 2
    # top3 버킷은 entry_rank=1인 행만 포함.
    assert result["rank_buckets"]["top3"]["5m"]["n"] == 1
