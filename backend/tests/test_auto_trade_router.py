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

from app.collectors import auto_trader as auto_trader_module
from app.collectors.auto_trader import STATE_ID
from app.db import async_session_factory, engine
from app.models import AutoTradeLog, AutoTradeState
from app.routers import auto_trade
from tests.test_auto_trader_collector import (
    FAKE_QUOTE_RAW,
    _FakeKiwoomClient,
    _UnfilledBuyClient,
    _UnfilledSellClient,
)


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
    """싱글턴 AutoTradeState(id=1) 행을 스냅샷/복원한다(test_auto_trader_collector.py와
    동일한 패턴).

    **2026-08-11 안전장치 추가(실사고 직후)**: `test_toggle_on_preserves_existing_
    holding_position`은 "킬스위치를 켜도 보유 포지션이 보존되는지" 검증하려고
    실제 `POST /toggle {"enabled": true}`를 호출해 이 싱글턴 행에
    `enabled=True` + `status="holding"`을 실제로 커밋한다 — 이 저장소는 실
    배포된 백엔드 컨테이너가 60초/30초 간격으로 계속 폴링하는 바로 그 dev
    Postgres를 테스트도 그대로 쓴다. 진짜 킬스위치가 켜진 채로 이 파일을
    돌리면 그 커밋 찰나에 운영 중인 백그라운드 잡이 이를 진짜 포지션으로 읽고
    실제 매도 주문을 시도할 수 있다 — 2026-08-11 실측으로 정확히 재현됐다
    (그 시점 실계좌가 0주 보유라 키움이 "매도가능수량 부족"으로 거부해 실피해는
    없었지만, 계좌가 실제로 포지션을 들고 있었다면 진짜 손절/청산이 나갔을
    것이다). 그래서 테스트 시작 전 실제 킬스위치가 켜져 있으면 아예 테스트를
    거부한다 — 조용히 넘어가면 다음에 또 같은 사고가 날 수 있다."""
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

    if snapshot is not None and snapshot["enabled"]:
        pytest.fail(
            "실제 킬스위치(AutoTradeState.enabled)가 켜져 있는 상태로는 이 테스트 파일을 "
            "돌릴 수 없습니다 — test_toggle_on_preserves_existing_holding_position이 실제 "
            "이 싱글턴 행에 enabled=True + status=holding을 커밋하는 순간, 실 배포된 "
            "백엔드가 이를 진짜 포지션으로 읽고 실제 주문을 시도할 위험이 있습니다"
            "(2026-08-11 실측으로 재현된 사고). 먼저 "
            "`POST /api/auto-trade/toggle {\"enabled\": false}`로 킬스위치를 끄고 다시 "
            "실행하세요."
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


@pytest.fixture(autouse=True)
def _reset_log_dedup_memory():
    """PLAN.md §5.71 — `test_auto_trader_collector.py`의 동일 이름 fixture와
    같은 이유(모듈 전역 dict가 같은 pytest 프로세스 안에서 파일 경계 없이
    공유됨) — 이 파일도 `_log`(따라서 `_last_log_by_code`)를 거치는
    manual-buy/manual-sell 엔드포인트를 테스트하므로 독립적으로 리셋한다."""
    auto_trader_module._last_log_by_code.clear()
    yield
    auto_trader_module._last_log_by_code.clear()


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


async def _get_state() -> AutoTradeState:
    async with async_session_factory() as session:
        return await session.get(AutoTradeState, STATE_ID)


async def _log_rows_since(start_max_log_id: int) -> list[AutoTradeLog]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(AutoTradeLog).where(AutoTradeLog.id > start_max_log_id).order_by(AutoTradeLog.id)
        )
        return list(result.scalars().all())


