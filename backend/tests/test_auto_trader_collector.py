"""Integration tests for app.collectors.auto_trader (PLAN.md §5.54) — 완전자동매매
실행 엔진(0167A0 트레일링 스탑). 이 프로젝트 최초로 실제 돈이 움직이는 엔진이라
**가장 중요한 테스트는 킬스위치가 꺼져 있을 때 어떤 외부 호출도 일어나지 않는지**
확인하는 것이다(아래 `test_disabled_*`).

Same house pattern as tests/test_positioning_snapshot.py: real dev Postgres via
app.db.async_session_factory. `AutoTradeState`는 **싱글턴(id=1 고정)**이라 다른
테이블들처럼 격리된 테스트 행을 새로 만들 수 없다 — 대신 테스트 시작 전 현재
행 상태를 스냅샷해 두고, 테스트가 끝나면 반드시 원상 복구한다(autouse fixture)
— 개발 DB에 실제로 배포된 엔진의 상태를 테스트가 훼손하면 안 되기 때문이다.

외부 호출(`_warm_stock_intraday`, `moving_average_cross`, `volume_spike`,
`KiwoomClient`)은 전부 `app.collectors.auto_trader`에 이름으로 임포트돼 있으므로
(`tests/test_paper_trades_router.py`와 동일한 관례) 그 이름을 몽키패치한다 —
실제 키움/네트워크를 절대 건드리지 않는다.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.collectors import auto_trader
from app.db import async_session_factory, engine
from app.models import AutoTradeLog, AutoTradeState

STATE_ID = auto_trader.STATE_ID

FAKE_QUOTE_RAW = {
    "sel_fpr_bid": "-16100",  # 매도 1호가 -> 매수 주문가로 쓰임
    "sel_fpr_req": "100",
    "buy_fpr_bid": "-16050",  # 매수 1호가 -> 매도 주문가로 쓰임
    "buy_fpr_req": "100",
    "tot_sel_req": "500",
    "tot_buy_req": "500",
    "bid_req_base_tm": "091500",
}


class _FakeKiwoomClient:
    """`async with KiwoomClient() as client:` 관례를 흉내내는 테스트 더블.
    place_buy_order/place_sell_order 호출 여부·인자를 기록해 검증한다."""

    instances: list["_FakeKiwoomClient"] = []

    def __init__(self, *args, **kwargs):
        self.buy_calls: list[tuple] = []
        self.sell_calls: list[tuple] = []
        self.quote_data = dict(FAKE_QUOTE_RAW)
        self.buy_response = {"ord_no": "0099001", "return_code": 0}
        self.sell_response = {"ord_no": "0099002", "return_code": 0}
        self.buy_error: Exception | None = None
        self.sell_error: Exception | None = None
        _FakeKiwoomClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def stock_quote(self, code):
        return dict(self.quote_data)

    async def place_buy_order(self, code, qty, price):
        self.buy_calls.append((code, qty, price))
        if self.buy_error is not None:
            raise self.buy_error
        return self.buy_response

    async def place_sell_order(self, code, qty, price):
        self.sell_calls.append((code, qty, price))
        if self.sell_error is not None:
            raise self.sell_error
        return self.sell_response


def _raising_kiwoom_client_factory(*args, **kwargs):  # pragma: no cover - 호출되면 안 됨
    raise AssertionError(
        "KiwoomClient()가 호출됐다 — 킬스위치가 꺼져 있으면 이 인스턴스화 자체가 절대 "
        "일어나면 안 된다(실제 주문 API 호출로 이어질 수 있는 경로)"
    )


async def _raising_warm_intraday(code, interval):  # pragma: no cover - 호출되면 안 됨
    raise AssertionError(
        "_warm_stock_intraday가 호출됐다 — 킬스위치가 꺼져 있으면 신호 평가 자체가 "
        "일어나면 안 된다(PLAN.md §5.54 절대 원칙 1)"
    )


async def _current_max_log_id() -> int:
    async with async_session_factory() as session:
        result = await session.execute(select(func.max(AutoTradeLog.id)))
        return result.scalar() or 0


@pytest.fixture(autouse=True)
async def _snapshot_and_restore_state():
    """싱글턴 AutoTradeState(id=1) 행을 스냅샷/복원한다 — 이 테이블은 실제
    배포된 엔진이 참조하는 단일 행이라, 테스트가 값을 바꿔도 반드시 원래
    값으로 되돌려야 한다."""
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
        )
        defaults.update(kwargs)
        for k, v in defaults.items():
            setattr(row, k, v)
        await session.commit()


async def _get_state() -> AutoTradeState:
    async with async_session_factory() as session:
        return await session.get(AutoTradeState, STATE_ID)


async def _log_rows() -> list[AutoTradeLog]:
    async with async_session_factory() as session:
        result = await session.execute(select(AutoTradeLog).order_by(AutoTradeLog.id))
        return list(result.scalars().all())


def _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=16000.0):
    async def fake_warm_intraday(code, interval):
        assert code == "0167A0"
        assert interval == 1
        return {"bars": [{"close": bars_close, "volume": 1000}]}

    def fake_ma_cross(bars, *args, **kwargs):
        return {"state": ma_state, "short_ma": None, "long_ma": None}

    def fake_volume_spike(bars, *args, **kwargs):
        return {"zscore": None, "is_spike": is_spike, "ratio": None}

    monkeypatch.setattr(auto_trader, "_warm_stock_intraday", fake_warm_intraday)
    monkeypatch.setattr(auto_trader, "moving_average_cross", fake_ma_cross)
    monkeypatch.setattr(auto_trader, "volume_spike", fake_volume_spike)


def _patch_kiwoom_client(monkeypatch) -> _FakeKiwoomClient:
    _FakeKiwoomClient.instances = []
    monkeypatch.setattr(auto_trader, "KiwoomClient", _FakeKiwoomClient)
    return _FakeKiwoomClient


# ---------------------------------------------------------------------------
# 킬스위치 꺼짐 — 가장 중요한 테스트: 어떤 외부 호출도 절대 일어나면 안 된다
# ---------------------------------------------------------------------------


async def test_disabled_makes_no_external_calls_and_returns_immediately(monkeypatch):
    await _set_state(enabled=False, status="idle")
    monkeypatch.setattr(auto_trader, "_warm_stock_intraday", _raising_warm_intraday)
    monkeypatch.setattr(auto_trader, "KiwoomClient", _raising_kiwoom_client_factory)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session)

    assert result == {"enabled": False, "action": "none"}
    assert await _log_rows() == []


async def test_disabled_even_while_holding_a_position_makes_no_calls(monkeypatch):
    """킬스위치가 꺼져 있으면 이미 포지션을 들고 있어도(holding) 손절/트레일
    판정조차 하지 않는다 — 꺼짐은 절대적이다."""
    await _set_state(enabled=False, status="holding", entry_price=16000, entry_qty=1)
    monkeypatch.setattr(auto_trader, "_warm_stock_intraday", _raising_warm_intraday)
    monkeypatch.setattr(auto_trader, "KiwoomClient", _raising_kiwoom_client_factory)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session)

    assert result["enabled"] is False
    assert await _log_rows() == []
    state = await _get_state()
    assert state.status == "holding"  # 건드리지 않음


async def test_missing_state_row_treated_as_disabled(monkeypatch):
    async with async_session_factory() as session:
        row = await session.get(AutoTradeState, STATE_ID)
        if row is not None:
            await session.delete(row)
            await session.commit()

    monkeypatch.setattr(auto_trader, "_warm_stock_intraday", _raising_warm_intraday)
    monkeypatch.setattr(auto_trader, "KiwoomClient", _raising_kiwoom_client_factory)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session)

    assert result == {"enabled": False, "action": "none"}


# ---------------------------------------------------------------------------
# idle — 진입 조건 미충족/충족, 예산 가드
# ---------------------------------------------------------------------------


async def test_idle_no_entry_when_conditions_unmet(monkeypatch):
    await _set_state(enabled=True, status="idle")
    _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=16000.0)
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session)

    assert result["action"] == "none"
    assert fake_cls.instances == []  # 주문 시도 자체가 없어야 함
    assert await _log_rows() == []  # 노이즈 방지 — idle+미충족은 로그 안 함
    state = await _get_state()
    assert state.status == "idle"


async def test_idle_enters_when_conditions_met_and_budget_ok(monkeypatch):
    await _set_state(enabled=True, status="idle")
    _patch_signals(monkeypatch, ma_state="golden", is_spike=True, bars_close=16000.0)
    _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session)

    assert result["action"] == "entry"
    fake = _FakeKiwoomClient.instances[-1]
    assert fake.buy_calls == [("0167A0", 1, 16100)]  # 매도1호가로 지정가 매수
    assert fake.sell_calls == []

    state = await _get_state()
    assert state.status == "holding"
    assert float(state.entry_price) == pytest.approx(16100.0)
    assert state.entry_qty == 1
    assert state.entry_order_no == "0099001"
    assert state.peak_price is None

    logs = await _log_rows()
    assert len(logs) == 1
    assert logs[0].event_type == "entry"
    assert "golden" in logs[0].reason


async def test_idle_blocked_by_budget_guard_when_notional_exceeds_total_budget(monkeypatch):
    """AUTO_TRADE_TOTAL_BUDGET_KRW(25,000원)를 넘는 가격이면 진입 조건이
    충족돼도 매수를 절대 시도하지 않는다 — §5.54-2 완료 기준의 핵심 케이스."""
    await _set_state(enabled=True, status="idle")
    high_price = auto_trader.AUTO_TRADE_TOTAL_BUDGET_KRW + 5000
    _patch_signals(monkeypatch, ma_state="golden", is_spike=True, bars_close=float(high_price))
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session)

    assert result["action"] == "budget_blocked"
    assert fake_cls.instances == []  # place_buy_order로 이어지는 클라이언트 호출 자체가 없어야 함

    state = await _get_state()
    assert state.status == "idle"

    logs = await _log_rows()
    assert len(logs) == 1
    assert logs[0].event_type == "error"


async def test_idle_already_holding_never_reaches_entry_logic(monkeypatch):
    """상태가 idle이 아니면애초에 진입 분기 자체를 타지 않는다는 것을
    run_auto_trade 레벨에서도 확인(quant/auto_trade_rules 단위테스트와 별개로
    통합 경로에서도 보장)."""
    await _set_state(enabled=True, status="holding", entry_price=16000, entry_qty=1, peak_price=None)
    _patch_signals(monkeypatch, ma_state="golden", is_spike=True, bars_close=16000.0)
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session)

    # holding 상태이므로 idle 진입 로직이 아니라 포지션 관리 로직(손절/트레일)이 평가된다.
    assert result["action"] != "entry"
    assert fake_cls.instances == [] or all(c.buy_calls == [] for c in fake_cls.instances)


# ---------------------------------------------------------------------------
# holding — 손절, 트레일 전환
# ---------------------------------------------------------------------------


async def test_holding_stop_loss_sells_and_resets_to_idle(monkeypatch):
    await _set_state(enabled=True, status="holding", entry_price=16000, entry_qty=1)
    _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=16000 * 0.98)
    _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session)

    assert result["action"] == "stop_loss"
    fake = _FakeKiwoomClient.instances[-1]
    assert fake.sell_calls == [("0167A0", 1, 16050)]  # 매수1호가로 지정가 매도

    state = await _get_state()
    assert state.status == "idle"
    assert state.entry_price is None
    assert state.entry_qty is None
    assert state.peak_price is None

    logs = await _log_rows()
    assert len(logs) == 1
    assert logs[0].event_type == "exit_stop_loss"


async def test_holding_trail_activate_no_order_placed(monkeypatch):
    await _set_state(enabled=True, status="holding", entry_price=16000, entry_qty=1)
    current = 16000 * 1.02
    _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=current)
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session)

    assert result["action"] == "trail_activate"
    assert fake_cls.instances == []  # 트레일 전환은 주문이 아니다

    state = await _get_state()
    assert state.status == "trailing"
    assert float(state.peak_price) == pytest.approx(current)

    logs = await _log_rows()
    assert len(logs) == 1
    assert logs[0].event_type == "trail_activate"


async def test_holding_no_action_when_no_condition_met(monkeypatch):
    await _set_state(enabled=True, status="holding", entry_price=16000, entry_qty=1)
    _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=16000 * 1.002)
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session)

    assert result["action"] == "none"
    assert fake_cls.instances == []
    assert await _log_rows() == []


# ---------------------------------------------------------------------------
# trailing — 신고가 갱신(무음), 청산
# ---------------------------------------------------------------------------


async def test_trailing_peak_update_is_silent_no_log(monkeypatch):
    await _set_state(enabled=True, status="trailing", entry_price=16000, entry_qty=1, peak_price=16200)
    new_high = 16300.0
    _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=new_high)
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session)

    assert result["action"] == "peak_update"
    assert fake_cls.instances == []

    state = await _get_state()
    assert state.status == "trailing"
    assert float(state.peak_price) == pytest.approx(new_high)
    assert await _log_rows() == []  # 신고가 갱신은 로그하지 않는다


async def test_trailing_exit_when_dead_cross_and_floor_reached(monkeypatch):
    await _set_state(enabled=True, status="trailing", entry_price=16000, entry_qty=1, peak_price=16500)
    floor_price = 16000 * 1.003
    _patch_signals(monkeypatch, ma_state="dead", is_spike=False, bars_close=floor_price)
    _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session)

    assert result["action"] == "exit_trail"
    fake = _FakeKiwoomClient.instances[-1]
    assert fake.sell_calls == [("0167A0", 1, 16050)]

    state = await _get_state()
    assert state.status == "idle"
    assert state.entry_price is None
    assert state.peak_price is None

    logs = await _log_rows()
    assert len(logs) == 1
    assert logs[0].event_type == "exit_trail"


async def test_trailing_stop_loss_overrides_trail_logic(monkeypatch):
    await _set_state(enabled=True, status="trailing", entry_price=16000, entry_qty=1, peak_price=16500)
    _patch_signals(monkeypatch, ma_state="dead", is_spike=False, bars_close=16000 * 0.98)
    _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session)

    assert result["action"] == "stop_loss"
    logs = await _log_rows()
    assert logs[0].event_type == "exit_stop_loss"


# ---------------------------------------------------------------------------
# 주문 실패 — 재시도 루프에 빠지지 않고 상태를 잘못 갱신하지 않는다
# ---------------------------------------------------------------------------


async def test_buy_order_failure_keeps_state_idle(monkeypatch):
    await _set_state(enabled=True, status="idle")
    _patch_signals(monkeypatch, ma_state="golden", is_spike=True, bars_close=16000.0)
    _patch_kiwoom_client(monkeypatch)

    # 인스턴스 생성 시점에 에러를 미리 설정할 수 없으므로, place_buy_order가 항상
    # 실패하도록 클래스를 하나 더 감싼다.
    class _FailingBuyClient(_FakeKiwoomClient):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.buy_error = RuntimeError("kiwoom 500")

    monkeypatch.setattr(auto_trader, "KiwoomClient", _FailingBuyClient)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session)

    assert result["action"] == "buy_failed"
    state = await _get_state()
    assert state.status == "idle"
    assert state.entry_price is None

    logs = await _log_rows()
    assert len(logs) == 1
    assert logs[0].event_type == "error"


async def test_sell_order_failure_keeps_state_holding(monkeypatch):
    await _set_state(enabled=True, status="holding", entry_price=16000, entry_qty=1)
    _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=16000 * 0.98)

    class _FailingSellClient(_FakeKiwoomClient):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.sell_error = RuntimeError("kiwoom 500")

    monkeypatch.setattr(auto_trader, "KiwoomClient", _FailingSellClient)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session)

    assert result["action"] == "sell_failed"
    state = await _get_state()
    assert state.status == "holding"  # 잘못된 상태로 갱신되지 않음
    assert float(state.entry_price) == pytest.approx(16000.0)

    logs = await _log_rows()
    assert len(logs) == 1
    assert logs[0].event_type == "error"
