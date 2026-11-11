"""Unit tests for GET /api/markets/futures-flow/live (app.routers.markets,
PLAN.md §4.7 3단 갱신 주기 — 2026-07-20 장중 실측으로 5~10분 티어 편입).

httpx.AsyncClient + ASGITransport against the real FastAPI app. No DB session
needed and no real network — the blocking naver_futures_flow call is
monkeypatched via markets._fetch_futures_flow_blocking.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.market_hours import KST
from app.routers import markets

RESULT = {
    "date": dt.date(2026, 7, 20),
    "flows": [
        {"investor": "개인", "net_value": -92100, "net_volume": None},
        {"investor": "외국인", "net_value": 53300, "net_volume": None},
        {"investor": "기관계", "net_value": 159200, "net_volume": None},
    ],
}


@pytest.fixture(autouse=True)
def _reset_cache():
    markets._futures_flow_live_cache.update({"data": None, "ts": 0.0, "fetched_at": None})
    yield
    markets._futures_flow_live_cache.update({"data": None, "ts": 0.0, "fetched_at": None})


@pytest.fixture(autouse=True)
def _force_market_open(monkeypatch):
    monkeypatch.setattr(markets, "_market_closed_kst", lambda now_kst: False)


async def test_futures_flow_live_returns_investors(monkeypatch):
    monkeypatch.setattr(markets, "_fetch_futures_flow_blocking", lambda target_date: RESULT)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/futures-flow/live")

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-07-20"
    assert body["investors"]["외국인"]["net_value"] == 53300
    assert body["market_closed"] is False
    assert "cached_at" in body


async def test_futures_flow_live_handles_none_result_as_empty_investors(monkeypatch):
    monkeypatch.setattr(markets, "_fetch_futures_flow_blocking", lambda target_date: None)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/futures-flow/live")

    assert resp.status_code == 200
    assert resp.json()["investors"] == {}


async def test_futures_flow_live_502_on_error(monkeypatch):
    def _raise(target_date):
        raise RuntimeError("boom")

    monkeypatch.setattr(markets, "_fetch_futures_flow_blocking", _raise)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/futures-flow/live")

    assert resp.status_code == 502


async def test_futures_flow_live_caches_within_ttl(monkeypatch):
    calls = []

    def fake_fetch(target_date):
        calls.append(target_date)
        return RESULT

    monkeypatch.setattr(markets, "_fetch_futures_flow_blocking", fake_fetch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/api/markets/futures-flow/live")
        r2 = await client.get("/api/markets/futures-flow/live")

    assert r1.json()["cached_at"] == r2.json()["cached_at"]
    assert len(calls) == 1


# ---- 장 마감 분기(2026-11-11 이슈 #3) ----

NEW_RESULT = {
    "date": dt.date(2026, 11, 11),
    "flows": [{"investor": "외국인", "net_value": -269400, "net_volume": None}],
}
OLD_RESULT = {
    "date": dt.date(2026, 11, 11),
    "flows": [{"investor": "외국인", "net_value": -73400, "net_volume": None}],
}


def _at(day, hour, minute):
    return dt.datetime(2026, 11, day, hour, minute, tzinfo=KST)


def _go_closed(monkeypatch, now_kst):
    """마감 상태 + 현재 KST 시각 고정."""
    monkeypatch.setattr(markets, "_market_closed_kst", lambda n: True)
    monkeypatch.setattr(markets, "kst_now", lambda: now_kst)


def _counting_fetch(monkeypatch, result):
    calls = []

    def fake(target_date):
        calls.append(target_date)
        return result

    monkeypatch.setattr(markets, "_fetch_futures_flow_blocking", fake)
    return calls


async def _get():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get("/api/markets/futures-flow/live")


async def test_closed_no_cache_fetches_from_source(monkeypatch):
    _go_closed(monkeypatch, _at(11, 15, 47))
    calls = _counting_fetch(monkeypatch, NEW_RESULT)
    body = (await _get()).json()
    assert len(calls) == 1
    assert body["market_closed"] is True
    assert body["investors"]["외국인"]["net_value"] == -269400


async def test_closed_cache_fetched_before_1550_is_refetched(monkeypatch):
    _go_closed(monkeypatch, _at(11, 15, 47))
    markets._futures_flow_live_cache.update(
        {
            "data": {"date": "2026-11-11", "investors": {"외국인": {"net_value": -73400}}, "market_closed": False, "cached_at": "x"},
            "ts": 0.0,  # TTL 만료
            "fetched_at": _at(11, 15, 28),
        }
    )
    calls = _counting_fetch(monkeypatch, NEW_RESULT)
    body = (await _get()).json()
    assert len(calls) == 1
    assert body["investors"]["외국인"]["net_value"] == -269400
    assert body["market_closed"] is True


async def test_closed_cache_fetched_after_1550_skips_source(monkeypatch):
    _go_closed(monkeypatch, _at(11, 19, 0))
    markets._futures_flow_live_cache.update(
        {
            "data": {"date": "2026-11-11", "investors": {"외국인": {"net_value": -269400}}, "market_closed": True, "cached_at": "x"},
            "ts": 0.0,
            "fetched_at": _at(11, 18, 10),
        }
    )
    calls = _counting_fetch(monkeypatch, NEW_RESULT)
    body = (await _get()).json()
    assert calls == []
    assert body["investors"]["외국인"]["net_value"] == -269400
    assert body["market_closed"] is True


async def test_next_morning_with_previous_evening_cache_skips_source(monkeypatch):
    _go_closed(monkeypatch, _at(12, 8, 0))
    markets._futures_flow_live_cache.update(
        {
            "data": {"date": "2026-11-11", "investors": {"외국인": {"net_value": -269400}}, "market_closed": True, "cached_at": "x"},
            "ts": 0.0,
            "fetched_at": _at(11, 18, 10),
        }
    )
    calls = _counting_fetch(monkeypatch, NEW_RESULT)
    body = (await _get()).json()
    assert calls == []
    assert body["market_closed"] is True


async def test_closed_refetch_failure_falls_back_to_cache_and_retries(monkeypatch):
    _go_closed(monkeypatch, _at(11, 15, 47))
    old_fetched = _at(11, 15, 28)
    markets._futures_flow_live_cache.update(
        {
            "data": {"date": "2026-11-11", "investors": {"외국인": {"net_value": -73400}}, "market_closed": False, "cached_at": "x"},
            "ts": 0.0,
            "fetched_at": old_fetched,
        }
    )

    def _raise(target_date):
        raise RuntimeError("boom")

    monkeypatch.setattr(markets, "_fetch_futures_flow_blocking", _raise)
    resp = await _get()
    assert resp.status_code == 200
    body = resp.json()
    assert body["market_closed"] is True
    assert body["investors"]["외국인"]["net_value"] == -73400
    # 확정 조회로 기록되지 않았다
    assert markets._futures_flow_live_cache["fetched_at"] == old_fetched

    # 다음 TTL에 재시도되어 새 값으로 교체
    markets._futures_flow_live_cache["ts"] = 0.0
    calls = _counting_fetch(monkeypatch, NEW_RESULT)
    body = (await _get()).json()
    assert len(calls) == 1
    assert body["investors"]["외국인"]["net_value"] == -269400


async def test_closed_refetch_failure_no_cache_returns_empty(monkeypatch):
    _go_closed(monkeypatch, _at(11, 15, 47))

    def _raise(target_date):
        raise RuntimeError("boom")

    monkeypatch.setattr(markets, "_fetch_futures_flow_blocking", _raise)
    resp = await _get()
    assert resp.status_code == 200
    body = resp.json()
    assert body["investors"] == {}
    assert body["market_closed"] is True
    assert markets._futures_flow_live_cache["fetched_at"] is None


async def test_closed_none_result_recorded_as_done(monkeypatch):
    # 휴장일 — None이면 빈 investors로 응답하고 확정 조회로 기록해 재호출하지 않는다.
    _go_closed(monkeypatch, _at(14, 16, 0))
    calls = _counting_fetch(monkeypatch, None)
    body = (await _get()).json()
    assert body["investors"] == {}
    assert body["date"] == "2026-11-14"
    assert len(calls) == 1
    markets._futures_flow_live_cache["ts"] = 0.0
    await _get()
    assert len(calls) == 1


async def test_closed_none_result_keeps_same_day_nonempty_cache(monkeypatch):
    _go_closed(monkeypatch, _at(11, 15, 47))
    markets._futures_flow_live_cache.update(
        {
            "data": {"date": "2026-11-11", "investors": {"외국인": {"net_value": -73400}}, "market_closed": False, "cached_at": "x"},
            "ts": 0.0,
            "fetched_at": _at(11, 15, 28),
        }
    )
    _counting_fetch(monkeypatch, None)
    body = (await _get()).json()
    assert body["investors"]["외국인"]["net_value"] == -73400
    assert body["market_closed"] is True


def test_last_close_confirm_kst_boundaries():
    from app.market_hours import last_close_confirm_kst

    assert last_close_confirm_kst(_at(11, 18, 4)) == _at(10, 18, 5)
    assert last_close_confirm_kst(_at(11, 18, 5)) == _at(11, 18, 5)
    assert last_close_confirm_kst(_at(11, 23, 59)) == _at(11, 18, 5)
    assert last_close_confirm_kst(_at(12, 0, 0)) == _at(11, 18, 5)
    assert last_close_confirm_kst(_at(12, 8, 0)) == _at(11, 18, 5)


def test_required_confirm_kst_boundaries():
    from app.market_hours import required_confirm_kst

    # 15:30~18:05(평일)에는 오늘 18:05(미래) — 어떤 캐시도 충족 못 해 재조회된다
    assert required_confirm_kst(_at(11, 15, 30)) == _at(11, 18, 5)
    assert required_confirm_kst(_at(11, 18, 4)) == _at(11, 18, 5)
    assert required_confirm_kst(_at(11, 18, 5)) == _at(11, 18, 5)
    # 개장 전/장중/주말은 last_close_confirm_kst와 동일
    assert required_confirm_kst(_at(12, 8, 0)) == _at(11, 18, 5)
    assert required_confirm_kst(_at(11, 12, 0)) == _at(10, 18, 5)
    assert required_confirm_kst(_at(14, 15, 40)) == _at(13, 18, 5)  # 토요일 15:40