def _patch_kiwoom_client(monkeypatch) -> type[_FakeKiwoomClient]:
    """**중요**: `manual-buy`는 `app.routers.auto_trade` 모듈 안에서 직접
    `KiwoomClient()`를 호출하지만, `manual-sell`은 `collectors/auto_trader.py`의
    기존 `_execute_sell`을 그대로 재사용한다 — 그 함수 몸체는 자기 모듈
    (`app.collectors.auto_trader`)의 전역 네임스페이스에 바인딩된 `KiwoomClient`를
    참조하므로, 라우터 모듈만 몽키패치하면 `manual-sell` 경로는 여전히 실제
    `KiwoomClient`(실거래 API)를 호출한다. 두 모듈 모두 패치해야 어느 경로로
    테스트하든 절대 실제 키움 API를 건드리지 않는다."""
    _FakeKiwoomClient.instances = []
    monkeypatch.setattr(auto_trade, "KiwoomClient", _FakeKiwoomClient)
    monkeypatch.setattr(auto_trader_module, "KiwoomClient", _FakeKiwoomClient)
    return _FakeKiwoomClient


def _raising_kiwoom_client_factory(*args, **kwargs):  # pragma: no cover - 호출되면 안 됨
    raise AssertionError(
        "KiwoomClient()가 호출됐다 — 검증 단계에서 거부돼야 할 요청이 주문 시도까지 이어졌다"
    )


def _patch_raising_kiwoom_client(monkeypatch) -> None:
    """`_patch_kiwoom_client`와 동일한 이유로 두 모듈 모두 패치한다."""
    monkeypatch.setattr(auto_trade, "KiwoomClient", _raising_kiwoom_client_factory)
    monkeypatch.setattr(auto_trader_module, "KiwoomClient", _raising_kiwoom_client_factory)


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


async def test_get_log_filters_by_kst_date():
    """PLAN.md §5.70 — 2099-01-0X처럼 실제 거래 이력과 절대 겹치지 않을 미래
    날짜로 고정해 실 dev DB의 다른 진짜 행과 섞이지 않게 한다(다른 테스트들이
    쓰는 `>= 2` 관용구 대신 이 테스트는 total/rows 정확한 개수를 확인해야
    하므로 날짜 자체를 충돌 불가능하게 고른다)."""
    async with async_session_factory() as session:
        # 2099-01-17 06:00 UTC == 2099-01-17 15:00 KST(그 날짜 안).
        session.add(
            AutoTradeLog(
                ts=dt.datetime(2099, 1, 17, 6, 0, tzinfo=dt.timezone.utc),
                event_type="entry", code="0167A0", price=16000, reason="1/17 기록",
            )
        )
        # 2099-01-16 15:01 UTC == 2099-01-17 00:01 KST(자정 막 넘어감, 여전히 1/17).
        session.add(
            AutoTradeLog(
                ts=dt.datetime(2099, 1, 16, 15, 1, tzinfo=dt.timezone.utc),
                event_type="entry", code="0167A0", price=16000, reason="1/17 자정 직후 기록",
            )
        )
        # 2099-01-16 14:59 UTC == 2099-01-16 23:59 KST(경계 밖 — 하루 전).
        session.add(
            AutoTradeLog(
                ts=dt.datetime(2099, 1, 16, 14, 59, tzinfo=dt.timezone.utc),
                event_type="entry", code="0167A0", price=16000, reason="1/16 기록",
            )
        )
        await session.commit()

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/auto-trade/log", params={"date": "2099-01-17", "limit": 50})

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    reasons = {r["reason"] for r in body["rows"]}
    assert reasons == {"1/17 기록", "1/17 자정 직후 기록"}


async def test_get_log_offset_paginates_and_total_ignores_offset():
    async with async_session_factory() as session:
        base = dt.datetime(2099, 2, 1, 6, 0, tzinfo=dt.timezone.utc)
        for i in range(3):
            session.add(
                AutoTradeLog(
                    ts=base - dt.timedelta(minutes=i),
                    event_type="entry", code="0167A0", price=16000, reason=f"2/1 기록 {i}",
                )
            )
        await session.commit()

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp_page1 = await client.get("/api/auto-trade/log", params={"date": "2099-02-01", "limit": 2, "offset": 0})
        resp_page2 = await client.get("/api/auto-trade/log", params={"date": "2099-02-01", "limit": 2, "offset": 2})

    page1 = resp_page1.json()
    page2 = resp_page2.json()
    assert page1["total"] == 3
    assert page2["total"] == 3  # total은 offset과 무관하게 항상 전체 개수
    assert len(page1["rows"]) == 2
    assert len(page2["rows"]) == 1
    # 두 페이지가 겹치지 않고 합치면 전체가 되는지(최신순 유지).
    assert [r["reason"] for r in page1["rows"]] == ["2/1 기록 0", "2/1 기록 1"]
    assert [r["reason"] for r in page2["rows"]] == ["2/1 기록 2"]


