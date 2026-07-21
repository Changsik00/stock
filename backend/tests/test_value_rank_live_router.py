"""Unit tests for GET /api/markets/value-rank/live (app.routers.flow_rank,
PLAN.md §4.7 3단 갱신 주기 — 2026-07-20 장중 실측으로 5~10분 티어 편입).

httpx.AsyncClient + ASGITransport against the real FastAPI app (flow_rank.router
is already wired into main.py). No DB session needed and no real network — the
blocking naver_value_rank/naver_rank calls are monkeypatched via
flow_rank._fetch_value_rank_market_blocking / _fetch_etf_codes_blocking.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.routers import flow_rank

KOSPI_RESULT = {
    "date": dt.date(2026, 7, 20),
    "rows": [
        {
            "code": "000660",
            "name": "SK하이닉스",
            "value_million": 2_825_522,
            "market_value_million": 500_000_000,
            "change_rate": 1.09,
            "stock_end_type": "stock",
        }
    ],
}
KOSDAQ_RESULT = {
    "date": dt.date(2026, 7, 20),
    "rows": [
        {
            "code": "247540",
            "name": "에코프로비엠",
            "value_million": 500_000,
            "market_value_million": 10_000_000,
            "change_rate": -0.5,
            "stock_end_type": "stock",
        }
    ],
}


@pytest.fixture(autouse=True)
def _reset_cache():
    flow_rank._value_rank_live_cache["data"] = None
    flow_rank._value_rank_live_cache["ts"] = 0.0
    yield
    flow_rank._value_rank_live_cache["data"] = None
    flow_rank._value_rank_live_cache["ts"] = 0.0


@pytest.fixture(autouse=True)
def _force_market_open(monkeypatch):
    monkeypatch.setattr(flow_rank, "is_nxt_closed", lambda now_kst: False)
    monkeypatch.setattr(flow_rank, "_fetch_etf_codes_blocking", lambda: set())


async def test_value_rank_live_merges_markets_and_sorts_by_value(monkeypatch):
    def fake_fetch(market):
        return KOSPI_RESULT if market == "kospi" else KOSDAQ_RESULT

    monkeypatch.setattr(flow_rank, "_fetch_value_rank_market_blocking", fake_fetch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/value-rank/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-07-20"
    assert body["market_closed"] is False
    assert [r["code"] for r in body["rows"]] == ["000660", "247540"]
    assert body["rows"][0]["rank"] == 1
    assert body["rows"][0]["turnover"] == round(2_825_522 / 500_000_000 * 100, 4)
    assert "cached_at" in body


async def test_value_rank_live_caches_within_ttl(monkeypatch):
    calls = []

    def fake_fetch(market):
        calls.append(market)
        return KOSPI_RESULT if market == "kospi" else KOSDAQ_RESULT

    monkeypatch.setattr(flow_rank, "_fetch_value_rank_market_blocking", fake_fetch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/api/markets/value-rank/live")
        r2 = await client.get("/api/markets/value-rank/live")

    assert r1.json()["cached_at"] == r2.json()["cached_at"]
    assert calls == ["kospi", "kosdaq"]


async def test_value_rank_live_empty_source_no_cache_returns_graceful_empty(monkeypatch):
    """2026-07-22 수정 — 이전엔 소스가 빈 응답이면(양쪽 시장 다 실패) 502였다.
    사용자 실측 지적: NXT 프리마켓(08:00~08:50)엔 is_nxt_closed가 "장중"으로
    판정하는데 이 소스(네이버 거래대금 순위)는 정규장(09:00) 전엔 항상
    totalCount=0이라, 매일 아침 이 카드가 502로 깨져 보였다. 캐시가 없으면
    (기동 직후) 빈 값 + market_closed=True로 부드럽게 응답해야 한다 — 더는
    502가 아니다."""

    def fake_fetch(market):
        raise RuntimeError("totalCount=0")

    monkeypatch.setattr(flow_rank, "_fetch_value_rank_market_blocking", fake_fetch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/value-rank/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["market_closed"] is True
    assert body["rows"] == []


async def test_value_rank_live_empty_source_with_cache_reuses_last_good(monkeypatch):
    """같은 상황이지만 이전에 성공한 캐시가 있으면(장중 도중 소스가 일시적으로
    비어도) 그 값을 market_closed=True로 재사용한다 — 508 대신 "마지막으로 알려진
    값"을 보여주는 게 사용자에게 더 유용하다(장 마감 폴백과 동일한 원칙)."""
    good_calls = {"n": 0}

    def fake_fetch_good(market):
        good_calls["n"] += 1
        return KOSPI_RESULT if market == "kospi" else KOSDAQ_RESULT

    monkeypatch.setattr(flow_rank, "_fetch_value_rank_market_blocking", fake_fetch_good)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get("/api/markets/value-rank/live")
    assert first.status_code == 200
    first_body = first.json()

    # TTL을 즉시 만료시켜 다음 요청이 실제로 재조회를 시도하게 한다.
    flow_rank._value_rank_live_cache["ts"] = 0.0

    def fake_fetch_empty(market):
        raise RuntimeError("totalCount=0")

    monkeypatch.setattr(flow_rank, "_fetch_value_rank_market_blocking", fake_fetch_empty)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        second = await client.get("/api/markets/value-rank/live")

    assert second.status_code == 200
    second_body = second.json()
    assert second_body["market_closed"] is True
    assert second_body["rows"] == first_body["rows"]  # 마지막 알려진 값 재사용


async def test_value_rank_live_market_closed_skips_fetch_no_cache(monkeypatch):
    monkeypatch.setattr(flow_rank, "is_nxt_closed", lambda now_kst: True)

    def _raise(market):  # pragma: no cover - 불리면 안 됨
        raise AssertionError("naver_value_rank should not be called when market is closed")

    monkeypatch.setattr(flow_rank, "_fetch_value_rank_market_blocking", _raise)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/value-rank/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["market_closed"] is True
    assert body["rows"] == []


async def test_value_rank_live_flow_rank_live_route_removed():
    """모듈 docstring "flow-rank/live는 만들지 않는다" — 실측 근거로 라이브
    엔드포인트를 아예 추가하지 않았으므로 404가 정상이다(EOD `/flow-rank`만 유지)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/flow-rank/live")

    assert resp.status_code == 404
