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
    {"name": "문구류", "change_rate": 8.27, "no": 332},
    {"name": "반도체와반도체장비", "change_rate": -10.07, "no": 278},
]
THEME_ROWS = [
    {"name": "2차전지", "change_rate": -2.5, "no": 30},
]

# fetch_group_value가 그룹 no별로 돌려줄 값(백만원) — no=278은 실패를 흉내내
# _fetch_values_blocking의 개별 실패 흡수/카운트를 검증한다.
FAKE_VALUES = {332: 1000, 30: 5000}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(group_snapshot.time, "sleep", lambda _seconds: None)


def _patch_common(monkeypatch, upserted=None, fetch_calls=None, value_calls=None):
    def fake_fetch(group_type):
        if fetch_calls is not None:
            fetch_calls.append(group_type)
        # 몽키패치되는 쪽은 얕은 복사본을 돌려줘야 컬렉터가 채워 넣는 "value" 키가
        # 모듈 상수 UPJONG_ROWS/THEME_ROWS 자체를 오염시키지 않는다(테스트 간 격리).
        rows = UPJONG_ROWS if group_type == "upjong" else THEME_ROWS
        return [dict(r) for r in rows]

    def fake_fetch_value(group_type, no):
        if value_calls is not None:
            value_calls.append((group_type, no))
        if no == 278:
            raise group_snapshot.naver_group.NaverGroupError("boom")
        return FAKE_VALUES[no]

    async def fake_upsert(session, date, group_type, rows):
        if upserted is not None:
            upserted.append((date, group_type, list(rows)))
        return len(rows)

    monkeypatch.setattr(group_snapshot, "_fetch_group_blocking", fake_fetch)
    monkeypatch.setattr(group_snapshot.naver_group, "fetch_group_value", fake_fetch_value)
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
    names_to_value = {r["name"]: r["value"] for r in upjong_call[2]}
    assert names_to_value == {"문구류": 1000, "반도체와반도체장비": None}  # no=278 fails

    theme_call = next(u for u in upserted if u[1] == "theme")
    assert theme_call[2][0]["value"] == 5000


async def test_collect_group_snapshot_fetches_value_per_group(monkeypatch):
    value_calls: list[tuple] = []
    _patch_common(monkeypatch, value_calls=value_calls)

    await group_snapshot.collect_group_snapshot(session=None, target_date=TARGET_DATE)

    assert sorted(value_calls) == [("theme", 30), ("upjong", 278), ("upjong", 332)]


async def test_collect_group_snapshot_message_notes_failures_and_market_sum_null(monkeypatch):
    _patch_common(monkeypatch)

    _total, message = await group_snapshot.collect_group_snapshot(session=None, target_date=TARGET_DATE)

    assert "실패 1건" in message
    assert "NULL" in message