async def test_get_log_trades_only_defaults_to_hiding_non_trade_events():
    """PLAN.md §5.73 — 사용자 지적("정보가 장황하기만 하지 유의미한 정보를
    제공한다고 생각하지는 않아") 이후 기본값을 trades_only=true로 바꿨다.
    entry_blocked_risk 같은 "거래로 안 이어진" 이벤트는 파라미터 없이
    호출하면 안 보이고, trades_only=false를 명시해야만 보인다."""
    async with async_session_factory() as session:
        base = dt.datetime(2099, 3, 1, 6, 0, tzinfo=dt.timezone.utc)
        session.add(
            AutoTradeLog(
                ts=base, event_type="entry", code="0167A0", price=16000, reason="실제 진입",
            )
        )
        session.add(
            AutoTradeLog(
                ts=base - dt.timedelta(minutes=1),
                event_type="entry_blocked_risk", code="0167A0", price=16000, reason="리스크 경보로 보류",
            )
        )
        session.add(
            AutoTradeLog(
                ts=base - dt.timedelta(minutes=2),
                event_type="error", code="0167A0", price=16000, reason="주문 실패",
            )
        )
        await session.commit()

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp_default = await client.get("/api/auto-trade/log", params={"date": "2099-03-01", "limit": 50})
        resp_all = await client.get(
            "/api/auto-trade/log", params={"date": "2099-03-01", "limit": 50, "trades_only": "false"}
        )

    default_body = resp_default.json()
    all_body = resp_all.json()
    assert default_body["total"] == 1
    assert [r["event_type"] for r in default_body["rows"]] == ["entry"]
    assert all_body["total"] == 3
    assert {r["event_type"] for r in all_body["rows"]} == {"entry", "entry_blocked_risk", "error"}


# ---------------------------------------------------------------------------
# POST /api/auto-trade/manual-buy (PLAN.md §5.56 — 자동 감지 엔진 위에 얹는
# 수동 매수 버튼. 자동 진입 경로(_handle_idle)와 동일한 안전장치를 검증한다.)
# ---------------------------------------------------------------------------


async def test_manual_buy_rejected_when_kill_switch_off(monkeypatch):
    """수동 매수도 킬스위치를 우회하지 않는다 — 꺼져 있으면 가격 조회/주문
    시도 자체를 하지 않는다(KiwoomClient 인스턴스화 자체가 없음으로 검증)."""
    await _set_state(enabled=False, status="idle")
    # `_default_intraday_fails` autouse 픽스처가 이미 _warm_stock_intraday를 항상
    # 실패하도록 몽키패치해 둔다 — 킬스위치 검증이 그보다 먼저 걸려야 하므로
    # 이 함수가 호출되면 애초에 이 테스트가 다른 이유(가격 조회 실패)로 실패한다.
    _patch_raising_kiwoom_client(monkeypatch)

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auto-trade/manual-buy")

    assert resp.status_code == 409
    assert "킬스위치" in resp.json()["detail"]
    state = await _get_state()
    assert state.status == "idle"
    assert state.entry_price is None


async def test_manual_buy_rejected_when_already_holding(monkeypatch):
    await _set_state(enabled=True, status="holding", entry_price=16000, entry_qty=1)
    _patch_raising_kiwoom_client(monkeypatch)

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auto-trade/manual-buy")

    assert resp.status_code == 409
    assert "이미 보유" in resp.json()["detail"]


async def test_manual_buy_rejected_when_budget_exceeded(monkeypatch):
    await _set_state(enabled=True, status="idle")
    from app.collectors.auto_trader import AUTO_TRADE_TOTAL_BUDGET_KRW

    high_price = AUTO_TRADE_TOTAL_BUDGET_KRW + 5000

    async def fake_intraday(code, interval):
        return {"bars": [{"close": float(high_price)}]}

    monkeypatch.setattr(auto_trade, "_warm_stock_intraday", fake_intraday)
    fake_cls = _patch_kiwoom_client(monkeypatch)

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auto-trade/manual-buy")

    assert resp.status_code == 400
    assert "예산 가드" in resp.json()["detail"]
    assert fake_cls.instances == []  # 주문 시도 자체가 없어야 함

    state = await _get_state()
    assert state.status == "idle"


