"""Unit tests for app.quant.positioning_backtest (PLAN.md §5.52, §5.50 포지셔닝
프레임 사후 검증 그룹별 버킷 통계).

Pure-function module — no DB/network involved, so these tests just feed
hand-built row lists and assert the grouped stats (same style as
tests/test_regime_backtest.py's hand-computed fixture, but here the whole
module is pure so no DB fixture is needed at all).
"""

from __future__ import annotations

import pytest

from app.quant import positioning_backtest as pb


def _row(
    regime=None,
    relative_strength_pct=None,
    foreign_spot_cum=None,
    foreign_futures_cum=None,
    nasdaq_futures_change_pct=None,
    next_day_change_rate=0.0,
):
    return {
        "regime": regime,
        "relative_strength_pct": relative_strength_pct,
        "foreign_spot_cum": foreign_spot_cum,
        "foreign_futures_cum": foreign_futures_cum,
        "nasdaq_futures_change_pct": nasdaq_futures_change_pct,
        "next_day_change_rate": next_day_change_rate,
    }


def test_min_samples_constant_is_20():
    # 다른 하우스 상수(MIN_BARS_FOR_LEVELS, MIN_BASELINE_DAYS)류와 동일한 "값을
    # 숨기는 문턱"을 20으로 못박은 설계(§5.52) — 실수로 바뀌면 이 테스트가 잡는다.
    assert pb.MIN_SAMPLES == 20


def test_bucket_below_min_samples_hides_avg_but_keeps_n():
    values = [1.0, -2.0, 3.0]  # n=3 < 20
    stats = pb._bucket_stats(values)
    assert stats == {"n": 3, "avg_next_day_change_rate": None, "positive_rate_pct": None}


def test_bucket_at_min_samples_reveals_avg_and_positive_rate():
    # n=20, 절반은 +1.0 절반은 -1.0 -> 평균 0.0, 상승확률 50%(양수만 카운트,
    # 0/음수는 하락 취급 — regime_backtest.compute_baseline과 동일한 정의).
    values = [1.0] * 10 + [-1.0] * 10
    stats = pb._bucket_stats(values)
    assert stats["n"] == 20
    assert stats["avg_next_day_change_rate"] == pytest.approx(0.0)
    assert stats["positive_rate_pct"] == pytest.approx(50.0)


def test_sign_label_positive_negative_and_zero_excluded():
    assert pb._sign_label(1.5) == "positive"
    assert pb._sign_label(-0.001) == "negative"
    assert pb._sign_label(0.0) is None
    assert pb._sign_label(None) is None


def test_group_by_skips_rows_with_none_key():
    rows = [_row(regime="코스피우세", next_day_change_rate=1.0), _row(regime=None, next_day_change_rate=99.0)]
    grouped = pb._group_by(rows, lambda r: r.get("regime"))
    assert set(grouped) == {"코스피우세"}
    assert grouped["코스피우세"]["n"] == 1


def test_compute_positioning_hitrate_groups_and_hides_small_samples():
    rows = []
    # "코스피우세" 25건, 전부 +1.0% -> n>=20이라 평균/상승확률 노출.
    rows += [_row(regime="코스피우세", relative_strength_pct=0.5, next_day_change_rate=1.0) for _ in range(25)]
    # "코스닥우세" 5건뿐 -> n<20이라 숨김.
    rows += [_row(regime="코스닥우세", relative_strength_pct=-0.5, next_day_change_rate=-1.0) for _ in range(5)]

    result = pb.compute_positioning_hitrate(rows)

    assert result["min_samples"] == 20
    assert result["by_regime"]["코스피우세"] == {
        "n": 25,
        "avg_next_day_change_rate": 1.0,
        "positive_rate_pct": 100.0,
    }
    assert result["by_regime"]["코스닥우세"] == {
        "n": 5,
        "avg_next_day_change_rate": None,
        "positive_rate_pct": None,
    }
    # relative_strength_pct 부호별 그룹도 같은 25/5 분포로 갈라져야 한다.
    assert result["by_relative_strength_sign"]["positive"]["n"] == 25
    assert result["by_relative_strength_sign"]["negative"]["n"] == 5
    # 한 번도 등장하지 않은 그룹(예: foreign_spot_cum 전부 None)은 키 자체가 없다.
    assert "by_foreign_spot_sign" in result
    assert result["by_foreign_spot_sign"] == {}


def test_compute_positioning_hitrate_empty_rows_returns_empty_groups():
    result = pb.compute_positioning_hitrate([])
    assert result["by_regime"] == {}
    assert result["by_relative_strength_sign"] == {}
    assert result["by_foreign_spot_sign"] == {}
    assert result["by_foreign_futures_sign"] == {}
    assert result["by_nasdaq_futures_sign"] == {}
    assert result["min_samples"] == 20
