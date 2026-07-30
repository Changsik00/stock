"""Unit tests for app.quant.expiry_pattern (PLAN.md §5.42-2, 만기 수렴 패턴).

전부 synthetic date -> close dict로 검증한다(모듈 docstring "순수 함수 분리"
참고 — index_ohlcv는 공유 프로덕션 데이터라 격리 가능한 키가 없어 실 DB를
쓰지 않는다). ``aggregate_expiry_pattern``이 유일한 대상이다.
"""

from __future__ import annotations

import datetime as dt

from app.quant.expiry_pattern import aggregate_expiry_pattern

# 40일 연속 날짜(주말 구분 없이 전부 "거래일"로 취급 — 이 모듈은 날짜의 요일이
# 아니라 dict에 존재하는지만 본다). basis = futures - spot = 날짜 인덱스(i) 그대로
# 되도록 값을 설계해 평균/중앙값을 손으로 검산할 수 있게 한다.
_BASE = dt.date(2024, 1, 1)
_DATES = [_BASE + dt.timedelta(days=i) for i in range(40)]


def _series():
    spot = {d: 100.0 for d in _DATES}
    futures = {d: 100.0 + i for i, d in enumerate(_DATES)}
    return futures, spot


# 4개 사이클의 만기일 = index 2/12/22/32. 첫 사이클(index 2)은 max_lookback=3일 때
# D-3(index -1)에 데이터가 없어 그 사이클만 D-3 집계에서 빠져야 한다(표본수 축소 검증).
_EXPIRIES = [_DATES[2], _DATES[12], _DATES[22], _DATES[32]]


def test_d_day_alignment_and_mean_median():
    futures, spot = _series()
    result = aggregate_expiry_pattern(futures, spot, _EXPIRIES, max_lookback_days=3, min_cycles=3)

    assert result["reason"] is None
    assert result["cycle_count"] == 4
    assert result["date_from"] == _DATES[2].isoformat()
    assert result["date_to"] == _DATES[32].isoformat()
    assert result["max_lookback_days"] == 3

    by_d_day = {p["d_day"]: p for p in result["points"]}
    assert set(by_d_day) == {0, -1, -2, -3}

    # D-0: 사이클 만기일 자체의 basis = [2, 12, 22, 32] 그대로.
    # spot이 항상 100.0(_series())이라 basis_pct = basis/100*100 == basis
    # (숫자가 우연히 같아 보이므로 별도 spot을 쓰는
    # test_basis_pct_matches_manual_calculation에서 실제 나눗셈을 검증한다).
    assert by_d_day[0]["n"] == 4
    assert by_d_day[0]["mean_basis"] == 17.0
    assert by_d_day[0]["median_basis"] == 17.0
    assert by_d_day[0]["mean_basis_pct"] == 17.0
    assert by_d_day[0]["median_basis_pct"] == 17.0

    # D-1: [1, 11, 21, 31].
    assert by_d_day[-1]["n"] == 4
    assert by_d_day[-1]["mean_basis"] == 16.0
    assert by_d_day[-1]["median_basis"] == 16.0
    assert by_d_day[-1]["mean_basis_pct"] == 16.0
    assert by_d_day[-1]["median_basis_pct"] == 16.0

    # D-2: [0, 10, 20, 30].
    assert by_d_day[-2]["n"] == 4
    assert by_d_day[-2]["mean_basis"] == 15.0
    assert by_d_day[-2]["median_basis"] == 15.0
    assert by_d_day[-2]["mean_basis_pct"] == 15.0
    assert by_d_day[-2]["median_basis_pct"] == 15.0

    # D-3: 첫 사이클(index 2)은 index -1이라 데이터가 없어 제외 -> [9, 19, 29]만.
    assert by_d_day[-3]["n"] == 3
    assert by_d_day[-3]["mean_basis"] == 19.0
    assert by_d_day[-3]["median_basis"] == 19.0
    assert by_d_day[-3]["mean_basis_pct"] == 19.0
    assert by_d_day[-3]["median_basis_pct"] == 19.0

    # points는 d_day 오름차순(가장 먼 과거 -> 만기 당일).
    assert [p["d_day"] for p in result["points"]] == [-3, -2, -1, 0]


def test_points_sorted_ascending_by_d_day():
    futures, spot = _series()
    result = aggregate_expiry_pattern(futures, spot, _EXPIRIES, max_lookback_days=3, min_cycles=3)
    d_days = [p["d_day"] for p in result["points"]]
    assert d_days == sorted(d_days)