async def test_manual_buy_success_places_order_and_updates_state(monkeypatch):
    await _set_state(enabled=True, status="idle")

    async def fake_intraday(code, interval):
        assert code == "0167A0"
        return {"bars": [{"close": 16000.0}]}

    monkeypatch.setattr(auto_trade, "_warm_stock_intraday", fake_intraday)
    fake_cls = _patch_kiwoom_client(monkeypatch)

    start_max_log_id = await _current_max_log_id()

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auto-trade/manual-buy")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "holding"
    assert body["entry_price"] == pytest.approx(16100.0)  # 매도1호가(FAKE_QUOTE_RAW)로 지정가 매수

    fake = fake_cls.instances[-1]
    assert fake.buy_calls == [("0167A0", 1, 16100)]
    assert fake.sell_calls == []

    state = await _get_state()
    assert state.status == "holding"
    assert float(state.entry_price) == pytest.approx(16100.0)
    assert state.entry_qty == 1
    assert state.entry_order_no == "0099001"

    logs = await _log_rows_since(start_max_log_id)
    assert len(logs) == 1
    assert logs[0].event_type == "manual_entry"
    assert "사용자가 대시보드에서 수동으로 매수" in logs[0].reason


async def test_manual_buy_order_failure_keeps_state_idle(monkeypatch):
    await _set_state(enabled=True, status="idle")

    async def fake_intraday(code, interval):
        return {"bars": [{"close": 16000.0}]}

    monkeypatch.setattr(auto_trade, "_warm_stock_intraday", fake_intraday)

    class _FailingBuyClient(_FakeKiwoomClient):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.buy_error = RuntimeError("kiwoom 500")

    _FakeKiwoomClient.instances = []
    monkeypatch.setattr(auto_trade, "KiwoomClient", _FailingBuyClient)

    start_max_log_id = await _current_max_log_id()

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auto-trade/manual-buy")

    assert resp.status_code == 502
    state = await _get_state()
    assert state.status == "idle"
    assert state.entry_price is None

    logs = await _log_rows_since(start_max_log_id)
    assert len(logs) == 1
    assert logs[0].event_type == "error"


async def test_manual_buy_unconfirmed_when_order_not_filled(monkeypatch):
    """PLAN.md §5.71 — 수동 매수도 `_handle_idle`과 동일한 미체결 확인을
    거친다. 체결 미확인이면 502로 응답하고 status는 holding으로 넘어가면
    안 된다(취소 시도함)."""
    await _set_state(enabled=True, status="idle")

    async def fake_intraday(code, interval):
        return {"bars": [{"close": 16000.0}]}

    monkeypatch.setattr(auto_trade, "_warm_stock_intraday", fake_intraday)
    _FakeKiwoomClient.instances = []
    monkeypatch.setattr(auto_trade, "KiwoomClient", _UnfilledBuyClient)

    start_max_log_id = await _current_max_log_id()

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auto-trade/manual-buy")

    assert resp.status_code == 502
    assert "체결" in resp.json()["detail"]

    fake = _FakeKiwoomClient.instances[-1]
    assert fake.buy_calls == [("0167A0", 1, 16100)]
    assert fake.cancel_calls == [("0167A0", "0099001", 1)]

    state = await _get_state()
    assert state.status == "idle"
    assert state.entry_price is None

    logs = await _log_rows_since(start_max_log_id)
    assert len(logs) == 1
    assert logs[0].event_type == "buy_unconfirmed"


# ---------------------------------------------------------------------------
# POST /api/auto-trade/manual-sell — 매도는 킬스위치 상태와 무관하게 항상
# 허용된다는 절대 원칙을 검증하는 케이스가 핵심.
# ---------------------------------------------------------------------------


async def test_manual_sell_rejected_when_idle(monkeypatch):
    await _set_state(enabled=True, status="idle")
    _patch_raising_kiwoom_client(monkeypatch)

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auto-trade/manual-sell")

    assert resp.status_code == 409
    assert "보유 중이 아님" in resp.json()["detail"]


