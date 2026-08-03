"""Unit tests for GET /api/markets/hynix-relative-strength (app.routers.markets) —
PLAN.md §5.50-7 "탑다운 프레임 ④ 개별 종목 반응": SK하이닉스(000660) 등락률 −
코스피 등락률(단순 초과수익률 관찰치, z-score 아님, "강함/약함" 판정 아님).

Same no-DB/no-network philosophy as test_markets_index_tiles_router.py: 이
엔드포인트가 재사용하는 3개 함수(`markets._warm_index_tiles_live`,
`markets._warm_stock_intraday`, `markets.get_stock_series_from_db`)를 markets
네임스페이스에서 monkeypatch한다 — markets.py가 `from .stocks import
_warm_stock_intraday`로 심볼을 자기 네임스페이스로 끌어왔으므로 실제 라우트
코드에 적용되려면 반드시 markets 쪽에서 패치해야 한다.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import get_session
from app.main import app
from app.routers import markets


async def _unused_session():
    yield object()


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _fake_index_tiles(kospi_change_rate, market_closed=False):
    async def fake_warm_index_tiles_live(session):
        return {
            "kospi": {"close": 3050.0, "change_rate": kospi_change_rate} if kospi_change_rate is not None else None,
            "kosdaq": None,
            "futures": None,
            "market_closed": market_closed,
            "risk": None,
            "cached_at": "2026-08-03T00:00:00+00:00",
        }

    return fake_warm_index_tiles_live


async def test_hynix_relative_strength_computes_difference(monkeypatch):
    """정상 케이스 — 하이닉스 등락률, 코스피 등락률 둘 다 있을 때 뺄셈이 정확해야
    한다. bars 마지막(최신) 종가 205000, DB 마지막(전일) 종가 200000 ->
    (205000-200000)/200000*100 = 2.5%. 코스피 등락률 1.0% -> 상대강도 1.5%p."""
    monkeypatch.setattr(markets, "_warm_index_tiles_live", _fake_index_tiles(1.0))

    async def fake_warm_stock_intraday(code, interval):
        assert code == "000660"
        assert interval == 1
        return {
            "code": code,
            "interval": interval,
            "date": "20260803",
            "bars": [
                {"close": 203000},
                {"close": 205000},  # bars 오름차순, 마지막이 최신.
            ],
            "cached_at": "2026-08-03T00:00:00+00:00",
        }

    monkeypatch.setattr(markets, "_warm_stock_intraday", fake_warm_stock_intraday)

    async def fake_get_stock_series_from_db(session, code, days):
        assert code == "000660"
        assert days == 5
        return [
            {"date": "20260731", "close": 198000.0, "changeRate": -0.3},
            {"date": "20260803", "close": 200000.0, "changeRate": 1.0},  # 마지막 = 전일 종가.
        ]

    monkeypatch.setattr(markets, "get_stock_series_from_db", fake_get_stock_series_from_db)
    app.dependency_overrides[get_session] = _unused_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/hynix-relative-strength")

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == "000660"
    assert body["market_closed"] is False
    assert body["kospi_change_rate"] == 1.0
    assert body["hynix_change_rate"] == round((205000.0 - 200000.0) / 200000.0 * 100, 4)
    assert body["hynix_change_rate"] == 2.5
    assert body["relative_strength_pct"] == round(2.5 - 1.0, 4)
    assert body["relative_strength_pct"] == 1.5
    assert "computed_at" in body


async def test_hynix_relative_strength_market_closed_returns_all_null_and_skips_calls(monkeypatch):
    """장 마감이면 세 필드 모두 null이고, 하이닉스 인트라데이/DB 조회 자체를
    하지 않아야 한다 — 호출되면 AssertionError를 던지는 fake로 검증한다."""
    monkeypatch.setattr(markets, "_warm_index_tiles_live", _fake_index_tiles(None, market_closed=True))

    async def _raise_intraday(code, interval):  # pragma: no cover - 불리면 안 됨
        raise AssertionError("_warm_stock_intraday should not be called when market is closed")

    async def _raise_db(session, code, days):  # pragma: no cover - 불리면 안 됨
        raise AssertionError("get_stock_series_from_db should not be called when market is closed")

    monkeypatch.setattr(markets, "_warm_stock_intraday", _raise_intraday)
    monkeypatch.setattr(markets, "get_stock_series_from_db", _raise_db)
    app.dependency_overrides[get_session] = _unused_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/hynix-relative-strength")

    assert resp.status_code == 200
    body = resp.json()
    assert body["market_closed"] is True
    assert body["hynix_change_rate"] is None
    assert body["kospi_change_rate"] is None
    assert body["relative_strength_pct"] is None


async def test_hynix_relative_strength_survives_intraday_failure(monkeypatch):
    """하이닉스 인트라데이 조회가 예외(예: 키움 502)를 던져도 이 엔드포인트
    자체는 200을 반환해야 한다(전체 502로 전파되면 안 됨) — hynix_change_rate/
    relative_strength_pct만 null이고, 무관한 kospi_change_rate는 그대로 채워져야
    한다."""
    monkeypatch.setattr(markets, "_warm_index_tiles_live", _fake_index_tiles(1.0))

    async def fake_warm_stock_intraday(code, interval):
        raise RuntimeError("kiwoom boom")

    monkeypatch.setattr(markets, "_warm_stock_intraday", fake_warm_stock_intraday)

    async def _raise_db(session, code, days):  # pragma: no cover - 불리면 안 됨(intraday 실패 후에도 시도는 하지만, 값 계산은 latest_close가 없어 None 유지)
        return []

    monkeypatch.setattr(markets, "get_stock_series_from_db", _raise_db)
    app.dependency_overrides[get_session] = _unused_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/hynix-relative-strength")

    assert resp.status_code == 200
    body = resp.json()
    assert body["hynix_change_rate"] is None
    assert body["relative_strength_pct"] is None
    assert body["kospi_change_rate"] == 1.0