def test_future_expiry_beyond_last_data_is_excluded():
    # 마지막 거래일 이후의 만기 후보(아직 안 지난 사이클)는 자동으로 빠진다.
    futures, spot = _series()
    future_expiry = _DATES[-1] + dt.timedelta(days=30)
    expiries = _EXPIRIES + [future_expiry]
    result = aggregate_expiry_pattern(futures, spot, expiries, max_lookback_days=3, min_cycles=3)
    assert result["cycle_count"] == 4  # future_expiry는 세지 않는다.
    assert result["date_to"] == _DATES[32].isoformat()


def test_expiry_not_a_trading_day_uses_nearest_prior():
    # index 15가 거래일 데이터에서 빠져 있으면(공휴일 등), 그 날짜를 만기로 넣어도
    # 가장 가까운 이전 거래일(index 14)을 D-0으로 써야 한다.
    futures, spot = _series()
    missing_date = _DATES[15]
    del futures[missing_date]
    del spot[missing_date]

    result = aggregate_expiry_pattern(
        futures, spot, [missing_date], max_lookback_days=0, min_cycles=1
    )
    assert result["reason"] is None
    assert result["cycle_count"] == 1
    # D-0 = index 14의 basis = 14.0 (index 15가 아니라). spot이 100.0 고정이라
    # basis_pct도 숫자상 14.0(= 14.0/100*100)으로 같다.
    assert result["points"] == [
        {
            "d_day": 0,
            "mean_basis": 14.0,
            "median_basis": 14.0,
            "mean_basis_pct": 14.0,
            "median_basis_pct": 14.0,
            "n": 1,
        }
    ]


def test_expiry_gap_too_large_is_skipped():
    # 거래일 데이터에 큰 공백(수집 중단 등)이 있어 만기 후보 이하 가장 가까운
    # 거래일이 5일(달력일)보다 더 멀면 그 사이클은 아예 건너뛴다.
    cluster_a = [_BASE + dt.timedelta(days=i) for i in range(5)]  # day0~4
    cluster_b = [_BASE + dt.timedelta(days=i) for i in range(20, 31)]  # day20~30
    dates = cluster_a + cluster_b
    spot = {d: 100.0 for d in dates}
    futures = {d: 100.0 + i for i, d in enumerate(dates)}

    gap_expiry = _BASE + dt.timedelta(days=10)  # 가장 가까운 이전 거래일은 day4 -> 간극 6일.
    result = aggregate_expiry_pattern(futures, spot, [gap_expiry], max_lookback_days=0, min_cycles=1)
    assert result["reason"] is not None
    assert result["cycle_count"] == 0


def test_basis_pct_matches_manual_calculation():
    # _series()는 spot=100.0 고정이라 basis_pct가 숫자상 basis와 같아 보여
    # 나눗셈 로직 자체를 못 가린다(위 테스트들 주석 참고). 여기서는 spot을
    # 200.0으로 고정해 실제 나눗셈이 들어가는지 손으로 검증한다.
    dates = [_BASE + dt.timedelta(days=i) for i in range(5)]  # day0~4
    spot = {d: 200.0 for d in dates}
    futures = {d: 200.0 + i for i, d in enumerate(dates)}  # basis = i
    expiry = dates[2]  # D-0=day2(basis=2.0), D-1=day1(1.0), D-2=day0(0.0)

    result = aggregate_expiry_pattern(futures, spot, [expiry], max_lookback_days=2, min_cycles=1)
    assert result["reason"] is None
    by_d_day = {p["d_day"]: p for p in result["points"]}

    # basis_pct = basis / 200.0 * 100 = basis / 2.
    assert by_d_day[0]["mean_basis"] == 2.0
    assert by_d_day[0]["mean_basis_pct"] == 1.0
    assert by_d_day[0]["median_basis_pct"] == 1.0
    assert by_d_day[-1]["mean_basis"] == 1.0
    assert by_d_day[-1]["mean_basis_pct"] == 0.5
    assert by_d_day[-2]["mean_basis"] == 0.0
    assert by_d_day[-2]["mean_basis_pct"] == 0.0


def test_insufficient_cycles_returns_honest_reason():
    futures, spot = _series()
    result = aggregate_expiry_pattern(futures, spot, _EXPIRIES, max_lookback_days=3, min_cycles=5)
    assert result["reason"] is not None
    assert "5" in result["reason"]
    assert result["cycle_count"] == 4
    assert result["points"] == []
    assert result["date_from"] is None
    assert result["date_to"] is None


def test_empty_input_is_graceful():
    result = aggregate_expiry_pattern({}, {}, [])
    assert result["reason"] is not None
    assert result["cycle_count"] == 0
    assert result["points"] == []

    # 가격 데이터는 있지만 만기 후보가 아예 없는 경우도 동일하게 정직한 실패.
    futures, spot = _series()
    result2 = aggregate_expiry_pattern(futures, spot, [])
    assert result2["reason"] is not None
    assert result2["cycle_count"] == 0
