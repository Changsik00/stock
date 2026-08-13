"""Unit tests for GET /api/groups/theme-picks (app.routers.groups, 사용자 요청
"2차전지/반도체/엔터/방산/바이오/AI 같은 관심 테마를 몇 개 고정으로 골라서 대장
종목 top10을 미니 트리맵 그리드로").

httpx.AsyncClient + ASGITransport against the real FastAPI app (groups.router is
already wired into main.py). No DB session needed and no real network — the
blocking naver_group calls are monkeypatched via groups._fetch_group_list_blocking
and groups._fetch_group_constituents_blocking (mirrors
test_groups_top_stocks_router.py).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.clients import naver_group
from app.main import app
from app.routers import groups

# CURATED_THEME_NAMES 8개 전부에 대해 no를 배정해둔 목록 페이지 응답.
NAME_TO_NO_ROWS = [
    {"name": name, "change_rate": 1.0, "no": idx}
    for idx, name in enumerate(groups.CURATED_THEME_NAMES, start=1)
]

CONSTITUENT_ROWS = [
    {"code": "413300", "name": "티엘엔지니어링", "change_rate": 10.19, "value": 8076},
    {"code": "365590", "name": "하이딥", "change_rate": 10.56, "value": 122},
]


@pytest.fixture(autouse=True)
def _reset_caches():
    groups._group_name_to_no_cache["upjong"] = {"ts": 0.0, "data": None}
    groups._group_name_to_no_cache["theme"] = {"ts": 0.0, "data": None}
    groups._group_top_stocks_cache.clear()
    yield
    groups._group_name_to_no_cache["upjong"] = {"ts": 0.0, "data": None}
    groups._group_name_to_no_cache["theme"] = {"ts": 0.0, "data": None}
    groups._group_top_stocks_cache.clear()


async def test_theme_picks_returns_all_curated_themes(monkeypatch):
    monkeypatch.setattr(groups, "_fetch_group_list_blocking", lambda group_type: NAME_TO_NO_ROWS)
    monkeypatch.setattr(
        groups, "_fetch_group_constituents_blocking", lambda group_type, no, limit: CONSTITUENT_ROWS
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/groups/theme-picks")

    assert resp.status_code == 200
    body = resp.json()
    assert "cached_at" in body
    assert [t["name"] for t in body["themes"]] == groups.CURATED_THEME_NAMES
    for theme in body["themes"]:
        assert theme["rows"] == CONSTITUENT_ROWS
        assert "error" not in theme
        assert "cached_at" in theme


async def test_theme_picks_uses_theme_group_type_and_calls_list_once(monkeypatch):
    calls = []

    def fake_list(group_type):
        calls.append(group_type)
        return NAME_TO_NO_ROWS

    monkeypatch.setattr(groups, "_fetch_group_list_blocking", fake_list)
    monkeypatch.setattr(
        groups, "_fetch_group_constituents_blocking", lambda group_type, no, limit: CONSTITUENT_ROWS
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/groups/theme-picks")

    assert resp.status_code == 200
    # 이름→no 매핑은 한 번만(테마 8개 순회 전체에서) 재조회돼야 한다.
    assert calls == ["theme"]


async def test_theme_picks_partial_failure_missing_name(monkeypatch):
    # 첫 번째 테마 이름을 목록에서 빼서 "매핑에 없음"(단일 조회의 404 상황)을
    # 재현한다 — 그 테마만 rows: []/error로 채워지고 나머지 7개는 정상 반환돼야
    # 한다.
    missing_name = groups.CURATED_THEME_NAMES[0]
    rows_without_first = [r for r in NAME_TO_NO_ROWS if r["name"] != missing_name]

    monkeypatch.setattr(groups, "_fetch_group_list_blocking", lambda group_type: rows_without_first)
    monkeypatch.setattr(
        groups, "_fetch_group_constituents_blocking", lambda group_type, no, limit: CONSTITUENT_ROWS
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/groups/theme-picks")

    assert resp.status_code == 200
    body = resp.json()
    themes_by_name = {t["name"]: t for t in body["themes"]}
    assert len(themes_by_name) == len(groups.CURATED_THEME_NAMES)

    failed = themes_by_name[missing_name]
    assert failed["rows"] == []
    assert "error" in failed

    for name in groups.CURATED_THEME_NAMES:
        if name == missing_name:
            continue
        assert themes_by_name[name]["rows"] == CONSTITUENT_ROWS
        assert "error" not in themes_by_name[name]


async def test_theme_picks_partial_failure_constituents_fetch_error(monkeypatch):
    # 두 번째 테마만 구성 종목 조회에서 NaverGroupError를 던지게 해(단일 조회의
    # 502 상황) 그 테마만 rows: []/error로 채워지고 나머지는 정상인지 확인한다.
    failing_name = groups.CURATED_THEME_NAMES[1]

    def fake_constituents(group_type, no, limit):
        if no == 2:  # NAME_TO_NO_ROWS의 두 번째 항목이 no=2
            raise naver_group.NaverGroupError("boom")
        return CONSTITUENT_ROWS

    monkeypatch.setattr(groups, "_fetch_group_list_blocking", lambda group_type: NAME_TO_NO_ROWS)
    monkeypatch.setattr(groups, "_fetch_group_constituents_blocking", fake_constituents)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/groups/theme-picks")

    assert resp.status_code == 200
    body = resp.json()
    themes_by_name = {t["name"]: t for t in body["themes"]}
    assert len(themes_by_name) == len(groups.CURATED_THEME_NAMES)

    failed = themes_by_name[failing_name]
    assert failed["rows"] == []
    assert "error" in failed

    for name in groups.CURATED_THEME_NAMES:
        if name == failing_name:
            continue
        assert themes_by_name[name]["rows"] == CONSTITUENT_ROWS


async def test_theme_picks_respects_limit_param(monkeypatch):
    calls = []

    def fake_constituents(group_type, no, limit):
        calls.append(limit)
        return CONSTITUENT_ROWS[:limit]

    monkeypatch.setattr(groups, "_fetch_group_list_blocking", lambda group_type: NAME_TO_NO_ROWS)
    monkeypatch.setattr(groups, "_fetch_group_constituents_blocking", fake_constituents)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/groups/theme-picks", params={"limit": 1})

    assert resp.status_code == 200
    assert calls == [1] * len(groups.CURATED_THEME_NAMES)
    for theme in resp.json()["themes"]:
        assert len(theme["rows"]) == 1


async def test_theme_picks_reuses_top_stocks_cache(monkeypatch):
    # top-stocks 엔드포인트와 캐시를 공유하므로, theme-picks가 먼저 채운 캐시를
    # top-stocks가 그대로 히트해야 한다(재구현 금지 확인).
    calls = []

    def fake_constituents(group_type, no, limit):
        calls.append((group_type, no, limit))
        return CONSTITUENT_ROWS

    monkeypatch.setattr(groups, "_fetch_group_list_blocking", lambda group_type: NAME_TO_NO_ROWS)
    monkeypatch.setattr(groups, "_fetch_group_constituents_blocking", fake_constituents)

    first_theme_name = groups.CURATED_THEME_NAMES[0]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        picks_resp = await client.get("/api/groups/theme-picks")
        assert picks_resp.status_code == 200
        calls_after_picks = len(calls)

        top_resp = await client.get(
            "/api/groups/top-stocks", params={"type": "theme", "name": first_theme_name}
        )

    assert top_resp.status_code == 200
    assert top_resp.json()["rows"] == CONSTITUENT_ROWS
    # top-stocks 호출이 캐시를 히트해 constituents fetch가 추가로 일어나지 않아야
    # 한다.
    assert len(calls) == calls_after_picks
