"""Unit tests for GET /api/markets/{market}/intraday (app.routers.markets) —
지수 분봉(PLAN.md §5.1), 키움 ka20005 소스.

Same no-DB/no-network philosophy as test_markets_attention_router.py —
app.routers.markets.KiwoomClient is monkeypatched to a fake async-context-manager
class so no real Kiwoom network call happens.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.routers import markets

_real_kiwoom_client = markets.KiwoomClient


def _make_fake_kiwoom_client(calls: dict, response_or_exc):
    class _FakeClient:
        call_count = 0

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def sector_minute_chart(self, inds_cd: str, tic_scope: str, **kwargs):
            _FakeClient.call_count += 1
            calls["n"] = calls.get("n", 0) + 1
            calls["inds_cd"] = inds_cd
            calls["tic_scope"] = tic_scope
            if isinstance(response_or_exc, Exception):
                raise response_or_exc
            return response_or_exc, {"cont-yn": "N", "next-key": "", "api-id": "ka20005"}

    return _FakeClient


@pytest.fixture(autouse=True)
def _clear_intraday_cache():
    markets._intraday_cache.clear()
    yield
    markets._intraday_cache.clear()


@pytest.fixture(autouse=True)
def _restore_kiwoom_client():
    yield
    markets.KiwoomClient = _real_kiwoom_client


def _fake_sector_response() -> dict:
    return {
        "return_code": 0,
        "inds_min_pole_qry": [
            {"cur_prc": "-651627", "trde_qty": "16249", "cntr_tm": "20260720153000",
             "open_pric": "+654294", "high_pric": "+654294", "low_pric": "+651625"},
            {"cur_prc": "-654294", "trde_qty": "198", "cntr_tm": "20260720090000",
             "open_pric": "+654294", "high_pric": "+654294", "low_pric": "+654294"},
        ],
    }


async def test_market_intraday_kospi_returns_ascending_bars():
    calls: dict = {}
    markets.KiwoomClient = _make_fake_kiwoom_client(calls, _fake_sector_response())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/kospi/intraday", params={"interval": 5})

    assert resp.status_code == 200
    body = resp.json()
    assert body["market"] == "kospi"
    assert body["interval"] == 5
    assert body["date"] == "20260720"
    assert [b["time"] for b in body["bars"]] == ["0900", "1530"]  # 오름차순
    assert calls["inds_cd"] == "001"
    assert calls["tic_scope"] == "5"


async def test_market_intraday_kosdaq_uses_101_inds_cd():
    calls: dict = {}
    markets.KiwoomClient = _make_fake_kiwoom_client(calls, _fake_sector_response())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/kosdaq/intraday", params={"interval": 1})

    assert resp.status_code == 200
    assert calls["inds_cd"] == "101"


async def test_market_intraday_futures_returns_501_without_calling_kiwoom():
    def _raise(*args, **kwargs):  # pragma: no cover - 불리면 안 됨
        raise AssertionError("KiwoomClient should not be constructed for futures intraday")

    markets.KiwoomClient = _raise

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/futures/intraday", params={"interval": 5})

    assert resp.status_code == 501
    assert resp.json()["detail"]["market"] == "futures"


async def test_market_intraday_invalid_interval_returns_400():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/kospi/intraday", params={"interval": 7})

    assert resp.status_code == 400


async def test_market_intraday_invalid_market_returns_400():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/notamarket/intraday", params={"interval": 5})

    assert resp.status_code == 400


async def test_market_intraday_kiwoom_failure_returns_502():
    calls: dict = {}
    markets.KiwoomClient = _make_fake_kiwoom_client(calls, RuntimeError("boom"))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/kospi/intraday", params={"interval": 5})

    assert resp.status_code == 502
    assert resp.json()["detail"]["source"] == "kiwoom_ka20005"


async def test_market_intraday_second_request_within_ttl_hits_cache():
    calls: dict = {}
    fake_cls = _make_fake_kiwoom_client(calls, _fake_sector_response())
    markets.KiwoomClient = fake_cls

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/api/markets/kospi/intraday", params={"interval": 60})
        r2 = await client.get("/api/markets/kospi/intraday", params={"interval": 60})

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert fake_cls.call_count == 1
    assert r1.json() == r2.json()
