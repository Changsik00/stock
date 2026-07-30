"""Unit tests for app.collectors.investor_warning.collect (PLAN.md §5.39-2).

No real network/DB — clients.kind_investor_warning.fetch_designations is
monkeypatched (same style as tests/test_short_selling_market_collector.py)
and the DB session is a FakeSession: SELECT statements (name -> code
resolution, caution_as_of) return canned results, INSERT (pg_insert) statements
are captured for inspection.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.sql import Select

from app.collectors import investor_warning
from app.clients import kind_investor_warning as kiw

DATE = dt.date(2026, 7, 30)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeSession:
    """SELECT(Stock.name, Stock.code) -> canned name_to_code mapping. INSERT
    (pg_insert) statements are recorded, not actually executed against a DB."""

    def __init__(self, name_to_code: dict[str, str]):
        self.name_to_code = name_to_code
        self.executed = []

    async def execute(self, stmt):
        if isinstance(stmt, Select):
            return _FakeResult(list(self.name_to_code.items()))
        self.executed.append(stmt)
        return None


def _row(tier: str, name: str, designated: dt.date, released: dt.date | None = None) -> dict:
    return {
        "tier": tier,
        "market": "KOSPI",
        "raw_name": name,
        "warning_type": "종가급변" if tier == kiw.TIER_CAUTION else None,
        "notice_date": designated - dt.timedelta(days=1),
        "designated_date": designated,
        "released_date": released,
    }


async def test_collect_upserts_rows_for_all_three_tiers(monkeypatch):
    responses = {
        kiw.TIER_CAUTION: [_row(kiw.TIER_CAUTION, "가온전선", dt.date(2026, 7, 30))],
        kiw.TIER_WARNING: [_row(kiw.TIER_WARNING, "비비안", dt.date(2026, 7, 20))],
        kiw.TIER_RISK: [],
    }

    def fake_fetch(tier, start, end, timeout=15):
        return responses[tier]

    monkeypatch.setattr(investor_warning.kiw, "fetch_designations", fake_fetch)

    session = FakeSession({"가온전선": "000500", "비비안": "002070"})
    rows_written = await investor_warning.collect(session, DATE)

    assert rows_written == 2
    params = [stmt.compile().params for stmt in session.executed]
    by_name = {p["raw_name"]: p for p in params}

    assert by_name["가온전선"]["code"] == "000500"
    assert by_name["가온전선"]["tier"] == kiw.TIER_CAUTION
    assert by_name["가온전선"]["warning_type"] == "종가급변"

    assert by_name["비비안"]["code"] == "002070"
    assert by_name["비비안"]["released_date"] is None  # 아직 지정 유지 중


async def test_collect_leaves_code_null_when_name_not_found_in_stocks(monkeypatch):
    monkeypatch.setattr(
        investor_warning.kiw,
        "fetch_designations",
        lambda tier, start, end, timeout=15: (
            [_row(kiw.TIER_WARNING, "미상장기업", dt.date(2026, 7, 30))]
            if tier == kiw.TIER_WARNING
            else []
        ),
    )

    session = FakeSession({})  # stocks 마스터에 해당 이름 없음
    rows_written = await investor_warning.collect(session, DATE)

    assert rows_written == 1
    params = session.executed[0].compile().params
    assert params["code"] is None
    assert params["raw_name"] == "미상장기업"  # 매칭 실패해도 raw_name은 보존


async def test_collect_skips_tier_that_raises_without_failing_others(monkeypatch):
    def fake_fetch(tier, start, end, timeout=15):
        if tier == kiw.TIER_WARNING:
            raise RuntimeError("kind unavailable")
        if tier == kiw.TIER_CAUTION:
            return [_row(kiw.TIER_CAUTION, "가온전선", dt.date(2026, 7, 30))]
        return []

    monkeypatch.setattr(investor_warning.kiw, "fetch_designations", fake_fetch)

    session = FakeSession({"가온전선": "000500"})
    rows_written = await investor_warning.collect(session, DATE)

    assert rows_written == 1
    assert len(session.executed) == 1


async def test_collect_uses_different_lookback_per_tier(monkeypatch):
    windows = {}

    def fake_fetch(tier, start, end, timeout=15):
        windows[tier] = (end - start).days
        return []

    monkeypatch.setattr(investor_warning.kiw, "fetch_designations", fake_fetch)

    session = FakeSession({})
    await investor_warning.collect(session, DATE)

    assert windows[kiw.TIER_CAUTION] == investor_warning._LOOKBACK_DAYS_CAUTION
    assert windows[kiw.TIER_WARNING] == investor_warning._LOOKBACK_DAYS_WARNING_RISK
    assert windows[kiw.TIER_RISK] == investor_warning._LOOKBACK_DAYS_WARNING_RISK


def test_collect_registered_in_registry():
    from app.collectors.base import REGISTRY

    assert REGISTRY["investor_warning"] is investor_warning.collect