async def test_manual_sell_succeeds_even_when_kill_switch_off(monkeypatch):
    """핵심 케이스 — 매도는 킬스위치 상태와 무관하게 항상 허용돼야 한다.
    킬스위치가 꺼져 있어도(따라서 자동 손절/청산 감시도 함께 꺼져 있어도)
    사용자가 수동으로 보유 포지션을 팔 수 있어야 한다."""
    await _set_state(enabled=False, status="holding", entry_price=16000, entry_qty=1)

    async def fake_intraday(code, interval):
        return {"bars": [{"close": 16000.0}]}

    monkeypatch.setattr(auto_trade, "_warm_stock_intraday", fake_intraday)
    fake_cls = _patch_kiwoom_client(monkeypatch)

    start_max_log_id = await _current_max_log_id()

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auto-trade/manual-sell")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "idle"

    fake = fake_cls.instances[-1]
    assert fake.sell_calls == [("0167A0", 1, 16050)]  # 매수1호가로 지정가 매도

    state = await _get_state()
    assert state.status == "idle"
    assert state.entry_price is None
    assert state.enabled is False  # 킬스위치 자체는 매도로 인해 변하지 않음

    logs = await _log_rows_since(start_max_log_id)
    assert len(logs) == 1
    assert logs[0].event_type == "exit_manual"
    assert "사용자가 대시보드에서 수동으로 매도" in logs[0].reason


async def test_manual_sell_success_while_trailing_and_enabled(monkeypatch):
    await _set_state(enabled=True, status="trailing", entry_price=16000, entry_qty=1, peak_price=16500)

    async def fake_intraday(code, interval):
        return {"bars": [{"close": 16400.0}]}

    monkeypatch.setattr(auto_trade, "_warm_stock_intraday", fake_intraday)
    fake_cls = _patch_kiwoom_client(monkeypatch)

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auto-trade/manual-sell")

    assert resp.status_code == 200
    state = await _get_state()
    assert state.status == "idle"
    assert state.peak_price is None
    fake = fake_cls.instances[-1]
    assert fake.sell_calls == [("0167A0", 1, 16050)]


async def test_manual_sell_order_failure_keeps_state_holding(monkeypatch):
    await _set_state(enabled=True, status="holding", entry_price=16000, entry_qty=1)

    async def fake_intraday(code, interval):
        return {"bars": [{"close": 16000.0}]}

    monkeypatch.setattr(auto_trade, "_warm_stock_intraday", fake_intraday)

    class _FailingSellClient(_FakeKiwoomClient):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.sell_error = RuntimeError("kiwoom 500")

    _FakeKiwoomClient.instances = []
    monkeypatch.setattr(auto_trade, "KiwoomClient", _FailingSellClient)
    monkeypatch.setattr(auto_trader_module, "KiwoomClient", _FailingSellClient)

    start_max_log_id = await _current_max_log_id()

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auto-trade/manual-sell")

    assert resp.status_code == 502
    state = await _get_state()
    assert state.status == "holding"
    assert float(state.entry_price) == pytest.approx(16000.0)

    logs = await _log_rows_since(start_max_log_id)
    assert len(logs) == 1
    assert logs[0].event_type == "error"


async def test_manual_sell_unconfirmed_when_order_not_filled(monkeypatch):
    """PLAN.md §5.71 — 수동 매도도 `_execute_sell`을 그대로 재사용하므로
    자동으로 같은 미체결 확인을 거친다. 체결 미확인이면 502로 응답하고
    포지션(holding)이 그대로 유지돼야 한다(idle로 넘어가면 안 됨 — 8/14
    실사고와 동일한 유형)."""
    await _set_state(enabled=True, status="holding", entry_price=16000, entry_qty=1)

    async def fake_intraday(code, interval):
        return {"bars": [{"close": 16000.0}]}

    monkeypatch.setattr(auto_trade, "_warm_stock_intraday", fake_intraday)
    _FakeKiwoomClient.instances = []
    monkeypatch.setattr(auto_trade, "KiwoomClient", _UnfilledSellClient)
    monkeypatch.setattr(auto_trader_module, "KiwoomClient", _UnfilledSellClient)

    start_max_log_id = await _current_max_log_id()

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auto-trade/manual-sell")

    assert resp.status_code == 502
    assert "체결" in resp.json()["detail"]

    fake = _FakeKiwoomClient.instances[-1]
    assert fake.sell_calls == [("0167A0", 1, 16050)]
    assert fake.cancel_calls == [("0167A0", "0099002", 1)]

    state = await _get_state()
    assert state.status == "holding"  # idle로 안 넘어감 — 핵심 단언
    assert float(state.entry_price) == pytest.approx(16000.0)

    logs = await _log_rows_since(start_max_log_id)
    assert len(logs) == 1
    assert logs[0].event_type == "sell_unconfirmed"


