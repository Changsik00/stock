"""Unit tests for app.collectors.group_snapshot.collect_group_snapshot (PLAN.md §4.6/§6 3.6-3).

No real network/DB involved — clients.naver_group and the DB upsert helper are
monkeypatched (same pattern as tests/test_flow_rank_collector.py).
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.collectors import group_snapshot

TARGET_DATE = dt.date(2026, 7, 18)

UPJONG_ROWS = [
    {"name": "문구류", "change_rate": 8.27},
    {"name": "반도체와반도체장비", "change_rate": -10.07},
]
THEME_ROWS = [
    {"name": "2차전지", "change_rate": -2.5},
]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(group_snapshot.time, "sleep", lambda _seconds: None)


def _patch_common(monkeypatch, upserted=None, fetch_calls=None):
    def fake_fetch(group_type):
        if fetch_calls is not None:
            fetch_calls.append(group_type)
        return UPJONG_ROWS if group_type == "upjong" else THEME_ROWS

    async def fake_upsert(session, date, group_type, rows):
        if upserted is not None:
            upserted.append((date, group_type, list(rows)))
        return len(rows)

    monkeypatch.setattr(group_snapshot, "_fetch_group_blocking", fake_fetch)
    monkeypatch.setattr(group_snapshot, "_upsert_rows", fake_upsert)


async def test_collect_group_snapshot_fetches_both_group_types(monkeypatch):
    fetch_calls: list[str] = []
    _patch_common(monkeypatch, fetch_calls=fetch_calls)

    total, message = await group_snapshot.collect_group_snapshot(session=None, target_date=TARGET_DATE)

    assert sorted(fetch_calls) == ["theme", "upjong"]
    assert total == 3  # 2 upjong rows + 1 theme row
    assert "업종 2개" in message
    assert "테마 1개" in message


async def test_collect_group_snapshot_upserts_rows_with_target_date(monkeypatch):
    upserted: list[tuple] = []
    _patch_common(monkeypatch, upserted=upserted)

    await group_snapshot.collect_group_snapshot(session=None, target_date=TARGET_DATE)

    assert len(upserted) == 2
    upjong_call = next(u for u in upserted if u[1] == "upjong")
    assert upjong_call[0] == TARGET_DATE
    assert upjong_call[2] == UPJONG_ROWS

    theme_call = next(u for u in upserted if u[1] == "theme")
    assert theme_call[2] == THEME_ROWS


async def test_collect_group_snapshot_message_notes_no_value_market_sum(monkeypatch):
    _patch_common(monkeypatch)

    _total, message = await group_snapshot.collect_group_snapshot(session=None, target_date=TARGET_DATE)

    assert "NULL" in message
