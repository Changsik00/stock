"""Integration tests for /api/auto-trade/* (app.routers.auto_trade, PLAN.md
§5.54) — 자동매매 엔진 상태 조회 · 킬스위치 · 매매일지.

Same pattern as tests/test_paper_trades_router.py: real dev Postgres via
app.db.async_session_factory, throwaway FastAPI app including only this
router. `AutoTradeState`는 싱글턴(id=1)이라 tests/test_auto_trader_collector.py와
동일하게 스냅샷/복원 방식으로 실제 배포 상태를 훼손하지 않는다.

`_warm_stock_intraday`는 `app.routers.auto_trade`에 이름으로 임포트돼 있으므로
그 이름을 몽키패치한다(라이브 가격 없이도 상태 조회를 검증할 수 있게)."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.collectors.auto_trader import STATE_ID
from app.db import async_session_factory, engine
from app.models import AutoTradeLog, AutoTradeState
from app.routers import auto_trade


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(auto_trade.router)
    return app


async def _current_max_log_id() -> int:
    async with async_session_factory() as session:
        result = await session.execute(select(func.max(AutoTradeLog.id)))
        return result.scalar() or 0


@pytest.fixture(autouse=True)
async def _snapshot_and_restore_state():
    async with async_session_factory() as session:
        row = await session.get(AutoTradeState, STATE_ID)
        snapshot = (
            {
                "enabled": row.enabled,
                "status": row.status,
                "code": row.code,
                "entry_price": row.entry_price,
                "entry_qty": row.entry_qty,
                "entry_at": row.entry_at,
                "entry_order_no": row.entry_order_no,
                "peak_price": row.peak_price,
                "entry_foreign_flow_sign": row.entry_foreign_flow_sign,
            }
            if row is not None
            else None
        )

    start_max_log_id = await _current_max_log_id()

    yield

    async with async_session_factory() as session:
        row = await session.get(AutoTradeState, STATE_ID)
        if snapshot is None:
            if row is not None:
                await session.delete(row)
        else:
            if row is None:
                row = AutoTradeState(id=STATE_ID)
                session.add(row)
            for k, v in snapshot.items():
                setattr(row, k, v)
        await session.commit()
        await session.execute(AutoTradeLog.__table__.delete().where(AutoTradeLog.id > start_max_log_id))
        await session.commit()
    await engine.dispose()


@pytest.fixture(autouse=True)
def _default_intraday_fails(monkeypatch):
    """기본값 — 라이브 가격 조회를 항상 실패시켜(장 마감 취급) 개별 테스트가
    잊고 몽키패치를 안 해도 실제 키움을 호출하지 않게 한다(test_paper_trades_router.py
    와 동일한 관례)."""

    async def _default_intraday(code, interval):
        raise RuntimeError("no live source configured for this test")

    monkeypatch.setattr(auto_trade, "_warm_stock_intraday", _default_intraday)


async def _set_state(**kwargs) -> None:
    async with async_session_factory() as session:
        row = await session.get(AutoTradeState, STATE_ID)
        if row is None:
            row = AutoTradeState(id=STATE_ID)
            session.add(row)
        defaults = dict(
            enabled=False,
            status="idle",
            code="0167A0",
            entry_price=None,
            entry_qty=None,
            entry_at=None,
            entry_order_no=None,
            peak_price=None,
            entry_foreign_flow_sign=None,
        )
        defaults.update(kwargs)
        for k, v in defaults.items():
            setattr(row, k, v)
        await session.commit()


# ---------------------------------------------------------------------------
# GET /api/auto-trade/state
# ---------------------------------------------------------------------------


async def test_get_state_default_is_disabled_idle():
    await _set_state(enabled=False, status="idle")
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/auto-trade/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["status"] == "idle"
    assert body["code"] == "0167A0"
    assert body["current_price"] is None


async def test_get_state_holding_includes_unrealized_pnl(monkeypatch):
    await _set_state(enabled=True, status="holding", entry_price=16000, entry_qty=1)

    async def fake_intraday(code, interval):
        assert code == "0167A0"
        return {"bars": [{"close": 16320.0}]}

    monkeypatch.setattr(auto_trade, "_warm_stock_intraday", fake_intraday)

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/auto-trade/state")
    body = resp.json()
    assert body["status"] == "holding"
    assert body["current_price"] == pytest.approx(16320.0)
    assert body["unrealized_pnl"] == pytest.approx(320.0)
    assert body["unrealized_pnl_pct"] == pytest.approx(2.0)


async def test_get_state_holding_price_fetch_failure_returns_none_not_500():
    await _set_state(enabled=True, status="holding", entry_price=16000, entry_qty=1)
    # _default_intraday_fails 픽스처가 이미 실패하도록 몽키패치해 둠.
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/auto-trade/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["current_price"] is None
    assert body["unrealized_pnl"] is None


# ---------------------------------------------------------------------------
# POST /api/auto-trade/toggle
# ---------------------------------------------------------------------------


async def test_toggle_on_then_off():
    await _set_state(enabled=False, status="idle")
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp_on = await client.post("/api/auto-trade/toggle", json={"enabled": True})
        assert resp_on.json()["enabled"] is True

        resp_off = await client.post("/api/auto-trade/toggle", json={"enabled": False})
        assert resp_off.json()["enabled"] is False


async def test_toggle_on_preserves_existing_holding_position():
    """킬스위치를 껐다 켜도 이미 보유 중인 포지션 정보(entry_price 등)는
    건드리지 않는다 — PLAN.md §5.54 안전 설계의 핵심 요구."""
    await _set_state(
        enabled=False,
        status="holding",
        entry_price=16000,
        entry_qty=1,
        entry_order_no="0012345",
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auto-trade/toggle", json={"enabled": True})
    body = resp.json()
    assert body["enabled"] is True
    assert body["status"] == "holding"
    assert body["entry_price"] == pytest.approx(16000.0)
    assert body["entry_order_no"] == "0012345"


# ---------------------------------------------------------------------------
# GET /api/auto-trade/log
# ---------------------------------------------------------------------------


async def test_get_log_returns_rows_newest_first():
    async with async_session_factory() as session:
        base = dt.datetime.now(dt.timezone.utc)
        session.add(
            AutoTradeLog(
                ts=base - dt.timedelta(minutes=5),
                event_type="entry",
                code="0167A0",
                price=16000,
                reason="첫 번째 기록",
            )
        )
        session.add(
            AutoTradeLog(
                ts=base,
                event_type="exit_stop_loss",
                code="0167A0",
                price=15760,
                reason="두 번째 기록",
            )
        )
        await session.commit()

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/auto-trade/log?limit=10")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert len(rows) >= 2
    assert rows[0]["reason"] == "두 번째 기록"  # 최신이 먼저
    assert rows[1]["reason"] == "첫 번째 기록"


async def test_get_log_respects_limit():
    async with async_session_factory() as session:
        base = dt.datetime.now(dt.timezone.utc)
        for i in range(5):
            session.add(
                AutoTradeLog(
                    ts=base - dt.timedelta(minutes=i),
                    event_type="entry",
                    code="0167A0",
                    price=16000,
                    reason=f"기록 {i}",
                )
            )
        await session.commit()

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/auto-trade/log?limit=2")
    assert len(resp.json()["rows"]) == 2