# ---------------------------------------------------------------------------
# _position_lock을 실제로 잡는지 검증.
#
# **시도했다가 되돌린 접근**: 처음에는 `test_auto_trader_collector.py::
# test_watch_and_run_auto_trade_do_not_double_sell_concurrently`처럼 두 개의
# 실제 동시 세션(`asyncio.gather`)이 같은 `_position_lock`을 놓고 실제로
# 경합하게 만들어 "중복 주문 없음"까지 함께 검증하려 했다. 이 프로세스에서는
# 그 방식이 이 모듈 레벨 `asyncio.Lock`에 실제 대기자(waiter)를 만들었고,
# 그 부작용(sqlalchemy 엔진 커넥션 풀 상태 오염, `IllegalStateChangeError`)이
# 같은 pytest 프로세스에서 나중에 도는 위 collector 테스트(동일한 락으로
# 동일한 패턴을 검증하는 기존 테스트)를 깨뜨리는 것을 재현 확인했다 — 즉
# 두 테스트가 같은 전역 락 객체에 대해 각자 독립적으로 경합을 만드는 것
# 자체가 이 pytest-asyncio 환경에서 안전하지 않았다. 실거래 코드가 걸린
# 테스트 스위트 전체의 안정성이 이 하나의 "있으면 좋은" 동시성 테스트보다
# 중요하므로, 실제 경합 대신 아래처럼 락의 `acquire`를 스파이(spy)해서
# "이 엔드포인트가 이 락을 잡는다"는 사실만 경합 없이 검증한다. 중복 주문
# 방지 자체는 이미 `_execute_sell`/`_position_lock`을 통째로 재사용하는
# 설계(재구현 없음)로 collector 테스트가 이미 그 락의 직렬화 동작을
# 검증해 두었다 — 이 라우터가 같은 락 인스턴스를 잡는지만 확인하면 충분하다.
# ---------------------------------------------------------------------------


def _spy_lock_acquire(monkeypatch) -> list[int]:
    """`_position_lock.acquire`가 실제로 호출되는지 카운트한다(경합 없이,
    원래 동작은 그대로 위임) — 새 waiter를 만들지 않으므로 다른 테스트의
    락 상태에 영향을 주지 않는다."""
    calls: list[int] = []
    orig_acquire = auto_trader_module._position_lock.acquire

    async def spy_acquire():
        calls.append(1)
        return await orig_acquire()

    monkeypatch.setattr(auto_trader_module._position_lock, "acquire", spy_acquire)
    return calls


async def test_manual_sell_acquires_position_lock(monkeypatch):
    await _set_state(enabled=True, status="holding", entry_price=16000, entry_qty=1)

    async def fake_intraday(code, interval):
        return {"bars": [{"close": 16000.0}]}

    monkeypatch.setattr(auto_trade, "_warm_stock_intraday", fake_intraday)
    _patch_kiwoom_client(monkeypatch)
    lock_calls = _spy_lock_acquire(monkeypatch)

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auto-trade/manual-sell")

    assert resp.status_code == 200
    assert lock_calls == [1]  # 이 엔드포인트가 collectors/auto_trader._position_lock을 잡았다


async def test_manual_buy_acquires_position_lock(monkeypatch):
    await _set_state(enabled=True, status="idle")

    async def fake_intraday(code, interval):
        return {"bars": [{"close": 16000.0}]}

    monkeypatch.setattr(auto_trade, "_warm_stock_intraday", fake_intraday)
    _patch_kiwoom_client(monkeypatch)
    lock_calls = _spy_lock_acquire(monkeypatch)

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auto-trade/manual-buy")

    assert resp.status_code == 200
    assert lock_calls == [1]  # 이 엔드포인트가 collectors/auto_trader._position_lock을 잡았다
