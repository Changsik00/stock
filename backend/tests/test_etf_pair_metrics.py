"""Unit tests for app.quant.etf_pair_metrics (PLAN.md §5.88-1) — 순수 함수,
2026-10-28 실측 예시값으로 검증한다."""

from __future__ import annotations

import math

import pytest

from app.quant.etf_pair_metrics import (
    PAIR_ETFS,
    UNDERLYING_CODE,
    compute_lag_pct,
    compute_premium_pct,
    compute_theory_price,
    summarize_distribution,
)


def test_constants():
    assert UNDERLYING_CODE == "150"
    assert PAIR_ETFS == {"233740": 2, "251340": -1}


def test_theory_and_lag_leverage_real_example():
    theory = compute_theory_price(5095, 2, 3.63)
    assert theory == pytest.approx(5464.9, abs=0.1)
    assert compute_lag_pct(5460, theory) == pytest.approx(-0.09, abs=0.01)


def test_theory_and_lag_inverse_real_example():
    theory = compute_theory_price(2690, -1, 3.63)
    assert theory == pytest.approx(2592.4, abs=0.1)
    assert compute_lag_pct(2595, theory) == pytest.approx(0.10, abs=0.01)


def test_premium_pct():
    assert compute_premium_pct(101, 100) == pytest.approx(1.0)
    assert compute_premium_pct(99, 100) == pytest.approx(-1.0)


@pytest.mark.parametrize("nav", [None, 0, -5, float("nan")])
def test_premium_none_for_bad_nav(nav):
    assert compute_premium_pct(100, nav) is None


def test_premium_none_for_bad_price():
    assert compute_premium_pct(None, 100) is None
    assert compute_premium_pct("abc", 100) is None


@pytest.mark.parametrize("theory", [None, 0, -1.0])
def test_lag_none_for_bad_theory(theory):
    assert compute_lag_pct(100, theory) is None


def test_theory_none_for_bad_inputs():
    assert compute_theory_price(None, 2, 1.0) is None
    assert compute_theory_price(100, None, 1.0) is None
    assert compute_theory_price(100, 2, None) is None
    assert compute_theory_price(0, 2, 1.0) is None
    assert compute_theory_price(-100, 2, 1.0) is None
    assert compute_theory_price(100, 2, math.inf) is None


def test_summarize_distribution():
    s = summarize_distribution([None, 1.0, 2.0, 3.0, 4.0, 5.0])
    assert s["mean"] == 3.0
    assert s["min"] == 1.0 and s["max"] == 5.0
    assert s["p5"] == pytest.approx(1.2)
    assert s["p95"] == pytest.approx(4.8)


def test_summarize_distribution_empty_and_single():
    assert summarize_distribution([]) is None
    assert summarize_distribution([None]) is None
    one = summarize_distribution([2.5])
    assert one == {"mean": 2.5, "p5": 2.5, "p95": 2.5, "min": 2.5, "max": 2.5}
