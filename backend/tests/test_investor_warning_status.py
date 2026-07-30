"""Unit tests for app.quant.investor_warning_status.classify_investor_warning_status
(PLAN.md §5.39). Pure function, no DB — see module docstring for the tier
priority (risk > warning > caution) and the caution "as-of" semantics that
differ from warning/risk's released_date-based semantics.
"""

from __future__ import annotations

import datetime as dt

from app.quant.investor_warning_status import classify_investor_warning_status


def _row(tier, designated, released=None, warning_type=None, market="KOSPI"):
    return {
        "tier": tier,
        "market": market,
        "warning_type": warning_type,
        "notice_date": designated - dt.timedelta(days=1),
        "designated_date": designated,
        "released_date": released,
    }


def test_no_rows_is_not_evaluable():
    result = classify_investor_warning_status([])
    assert result["evaluable"] is False
    assert result["active_tier"] is None


def test_warning_active_when_released_date_is_none():
    rows = [_row("warning", dt.date(2026, 7, 20), released=None)]
    result = classify_investor_warning_status(rows)
    assert result["evaluable"] is True
    assert result["active_tier"] == "warning"
    assert result["label"] == "투자경고종목"
    assert result["designated_date"] == dt.date(2026, 7, 20)


def test_warning_inactive_when_already_released():
    rows = [_row("warning", dt.date(2026, 4, 28), released=dt.date(2026, 5, 11))]
    result = classify_investor_warning_status(rows)
    assert result["evaluable"] is True
    assert result["active_tier"] is None


def test_risk_outranks_warning_when_both_active():
    rows = [
        _row("warning", dt.date(2026, 6, 1), released=None),
        _row("risk", dt.date(2026, 7, 1), released=None),
    ]
    result = classify_investor_warning_status(rows)
    assert result["active_tier"] == "risk"
    assert result["label"] == "투자위험종목"


def test_most_recent_designated_date_wins_when_multiple_active_rows_same_tier():
    # 실무적으로 드물지만(재지정 등) designated_date가 다른 두 활성 행이 있으면
    # 더 최근 지정을 대표값으로 쓴다.
    rows = [
        _row("warning", dt.date(2026, 1, 1), released=None),
        _row("warning", dt.date(2026, 7, 1), released=None),
    ]
    result = classify_investor_warning_status(rows)
    assert result["designated_date"] == dt.date(2026, 7, 1)


def test_caution_active_only_when_designated_date_matches_as_of():
    rows = [_row("caution", dt.date(2026, 7, 30), warning_type="종가급변")]

    active = classify_investor_warning_status(rows, caution_as_of=dt.date(2026, 7, 30))
    assert active["active_tier"] == "caution"
    assert active["label"] == "투자주의종목"
    assert active["warning_type"] == "종가급변"

    # 같은 종목이라도 그 caution 지정이 "가장 최근 caution 데이터 날짜"보다
    # 과거면(=오늘자 목록엔 없다는 뜻) 더 이상 활성으로 보지 않는다.
    stale = classify_investor_warning_status(rows, caution_as_of=dt.date(2026, 7, 31))
    assert stale["active_tier"] is None


def test_caution_not_evaluated_without_as_of_reference():
    rows = [_row("caution", dt.date(2026, 7, 30))]
    result = classify_investor_warning_status(rows, caution_as_of=None)
    assert result["active_tier"] is None
    assert result["evaluable"] is True  # rows 자체는 있었음 — 판정 기준만 없었을 뿐


def test_caution_row_does_not_leak_warning_type_into_other_tiers():
    rows = [_row("warning", dt.date(2026, 7, 20), released=None)]
    result = classify_investor_warning_status(rows, caution_as_of=dt.date(2026, 7, 30))
    assert result["active_tier"] == "warning"
    assert result["warning_type"] is None
