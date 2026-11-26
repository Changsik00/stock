"""Phase 5.122 — 자동매매 수량: 1주 고정 -> 5만원 한도 내 최대 수량 + 부분 체결 처리.

안전: `AutoTradeState` id=1(실거래 행)·실 `auto_trade_log`에는 일절 닿지 않는다. 상태/세션은
메모리 객체(SimpleNamespace)로 두고, 가짜로 바꾸는 건 외부 소스(키움 클라이언트·호가·시계 대기)와
로그 기록(`_log`)뿐이다. 판정·정산 함수(`_handle_idle`/`_execute_sell`/`_settle_*`/
`compute_order_qty`/`classify_*`)는 실제 코드를 그대로 돌린다.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from app.clients import kiwoom as kiwoom_mod
from app.clients.kiwoom import (
    MAX_ORDER_NOTIONAL_KRW,
    SELL_MAX_ORDER_NOTIONAL_KRW,
    KiwoomClient,
)
from app.collectors import auto_trader
from app.quant import auto_trade_rules as rules

# ---------------- 순수 함수: 수량 산식 ----------------


@pytest.mark.parametrize(
    "price,cash,expected",
    [
        (10_680, 60_000, 4),  # min(5만,5만,6만)=5만 / 10,680 = 4.68 -> 4
        (10_680, 23_680, 2),  # 주문가능금액이 상한 -> 2
        (10_680, None, 0),  # 조회 실패 -> 보수적으로 차단
        (60_000, 100_000, 0),  # 1주도 5만원 초과 -> 0
        (0, 100_000, 0),
        (-5, 100_000, 0),
        (None, 100_000, 0),
        (10_680, 0, 0),
        (10_680, 10_679, 0),  # 1주도 못 사는 현금
        (10_680, 10_680, 1),  # 경계: 정확히 1주
    ],
)
def test_compute_order_qty(price, cash, expected):
    assert rules.compute_order_qty(price, 50_000, 50_000, cash) == expected


def test_compute_order_qty_zero_limits_block():
    assert rules.compute_order_qty(10_000, 0, 50_000, 100_000) == 0
    assert rules.compute_order_qty(10_000, 50_000, 0, 100_000) == 0


def test_compute_order_qty_budget_smaller_than_cap():
    assert rules.compute_order_qty(10_000, 50_000, 25_000, 100_000) == 2


def test_total_budget_is_50k_now():
    assert auto_trader.AUTO_TRADE_TOTAL_BUDGET_KRW == 50_000


@pytest.mark.parametrize(
    "ordered,actual,kind",
    [(4, 4, "full"), (4, 2, "partial"), (4, 1, "partial"), (4, 0, "unconfirmed"), (4, None, "unconfirmed"), (4, 5, "unconfirmed")],
)
def test_classify_buy_fill(ordered, actual, kind):
    assert rules.classify_buy_fill(ordered, actual) == kind


@pytest.mark.parametrize(
    "held,remaining,kind",
    [(4, 0, "full"), (4, 3, "partial"), (4, 4, "unconfirmed"), (4, None, "unconfirmed"), (4, 5, "unconfirmed")],
)
def test_classify_sell_fill(held, remaining, kind):
    assert rules.classify_sell_fill(held, remaining) == kind


# ---------------- 키움 클라이언트: 매도 캡 / 주문가능금액 (call_tr만 가짜, HTTP 없음) ----------------


def _client_with_fake_call_tr(responses=None):
    client = object.__new__(KiwoomClient)
    calls: list[tuple] = []

    async def call_tr(api_id, body, **kw):
        calls.append((api_id, body))
        return (responses or {}), {}

    client.call_tr = call_tr  # type: ignore[method-assign]
    return client, calls


async def test_sell_cap_allows_up_to_double_buy_cap():
    assert SELL_MAX_ORDER_NOTIONAL_KRW == 2 * MAX_ORDER_NOTIONAL_KRW == 100_000
    client, calls = _client_with_fake_call_tr({"ord_no": "1"})
    await client.place_sell_order("0167A0", 4, 13_000)  # 52,000: 매수 캡은 넘지만 매도 캡 이내
    assert calls[0][0] == "kt10001"
    await client.place_sell_order("0167A0", 1, 100_000)  # 정확히 경계


async def test_sell_cap_over_double_raises_without_call():
    client, calls = _client_with_fake_call_tr()
    with pytest.raises(ValueError):
        await client.place_sell_order("0167A0", 1, 100_001)
    assert calls == []


async def test_buy_cap_unchanged_52k_raises():
    client, calls = _client_with_fake_call_tr()
    with pytest.raises(ValueError):
        await client.place_buy_order("0167A0", 4, 13_000)  # 52,000 > 50,000
    assert calls == []


async def test_get_orderable_cash_uses_qry_tp_and_100stk_field():
    client, calls = _client_with_fake_call_tr(
        {"100stk_ord_alow_amt": "000000000023680", "ord_alow_amt": "000000000012620"}
    )
    assert await client.get_orderable_cash() == 23_680
    assert calls == [("kt00001", {"qry_tp": "3"})]


async def test_get_orderable_cash_missing_field_raises():
    client, _ = _client_with_fake_call_tr({"ord_alow_amt": "1"})
    with pytest.raises(ValueError):
        await client.get_orderable_cash()


# ---------------- 가짜 키움(외부 소스) + 메모리 상태 ----------------


class FakeKiwoom:
    """체결 시나리오를 `holdings_after_buy`/`holdings_after_sell`로 지정한다(주문 직후 실보유수량).
    취소 후 추가 체결을 흉내내려면 `holdings_after_cancel`."""

    instances: list["FakeKiwoom"] = []
    cash: int | None = 100_000
    holdings_after_buy: int | None = None  # None이면 전량 체결(주문 수량 그대로)
    holdings_after_sell: int | None = 0
    holdings_after_cancel: int | None = None
    holdings_error = False

    def __init__(self):
        self.buys: list[tuple] = []
        self.sells: list[tuple] = []
        self.cancels: list[tuple] = []
        self._holdings = 0
        FakeKiwoom.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get_orderable_cash(self):
        if FakeKiwoom.cash is None:
            raise RuntimeError("주문가능금액 조회 실패")
        return FakeKiwoom.cash

    async def place_buy_order(self, code, qty, price):
        self.buys.append((code, qty, price))
        b = FakeKiwoom.holdings_after_buy
        self._holdings = qty if b is None else b
        return {"ord_no": "B1", "return_code": 0}

    async def place_sell_order(self, code, qty, price):
        self.sells.append((code, qty, price))
        self._holdings = FakeKiwoom.holdings_after_sell
        return {"ord_no": "S1", "return_code": 0}

    async def cancel_order(self, code, order_no, qty):
        self.cancels.append((code, order_no, qty))
        if FakeKiwoom.holdings_after_cancel is not None:
            self._holdings = FakeKiwoom.holdings_after_cancel
        return {"return_code": 0}

    async def get_account_holdings_qty(self, code):
        if FakeKiwoom.holdings_error:
            raise RuntimeError("kt00018 실패")
        return self._holdings


class FakeSession:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        return None


def make_state(**kw):
    base = dict(
        enabled=True, status="idle", code="0167A0", entry_price=None, entry_qty=None, entry_at=None,
        entry_order_no=None, peak_price=None, entry_foreign_flow_sign=None, entry_source=None,
        manual_exit_rules=None, updated_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def env(monkeypatch):
    FakeKiwoom.instances = []
    FakeKiwoom.cash = 100_000
    FakeKiwoom.holdings_after_buy = None
    FakeKiwoom.holdings_after_sell = 0
    FakeKiwoom.holdings_after_cancel = None
    FakeKiwoom.holdings_error = False
    logs: list[dict] = []
    price = {"buy": 10_680, "sell": 10_600}

    async def best(client, code, side):
        return price["buy"] if side == "buy" else price["sell"]

    async def fake_log(session, **kw):
        logs.append(kw)

    async def series(session, days=1):
        return {"spot": [{"value": 5.0}]}

    monkeypatch.setattr(auto_trader, "KiwoomClient", FakeKiwoom)
    monkeypatch.setattr(auto_trader, "_best_fill_price", best)
    monkeypatch.setattr(auto_trader, "_log", fake_log)
    monkeypatch.setattr(auto_trader, "_CONFIRM_HOLDINGS_RETRY_DELAYS_SEC", ())  # 실제 sleep 없음
    monkeypatch.setattr(auto_trader.intraday_snapshot, "get_foreign_position_series", series)
    return SimpleNamespace(logs=logs, price=price)


async def _enter(state, session=None):
    return await auto_trader._handle_idle(
        session or FakeSession(), state, "0167A0", 10_680.0, True, True,
        {"src": "test"}, {"action": "idle"}, dt.time(10, 0), False,
    )


# ---------------- 매수: 수량 산출 / 부분 체결 ----------------


async def test_buy_full_fill_uses_max_qty_by_cash(env):
    state = make_state()
    FakeKiwoom.cash = 60_000
    res = await _enter(state)
    fake = FakeKiwoom.instances[-1]
    assert fake.buys == [("0167A0", 4, 10_680)]  # floor(50,000 / 10,680) = 4
    assert res["action"] == "entry"
    assert state.status == "holding" and state.entry_qty == 4
    entry = [lg for lg in env.logs if lg["event_type"] == "entry"][0]
    assert entry["signal_snapshot"]["qty"] == 4 and "4주" in entry["reason"]


async def test_buy_limited_by_orderable_cash(env):
    state = make_state()
    FakeKiwoom.cash = 23_680
    await _enter(state)
    assert FakeKiwoom.instances[-1].buys == [("0167A0", 2, 10_680)]
    assert state.entry_qty == 2


async def test_buy_blocked_when_cash_unknown(env):
    state = make_state()
    FakeKiwoom.cash = None
    res = await _enter(state)
    assert res["action"] == "budget_blocked"
    assert FakeKiwoom.instances[-1].buys == []
    assert state.status == "idle" and state.entry_qty is None
    assert env.logs[-1]["event_type"] == "error" and "주문가능금액 조회 실패" in env.logs[-1]["reason"]


async def test_buy_blocked_when_price_above_cap(env):
    state = make_state()
    env.price["buy"] = 60_000
    res = await _enter(state)
    assert res["action"] == "budget_blocked"
    assert FakeKiwoom.instances[-1].buys == []
    assert "60000" in env.logs[-1]["reason"]


async def test_buy_partial_fill_cancels_remainder_and_holds_actual(env):
    """4주 주문 중 2주만 체결 -> 잔량 2주 취소, entry_qty=2로 holding (수정 전엔 unconfirmed/idle로
    실보유 2주가 손절 감시 밖에 남았다)."""
    state = make_state()
    FakeKiwoom.cash = 60_000
    FakeKiwoom.holdings_after_buy = 2
    res = await _enter(state)
    fake = FakeKiwoom.instances[-1]
    assert fake.buys[0][1] == 4
    assert fake.cancels == [("0167A0", "B1", 2)]  # 미체결 잔량만 취소
    assert res["action"] == "entry"
    assert state.status == "holding" and state.entry_qty == 2
    entry = [lg for lg in env.logs if lg["event_type"] == "entry"][0]
    assert "부분 체결 2/4주" in entry["reason"] and entry["signal_snapshot"]["qty"] == 2


async def test_buy_partial_then_more_fill_during_cancel(env):
    state = make_state()
    FakeKiwoom.cash = 60_000
    FakeKiwoom.holdings_after_buy = 1
    FakeKiwoom.holdings_after_cancel = 3  # 취소 직전 추가 체결 -> 재조회로 3주 확정
    await _enter(state)
    assert state.status == "holding" and state.entry_qty == 3


async def test_buy_zero_fill_stays_idle_and_cancels_all(env):
    state = make_state()
    FakeKiwoom.cash = 60_000
    FakeKiwoom.holdings_after_buy = 0
    res = await _enter(state)
    assert res["action"] == "buy_unconfirmed"
    assert FakeKiwoom.instances[-1].cancels == [("0167A0", "B1", 4)]
    assert state.status == "idle" and state.entry_qty is None


async def test_buy_unreadable_holdings_stays_idle(env):
    state = make_state()
    FakeKiwoom.holdings_error = True
    res = await _enter(state)
    assert res["action"] == "buy_unconfirmed"
    assert state.status == "idle"


# ---------------- 매도: 수량 = entry_qty / 부분 체결 ----------------


def _holding(qty=4):
    return make_state(status="holding", entry_price=10_680, entry_qty=qty, entry_order_no="B1")


async def _sell(state):
    return await auto_trader._execute_sell(
        FakeSession(), state, "0167A0", 10_600.0, "exit_stop_loss", "손절", {"s": 1}, {"action": "stop_loss"}
    )


async def test_sell_orders_entry_qty_and_resets_on_full_fill(env):
    state = _holding(4)
    await _sell(state)
    fake = FakeKiwoom.instances[-1]
    assert fake.sells == [("0167A0", 4, 10_600)]
    assert state.status == "idle" and state.entry_qty is None
    exit_log = [lg for lg in env.logs if lg["event_type"] == "exit_stop_loss"][0]
    assert exit_log["signal_snapshot"]["qty"] == 4


async def test_sell_partial_fill_keeps_holding_with_remaining_qty(env):
    """4주 매도 중 3주 체결(잔량 1) -> entry_qty=1·holding 유지, 미체결 1주 취소. 수정 전엔
    entry_qty=4가 남아 다음 매도가 수량 초과로 계속 거부됐다."""
    state = _holding(4)
    FakeKiwoom.holdings_after_sell = 1
    res = await _sell(state)
    fake = FakeKiwoom.instances[-1]
    assert fake.cancels == [("0167A0", "S1", 1)]
    assert res["action"] == "sell_partial"
    assert state.status == "holding" and state.entry_qty == 1
    lg = env.logs[-1]
    assert lg["event_type"] == "sell_unconfirmed" and "잔량 1주 유지" in lg["reason"]
    assert lg["signal_snapshot"]["qty"] == 3 and lg["signal_snapshot"]["remaining_qty"] == 1


async def test_sell_partial_then_next_poll_sells_remainder(env):
    state = _holding(4)
    FakeKiwoom.holdings_after_sell = 1
    await _sell(state)
    FakeKiwoom.holdings_after_sell = 0
    await _sell(state)
    sells = [s for f in FakeKiwoom.instances for s in f.sells]
    assert sells == [("0167A0", 4, 10_600), ("0167A0", 1, 10_600)]
    assert state.status == "idle"


async def test_sell_zero_fill_keeps_position_unchanged(env):
    state = _holding(4)
    FakeKiwoom.holdings_after_sell = 4
    res = await _sell(state)
    assert res["action"] == "sell_unconfirmed"
    assert FakeKiwoom.instances[-1].cancels == [("0167A0", "S1", 4)]
    assert state.status == "holding" and state.entry_qty == 4


async def test_sell_unreadable_holdings_keeps_position(env):
    state = _holding(4)
    FakeKiwoom.holdings_error = True
    res = await _sell(state)
    assert res["action"] == "sell_unconfirmed"
    assert state.status == "holding" and state.entry_qty == 4


# ---------------- 30초 손절 감시 경로 ----------------


async def test_watch_stop_loss_sells_entry_qty_and_handles_partial(env, monkeypatch):
    state = _holding(3)
    session = FakeSession()

    async def get_state(s):
        return state

    async def risk(s):
        return False

    monkeypatch.setattr(auto_trader, "_get_state", get_state)
    monkeypatch.setattr(auto_trader, "_get_risk_alert_active", risk)
    env.price["sell"] = 10_000  # 진입가 10,680 대비 -6.4% -> 손절
    FakeKiwoom.holdings_after_sell = 1
    res = await auto_trader.watch_stop_loss(session)
    sells = [s for f in FakeKiwoom.instances for s in f.sells]
    assert sells == [("0167A0", 3, 10_000)]
    assert res["action"] == "sell_partial"
    assert state.status == "holding" and state.entry_qty == 1


# ---------------- 수동 매수 라우터(가짜 세션, 실DB 미접촉) ----------------


class _RouterSession(FakeSession):
    def __init__(self, state):
        super().__init__()
        self.state = state

    async def get(self, model, pk):
        return self.state


@pytest.fixture
def manual_api(env, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app.db import get_session
    from app.main import app
    from app.routers import auto_trade as router_mod

    state = make_state()

    async def override():
        yield _RouterSession(state)

    async def intraday(code, interval):
        return {"bars": [{"close": 10_680}]}

    async def series(session, days=1):
        return {"spot": [{"value": 5.0}]}

    async def fake_log(session, **kw):
        env.logs.append(kw)

    app.dependency_overrides[get_session] = override
    monkeypatch.setattr(router_mod, "_warm_stock_intraday", intraday)
    monkeypatch.setattr(router_mod, "KiwoomClient", FakeKiwoom)
    monkeypatch.setattr(router_mod, "_best_fill_price", auto_trader._best_fill_price)
    monkeypatch.setattr(router_mod, "_log", fake_log)
    monkeypatch.setattr(router_mod.intraday_snapshot, "get_foreign_position_series", series)
    yield SimpleNamespace(state=state, http=lambda: AsyncClient(transport=ASGITransport(app=app), base_url="http://t"))
    app.dependency_overrides.pop(get_session, None)


async def test_manual_buy_partial_fill_holds_actual_qty(manual_api, env):
    FakeKiwoom.cash = 60_000
    FakeKiwoom.holdings_after_buy = 3
    async with manual_api.http() as c:
        r = await c.post("/api/auto-trade/manual-buy", json={})
    assert r.status_code == 200, r.text
    fake = FakeKiwoom.instances[-1]
    assert fake.buys == [("0167A0", 4, 10_680)] and fake.cancels == [("0167A0", "B1", 1)]
    assert manual_api.state.status == "holding" and manual_api.state.entry_qty == 3
    assert r.json()["entry_qty"] == 3
    assert "부분 체결 3/4주" in env.logs[-1]["reason"]


async def test_manual_buy_zero_qty_returns_400_without_order(manual_api, env):
    FakeKiwoom.cash = 5_000
    async with manual_api.http() as c:
        r = await c.post("/api/auto-trade/manual-buy", json={})
    assert r.status_code == 400 and "주문 수량 0주" in r.json()["detail"]
    assert FakeKiwoom.instances[-1].buys == []
    assert manual_api.state.status == "idle"
