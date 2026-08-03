"""Integration tests for /api/paper-trades* (app.routers.paper_trades, PLAN.md
§5.51) — 가상 매매 기록 장부.

Same pattern as tests/test_basis_router.py: real dev Postgres via
app.db.async_session_factory, throwaway FastAPI app including only this
router (no dependency override needed — get_session already points at the
dev DB per .env). Rows created during a test are cleaned up in teardown by
recording the max id before the test and deleting anything created above it
afterwards (paper_trade has no natural "far future date" partition like
IndexOhlcv, so an id-watermark is the simplest safe cleanup).

Live-price mocking follows tests/test_markets_pair_view_and_nasdaq_futures_router.py's
convention: `_warm_stock_intraday`/`_fetch_etf_nav_safe` are imported by name
into app.routers.paper_trades, so monkeypatching the attribute on that module
replaces what `_get_live_price` calls.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.db import async_session_factory, engine
from app.models import PaperTrade
from app.routers import paper_trades


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(paper_trades.router)
    return app


async def _current_max_id() -> int:
    async with async_session_factory() as session:
        result = await session.execute(select(func.max(PaperTrade.id)))
        return result.scalar() or 0


@pytest.fixture(autouse=True)
async def _cleanup_paper_trades():
    start_max_id = await _current_max_id()
    yield
    async with async_session_factory() as session:
        await session.execute(PaperTrade.__table__.delete().where(PaperTrade.id > start_max_id))
        await session.commit()
    await engine.dispose()


@pytest.fixture(autouse=True)
def _clear_monkeypatched_helpers(monkeypatch):
    """기본값 — 라이브 가격 조회를 항상 실패시켜(장 마감 취급) 개별 테스트가
    잊고 몽키패치를 안 해도 실제 키움/네이버를 호출하지 않게 한다."""

    async def _default_intraday(code, interval):
        raise RuntimeError("no live source configured for this test")

    async def _default_etf_nav():
        return {}

    monkeypatch.setattr(paper_trades, "_warm_stock_intraday", _default_intraday)
    monkeypatch.setattr(paper_trades, "_fetch_etf_nav_safe", _default_etf_nav)
    yield


async def _create(client, **overrides) -> dict:
    payload = {"code": "005930", "side": "buy", "entry_price": 70000, "entry_qty": 10, "note": "test entry"}
    payload.update(overrides)
    resp = await client.post("/api/paper-trades", json=payload)
    return resp


# -- POST /api/paper-trades ---------------------------------------------------------


async def test_create_and_list_open_position_includes_live_price(monkeypatch):
    async def fake_intraday(code, interval):
        assert code == "005930"
        assert interval == 1
        return {"bars": [{"close": 71000}]}

    monkeypatch.setattr(paper_trades, "_warm_stock_intraday", fake_intraday)

    async with AsyncClient(transport=ASGITransport(app=_make_app()), base_url="http://test") as client:
        create_resp = await _create(client, code="005930", side="buy", entry_price=70000, entry_qty=10)
        assert create_resp.status_code == 200
        created = create_resp.json()
        assert created["status"] == "open"
        assert created["code"] == "005930"
        assert created["name"] == "삼성전자"
        assert created["note"] == "test entry"
        trade_id = created["id"]

        list_resp = await client.get("/api/paper-trades", params={"status": "open"})
        assert list_resp.status_code == 200
        rows = list_resp.json()["rows"]
        row = next(r for r in rows if r["id"] == trade_id)
        assert row["current_price"] == 71000
        assert row["unrealized_pnl"] == (71000 - 70000) * 10
        assert row["unrealized_pnl_pct"] == round((71000 - 70000) * 10 / (70000 * 10) * 100, 4)
        assert row["realized_pnl"] is None


async def test_create_invalid_code_returns_400():
    async with AsyncClient(transport=ASGITransport(app=_make_app()), base_url="http://test") as client:
        resp = await _create(client, code="999999")
    assert resp.status_code == 400


async def test_create_invalid_side_returns_400():
    async with AsyncClient(transport=ASGITransport(app=_make_app()), base_url="http://test") as client:
        resp = await _create(client, side="short")
    assert resp.status_code == 400


async def test_create_nonpositive_price_or_qty_returns_400():
    async with AsyncClient(transport=ASGITransport(app=_make_app()), base_url="http://test") as client:
        resp1 = await _create(client, entry_price=0)
        resp2 = await _create(client, entry_qty=-1)
    assert resp1.status_code == 400
    assert resp2.status_code == 400


# -- POST /api/paper-trades/{id}/close ------------------------------------------------


async def test_close_long_position_positive_pnl_when_exit_above_entry():
    """롱(side=buy)은 청산가가 진입가보다 높으면 실현손익이 양수여야 한다."""
    async with AsyncClient(transport=ASGITransport(app=_make_app()), base_url="http://test") as client:
        created = (await _create(client, code="005930", side="buy", entry_price=100.0, entry_qty=10)).json()
        resp = await client.post(f"/api/paper-trades/{created['id']}/close", json={"exit_price": 110.0})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "closed"
    assert body["exit_price"] == 110.0
    assert body["realized_pnl"] == (110.0 - 100.0) * 10
    assert body["realized_pnl"] > 0
    assert body["realized_pnl_pct"] == pytest.approx(10.0)


async def test_close_long_position_negative_pnl_when_exit_below_entry():
    async with AsyncClient(transport=ASGITransport(app=_make_app()), base_url="http://test") as client:
        created = (await _create(client, code="005930", side="buy", entry_price=100.0, entry_qty=10)).json()
        resp = await client.post(f"/api/paper-trades/{created['id']}/close", json={"exit_price": 90.0})

    body = resp.json()
    assert body["realized_pnl"] == (90.0 - 100.0) * 10
    assert body["realized_pnl"] < 0


async def test_close_short_position_positive_pnl_when_exit_below_entry():
    """숏(side=sell)은 청산가가 진입가보다 낮으면 실현손익이 양수여야 한다(롱과 반대)."""
    async with AsyncClient(transport=ASGITransport(app=_make_app()), base_url="http://test") as client:
        created = (await _create(client, code="005930", side="sell", entry_price=100.0, entry_qty=10)).json()
        resp = await client.post(f"/api/paper-trades/{created['id']}/close", json={"exit_price": 90.0})

    body = resp.json()
    assert body["realized_pnl"] == (100.0 - 90.0) * 10
    assert body["realized_pnl"] > 0


async def test_close_short_position_negative_pnl_when_exit_above_entry():
    async with AsyncClient(transport=ASGITransport(app=_make_app()), base_url="http://test") as client:
        created = (await _create(client, code="005930", side="sell", entry_price=100.0, entry_qty=10)).json()
        resp = await client.post(f"/api/paper-trades/{created['id']}/close", json={"exit_price": 110.0})

    body = resp.json()
    assert body["realized_pnl"] == (100.0 - 110.0) * 10
    assert body["realized_pnl"] < 0


async def test_close_unknown_id_returns_404():
    async with AsyncClient(transport=ASGITransport(app=_make_app()), base_url="http://test") as client:
        resp = await client.post("/api/paper-trades/999999999/close", json={"exit_price": 100.0})
    assert resp.status_code == 404


async def test_close_already_closed_returns_409():
    async with AsyncClient(transport=ASGITransport(app=_make_app()), base_url="http://test") as client:
        created = (await _create(client)).json()
        first = await client.post(f"/api/paper-trades/{created['id']}/close", json={"exit_price": 71000.0})
        second = await client.post(f"/api/paper-trades/{created['id']}/close", json={"exit_price": 72000.0})

    assert first.status_code == 200
    assert second.status_code == 409


# -- DELETE /api/paper-trades/{id} ---------------------------------------------------


async def test_delete_removes_row_from_list():
    async with AsyncClient(transport=ASGITransport(app=_make_app()), base_url="http://test") as client:
        created = (await _create(client)).json()
        trade_id = created["id"]

        del_resp = await client.delete(f"/api/paper-trades/{trade_id}")
        assert del_resp.status_code == 204

        list_resp = await client.get("/api/paper-trades", params={"status": "all"})
        ids = [r["id"] for r in list_resp.json()["rows"]]
        assert trade_id not in ids


async def test_delete_unknown_id_returns_404():
    async with AsyncClient(transport=ASGITransport(app=_make_app()), base_url="http://test") as client:
        resp = await client.delete("/api/paper-trades/999999999")
    assert resp.status_code == 404


# -- GET /api/paper-trades -------------------------------------------------------------


async def test_list_open_position_live_price_failure_returns_null_fields_not_500():
    """`_warm_stock_intraday`가 실패해도(장 마감, 키움 502 등) 목록 조회는 200을
    유지하고 current_price/unrealized_pnl/unrealized_pnl_pct만 null이어야 한다."""
    async with AsyncClient(transport=ASGITransport(app=_make_app()), base_url="http://test") as client:
        created = (await _create(client, code="005930")).json()

        list_resp = await client.get("/api/paper-trades", params={"status": "open"})

    assert list_resp.status_code == 200
    row = next(r for r in list_resp.json()["rows"] if r["id"] == created["id"])
    assert row["current_price"] is None
    assert row["unrealized_pnl"] is None
    assert row["unrealized_pnl_pct"] is None


async def test_list_invalid_status_returns_400():
    async with AsyncClient(transport=ASGITransport(app=_make_app()), base_url="http://test") as client:
        resp = await client.get("/api/paper-trades", params={"status": "bogus"})
    assert resp.status_code == 400


async def test_list_etf_leg_uses_etf_nav_live_price(monkeypatch):
    """레버리지/인버스 ETF 코드는 `_warm_stock_intraday`가 아니라
    `_fetch_etf_nav_safe`(naver_etf now_value)로 현재가를 가져와야 한다."""

    async def fake_etf_nav():
        return {"0193W0": {"now_value": 10900.0, "nav": 10850.0}}

    monkeypatch.setattr(paper_trades, "_fetch_etf_nav_safe", fake_etf_nav)

    async with AsyncClient(transport=ASGITransport(app=_make_app()), base_url="http://test") as client:
        created = (await _create(client, code="0193W0", side="buy", entry_price=10000, entry_qty=5)).json()
        list_resp = await client.get("/api/paper-trades", params={"status": "open"})

    row = next(r for r in list_resp.json()["rows"] if r["id"] == created["id"])
    assert row["current_price"] == 10900.0
    assert row["unrealized_pnl"] == (10900.0 - 10000) * 5
