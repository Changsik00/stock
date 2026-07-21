"""Unit tests for GET /api/markets/scalp-candidates (app.routers.scalp,
PLAN.md §5.2).

httpx.AsyncClient + ASGITransport against the real FastAPI app. No real
network/DB — this router doesn't fetch anything itself, it only calls the
already-tested warm functions from routers.flow_rank (_warm_value_rank_live)
and routers.markets (_warm_attention), so here we monkeypatch those two warm
functions directly (same "swap the collaborator" style as
test_markets_attention_router.py / test_value_rank_live_router.py, but one
level up since scalp.py composes both).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import get_session
from app.main import app
from app.routers import scalp

VALUE_RANK_PAYLOAD = {
    "date": "2026-07-21",
    "market_closed": False,
    "cached_at": "2026-07-21T01:00:00+00:00",
    "rows": [
        {
            "rank": 1,
            "market": "kospi",
            "code": "000660",
            "name": "SK하이닉스",
            "value": 500_000,
            "change_rate": 3.5,
            "is_etf": False,
            "turnover": 8.2,
        },
        {
            "rank": 2,
            "market": "kospi",
            "code": "069500",
            "name": "KODEX 200",
            "value": 400_000,
            "change_rate": 0.5,
            "is_etf": True,  # ETF -> 후보에서 제외돼야 함
            "turnover": 12.0,
        },
        {
            "rank": 3,
            "market": "kosdaq",
            "code": "247540",
            "name": "에코프로비엠",
            "value": 100_000,
            "change_rate": -6.1,
            "is_etf": False,
            "turnover": 15.4,
        },
    ],
}

ATTENTION_PAYLOAD = {
    "rows": [{"rank": 1, "code": "247540", "name": "에코프로비엠", "change_rate": -6.1, "is_etf": False, "market": "kosdaq"}],
    "qry_tp": "4",
    "queried_at": "2026-07-21T01:00:05+00:00",
    "market_closed": False,
}


async def _fake_warm_value_rank_live():
    return VALUE_RANK_PAYLOAD


async def _fake_warm_attention(session):
    return ATTENTION_PAYLOAD


@pytest.fixture(autouse=True)
def _patch_warm_functions(monkeypatch):
    monkeypatch.setattr(scalp, "_warm_value_rank_live", _fake_warm_value_rank_live)
    monkeypatch.setattr(scalp, "_warm_attention", _fake_warm_attention)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


async def _get_session_override():
    yield None


async def test_scalp_candidates_excludes_etf_and_marks_attention():
    app.dependency_overrides[get_session] = _get_session_override

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/scalp-candidates")

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-07-21"
    assert body["market_closed"] is False
    codes = [r["code"] for r in body["rows"]]
    assert "069500" not in codes  # ETF 제외
    assert set(codes) == {"000660", "247540"}

    by_code = {r["code"]: r for r in body["rows"]}
    assert by_code["247540"]["in_attention_top"] is True
    assert by_code["000660"]["in_attention_top"] is False
    assert by_code["247540"]["value_rank_position"] == 3
    assert by_code["000660"]["value_rank_position"] == 1
    assert by_code["247540"]["turnover"] == 15.4
    assert by_code["000660"]["change_rate"] == 3.5
    # score 내림차순 정렬 확인
    scores = [r["score"] for r in body["rows"]]
    assert scores == sorted(scores, reverse=True)


async def test_scalp_candidates_limit_param():
    app.dependency_overrides[get_session] = _get_session_override

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/scalp-candidates?limit=1")

    assert resp.status_code == 200
    assert len(resp.json()["rows"]) == 1


async def test_scalp_candidates_empty_rows_when_no_value_rank_data(monkeypatch):
    async def _empty_value_rank():
        return {"date": None, "market_closed": False, "cached_at": None, "rows": []}

    monkeypatch.setattr(scalp, "_warm_value_rank_live", _empty_value_rank)
    app.dependency_overrides[get_session] = _get_session_override

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/scalp-candidates")

    assert resp.status_code == 200
    assert resp.json()["rows"] == []


async def test_scalp_candidates_market_closed_reflected_from_value_rank(monkeypatch):
    async def _closed_value_rank():
        return {**VALUE_RANK_PAYLOAD, "market_closed": True}

    monkeypatch.setattr(scalp, "_warm_value_rank_live", _closed_value_rank)
    app.dependency_overrides[get_session] = _get_session_override

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/scalp-candidates")

    assert resp.status_code == 200
    assert resp.json()["market_closed"] is True
