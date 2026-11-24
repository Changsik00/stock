"""quant/nq_comovement.py 순수 함수 테스트 (PLAN.md Phase 5.116)."""

from __future__ import annotations

import pytest

from app.quant import nq_comovement as nc


def test_hhmm_to_min():
    assert nc.hhmm_to_min("0900") == 540
    assert nc.hhmm_to_min("1530") == 930
    assert nc.hhmm_to_min("9") is None
    assert nc.hhmm_to_min("2560") is None


def test_resample_kospi_last_close_per_bucket_and_session_bounds():
    bars = [(539, 99.0), (540, 100.0), (544, 101.0), (545, 102.0), (929, 103.0), (930, 104.0), (933, 105.0), (936, 106.0)]
    out = nc.resample_kospi(bars)
    assert out == {540: 101.0, 545: 102.0, 925: 105.0}  # 09:00 이전·15:36 이후 제외, 15:30~15:35 종가 봉은 마지막 버킷에 합침


def test_pair_returns_uses_anchor_for_open_and_requires_prev_bucket():
    k = {540: 101.0, 545: 102.01, 555: 105.0}
    n = {540: 200.0, 545: 202.0, 555: 210.0}
    pairs = nc.pair_returns(k, n, anchor_k=100.0, anchor_nq=200.0)
    # 09:10 버킷이 없어 09:15는 직전 값이 없으므로 제외
    assert [p[0] for p in pairs] == [540, 545]
    assert pairs[0][1] == pytest.approx(1.0) and pairs[0][2] == pytest.approx(0.0)
    # 기준점이 없으면 09:00 쌍은 건너뛴다
    assert [p[0] for p in nc.pair_returns(k, n)] == [545]


def _series(beta, noise, n=40):
    nq_rets = [((i * 7) % 11 - 5) * 0.02 for i in range(n)]
    k_rets = [beta * r + noise * (((i * 5) % 7) - 3) * 0.01 for i, r in enumerate(nq_rets)]
    return [(540 + 5 * i, kr, nr) for i, (kr, nr) in enumerate(zip(k_rets, nq_rets))]


def test_regression_perfect_and_noisy():
    reg = nc.regression(_series(0.8, 0.0))
    assert reg["corr"] == pytest.approx(1.0)
    assert reg["beta"] == pytest.approx(0.8)
    assert reg["r2"] == pytest.approx(1.0)
    noisy = nc.regression(_series(0.8, 3.0))
    assert 0 < noisy["corr"] < 1
    assert noisy["r2"] == pytest.approx(noisy["corr"] ** 2)


def test_regression_none_when_too_few_or_flat():
    assert nc.regression(_series(1.0, 0.0, n=nc.MIN_PAIRS - 1)) is None
    assert nc.regression(_series(1.0, 0.0, n=nc.MIN_PAIRS)) is not None
    flat = [(540 + 5 * i, 0.1 * i, 0.0) for i in range(20)]
    assert nc.regression(flat) is None


def test_decompose_sums_to_total():
    d = nc.decompose(-1.5, -2.0, 0.6)
    assert d["explained_pct"] == pytest.approx(-1.2)
    assert d["unique_pct"] == pytest.approx(-0.3)
    assert d["explained_pct"] + d["unique_pct"] == pytest.approx(d["kospi_change_pct"])


def test_overnight_gap():
    g = nc.overnight_gap(4700.0, 4747.0, 25000.0, 25500.0)
    assert g["nq_pct"] == pytest.approx(2.0)
    assert g["kospi_gap_pct"] == pytest.approx(1.0)
    assert nc.overnight_gap(None, 4747.0, 25000.0, 25500.0) is None


def test_percentile_quantile_and_history():
    sample = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
    assert nc.percentile_of(0.3, sample) == pytest.approx(50.0)
    assert nc.percentile_of(0.3, []) is None
    assert nc.quantile(sample, 0.5) == pytest.approx(0.35)
    h = nc.history_summary(sample)
    assert h["days"] == 6 and h["lower"] < h["median"] < h["upper"]
    assert nc.history_summary([])["median"] is None


def test_label_thresholds_come_from_distribution():
    hist = [i / 20 for i in range(20)]  # 0.00..0.95
    lower, upper = nc.quantile(hist, 1 / 3), nc.quantile(hist, 2 / 3)
    assert nc.label_corr(upper + 0.01, hist) == nc.LABEL_STRONG
    assert nc.label_corr(lower - 0.01, hist) == nc.LABEL_WEAK
    assert nc.label_corr((lower + upper) / 2, hist) == nc.LABEL_MID
    # 분포를 두 배로 늘리면 같은 값의 라벨이 달라진다 — 하드코딩 임계값이 없다
    assert nc.label_corr(0.5, [x * 2 for x in hist]) == nc.LABEL_WEAK
    assert nc.label_corr(0.5, hist[: nc.MIN_HISTORY_DAYS - 1]) is None
    assert nc.label_corr(None, hist) is None
