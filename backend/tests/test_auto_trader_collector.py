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

import asyncio
import datetime as dt

import pytest
from sqlalchemy import func, select

from app.collectors import auto_trader
from app.db import async_session_factory, engine
from app.models import AutoTradeLog, AutoTradeState

STATE_ID = auto_trader.STATE_ID

# PLAN.md §5.55(2026-08-06) 배선 이후 run_auto_trade가 진입 시간 필터(§5.55-1,
# 09:00~09:10/15:20~15:30 KST 진입 금지)와 EOD 강제청산(§5.55-2, 15:20~15:30
# KST) 판정에 "지금 몇 시인지"를 쓴다. §5.55와 무관한 기존 테스트가 실행되는
# 실제 벽시계 시각에 따라 우연히 이 창에 걸려 플레이키해지지 않도록, 그 두
# 창 밖의 고정 시각을 기본으로 넘긴다(house rule — 시간 관련 테스트는 now_kst
# 주입으로 실제 시계에 의존하지 않게 설계). §5.55 전용 테스트는 각자 원하는
# 시각을 명시적으로 넘긴다.
SAFE_NOW_KST = dt.time(10, 0)

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
    place_buy_order/place_sell_order 호출 여부·인자를 기록해 검증한다.

    PLAN.md §5.71 — `get_unfilled_orders`/`cancel_order` 추가. 기본값
    `unfilled_orders=[]`(방금 낸 주문이 미체결 목록에 전혀 없음)은 "항상
    즉시 체결됨"을 뜻해 기존(§5.71 이전) 테스트들의 가정과 동일하다 — 그
    테스트들은 이 두 메서드가 생기기 전부터 통과하던 그대로 수정 없이
    계속 통과해야 한다. 미체결을 흉내내려면 테스트가
    `fake.unfilled_orders = [{"ord_no": "0099001", "ord_qty": "1",
    "cntr_qty": "0"}]`처럼 방금 응답의 ord_no와 일치하는 행을 채워 넣는다."""

    instances: list["_FakeKiwoomClient"] = []

    def __init__(self, *args, **kwargs):
        self.buy_calls: list[tuple] = []
        self.sell_calls: list[tuple] = []
        self.cancel_calls: list[tuple] = []
        self.quote_data = dict(FAKE_QUOTE_RAW)
        self.buy_response = {"ord_no": "0099001", "return_code": 0}
        self.sell_response = {"ord_no": "0099002", "return_code": 0}
        self.buy_error: Exception | None = None
        self.sell_error: Exception | None = None
        self.unfilled_orders: list[dict] = []
        self.get_unfilled_orders_error: Exception | None = None
        self.cancel_error: Exception | None = None
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

    async def get_unfilled_orders(self):
        if self.get_unfilled_orders_error is not None:
            raise self.get_unfilled_orders_error
        return {"oso": list(self.unfilled_orders), "return_code": 0}

    async def cancel_order(self, code, orig_order_no, qty):
        self.cancel_calls.append((code, orig_order_no, qty))
        if self.cancel_error is not None:
            raise self.cancel_error
        return {"return_code": 0}


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


# 2026-08-05부터 실제 자동매매가 이 테이블에 진짜 행을 남기기 시작했다(§5.54-6
# 완료 후 최초 실거래 진입) — `_log_rows()`가 전체 테이블을 무조건으로 읽으면
# 실제 운영 로그까지 섞여서 테스트가 깨진다. 이 워터마크(테스트 시작 시점의
# max id)로 "이 테스트가 만든 행만" 걸러낸다 — 아래 autouse 픽스처가 매 테스트
# 시작 시 갱신한다.
_test_log_watermark: dict[str, int] = {"id": 0}


@pytest.fixture(autouse=True)
async def _snapshot_and_restore_state():
    """싱글턴 AutoTradeState(id=1) 행을 스냅샷/복원한다 — 이 테이블은 실제
    배포된 엔진이 참조하는 단일 행이라, 테스트가 값을 바꿔도 반드시 원래
    값으로 되돌려야 한다.

    **2026-08-11 안전장치 추가(실사고 직후)**: 일부 테스트(예:
    `test_toggle_on_preserves_existing_holding_position` 계열)는 "킬스위치를
    켜도 보유 포지션이 보존되는지" 검증하려고 **실제 이 싱글턴 행에
    `enabled=True` + `status="holding"`을 실제로 커밋**한다 — 이 저장소는
    실 배포된 백엔드 컨테이너(`stock-backend-1`)가 60초/30초 간격으로 계속
    폴링하는 바로 그 dev Postgres를 테스트도 그대로 쓴다. 만약 진짜 킬스위치가
    켜진 채로 이 테스트 파일을 돌리면, 테스트가 그 조합을 커밋하는 찰나의
    순간에 운영 중인 백그라운드 잡이 이를 "진짜 보유 포지션"으로 읽고
    **실제 매도 주문을 시도할 수 있다** — 2026-08-11 실측으로 정확히 이 경로가
    재현됐다(다행히 그 시점 실계좌가 0주 보유라 키움이 "매도가능수량 부족"으로
    거부해 실피해는 없었지만, 계좌가 실제로 포지션을 들고 있었다면 진짜
    손절/청산이 나갔을 수 있다). 그래서 테스트 시작 전 **실제 킬스위치가
    켜져 있으면 아예 테스트를 거부**한다 — "몰래 안전하게 처리"가 아니라
    "크게 실패시켜 사람이 먼저 끄게 만드는" 쪽을 택했다(조용히 넘어가면 다음에
    또 같은 사고가 날 수 있음)."""
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
            "돌릴 수 없습니다 — 이 파일의 일부 테스트가 실제 이 싱글턴 행에 enabled=True + "
            "status=holding을 커밋하는 순간, 실 배포된 백엔드가 이를 진짜 포지션으로 읽고 "
            "실제 주문을 시도할 위험이 있습니다(2026-08-11 실측으로 재현된 사고). "
            "먼저 `POST /api/auto-trade/toggle {\"enabled\": false}`로 킬스위치를 끄고 다시 "
            "실행하세요."
        )

    start_max_log_id = await _current_max_log_id()
    _test_log_watermark["id"] = start_max_log_id

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
            entry_foreign_flow_sign=None,
        )
        defaults.update(kwargs)
        for k, v in defaults.items():
            setattr(row, k, v)
        await session.commit()


async def _get_state() -> AutoTradeState:
    async with async_session_factory() as session:
        return await session.get(AutoTradeState, STATE_ID)


async def _log_rows() -> list[AutoTradeLog]:
    """이 테스트가 만든 행만 반환한다(`_test_log_watermark` 기준) — 2026-08-05부터
    실제 운영 로그가 이 테이블에 섞여 있어, 필터 없이 전체를 읽으면 실제
    자동매매 기록까지 테스트 결과에 끼어든다(모듈 상단 주석 참고)."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(AutoTradeLog)
            .where(AutoTradeLog.id > _test_log_watermark["id"])
            .order_by(AutoTradeLog.id)
        )
        return list(result.scalars().all())


def _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=16000.0, bars_date=None):
    """bars_date 기본값은 "실제 오늘"(auto_trader.KST 기준) — run_auto_trade의
    "오늘 데이터 아니면 스킵" 가드(2026-08-18, PLAN.md §5.70)가 today_str 인자를
    안 받으면 똑같이 실제 오늘 날짜를 계산해서 대조하므로, 이 기본값이면 기존
    테스트들이 전부 "오늘 데이터"로 취급돼 today_str을 따로 주입할 필요가 없다.
    자정 경계를 걸치는 극히 드문 경우를 빼면 플레이키하지 않다 — 그 가드
    자체를 검증하는 테스트만 bars_date를 명시적으로 다르게 준다."""
    resolved_bars_date = bars_date or dt.datetime.now(auto_trader.KST).strftime("%Y%m%d")

    async def fake_warm_intraday(code, interval):
        assert code == "0167A0"
        assert interval == 1
        return {"bars": [{"close": bars_close, "volume": 1000, "date": resolved_bars_date}]}

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
# PLAN.md §5.55(2026-08-06, 실제 손실 사고 이후 안전 규칙) 배선용 몽키패치
#
# run_auto_trade/watch_stop_loss가 이제 매 폴링 `_warm_index_tiles_live`(리스크
# 경보)/`_warm_regime`(EOD 강제청산용 코스닥 외국인 confirmed_streak)를
# 호출한다 — 실제 키움/네이버를 두드리지 않도록 아래 autouse 픽스처가 기본값
# (리스크 경보 없음, 코스닥 외국인 스트릭 0=중립)으로 항상 몽키패치해 둔다.
# §5.55 전용 테스트는 각자 monkeypatch.setattr로 이 기본값을 다시 덮어쓴다
# (같은 monkeypatch 픽스처 인스턴스라 마지막 설정이 이긴다).
# ---------------------------------------------------------------------------


def _fake_index_tiles_live(alerts: list | None = None):
    async def fn(session):
        return {"risk": {"alerts": alerts or [], "has_data": True}, "market_closed": False}

    return fn


def _fake_regime(kosdaq_confirmed_streak: int = 0):
    async def fn(session):
        return {"kosdaq": {"외국인": {"confirmed_streak": kosdaq_confirmed_streak}}}

    return fn


@pytest.fixture(autouse=True)
def _patch_risk_and_regime_default(monkeypatch):
    monkeypatch.setattr(auto_trader, "_warm_index_tiles_live", _fake_index_tiles_live())
    monkeypatch.setattr(auto_trader, "_warm_regime", _fake_regime())


@pytest.fixture(autouse=True)
def _reset_log_dedup_memory():
    """PLAN.md §5.71 — `_log`의 반복 억제는 in-process 모듈 전역 dict
    (`auto_trader._last_log_by_code`)로 판정한다. 같은 pytest 프로세스 안에서
    파일 경계 없이 여러 테스트에 걸쳐 상태가 남으므로(test_auto_trade_router.py
    도 동일한 dict를 공유 — 같은 모듈 import), 매 테스트 전후로 비워 다른
    테스트의 (event_type, reason) 조합이 우연히 이번 테스트의 첫 로그를
    억제하지 않게 한다."""
    auto_trader._last_log_by_code.clear()
    yield
    auto_trader._last_log_by_code.clear()


# ---------------------------------------------------------------------------
# 킬스위치 꺼짐 — 가장 중요한 테스트: 어떤 외부 호출도 절대 일어나면 안 된다
# ---------------------------------------------------------------------------


async def test_disabled_makes_no_external_calls_and_returns_immediately(monkeypatch):
    await _set_state(enabled=False, status="idle")
    monkeypatch.setattr(auto_trader, "_warm_stock_intraday", _raising_warm_intraday)
    monkeypatch.setattr(auto_trader, "KiwoomClient", _raising_kiwoom_client_factory)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST)

    assert result == {"enabled": False, "action": "none"}
    assert await _log_rows() == []


async def test_disabled_even_while_holding_a_position_makes_no_calls(monkeypatch):
    """킬스위치가 꺼져 있으면 이미 포지션을 들고 있어도(holding) 손절/트레일
    판정조차 하지 않는다 — 꺼짐은 절대적이다."""
    await _set_state(enabled=False, status="holding", entry_price=16000, entry_qty=1)
    monkeypatch.setattr(auto_trader, "_warm_stock_intraday", _raising_warm_intraday)
    monkeypatch.setattr(auto_trader, "KiwoomClient", _raising_kiwoom_client_factory)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST)

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
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST)

    assert result == {"enabled": False, "action": "none"}


# ---------------------------------------------------------------------------
# idle — 진입 조건 미충족/충족, 예산 가드
# ---------------------------------------------------------------------------


async def test_idle_no_entry_when_conditions_unmet(monkeypatch):
    await _set_state(enabled=True, status="idle")
    _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=16000.0)
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST)

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
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST)

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


class _UnfilledBuyClient(_FakeKiwoomClient):
    """PLAN.md §5.71 — place_buy_order의 기본 응답 ord_no("0099001")가
    체결 안 된 채(cntr_qty=0) 미체결 목록에 그대로 남아있는 상황을 흉내낸다."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.unfilled_orders = [{"ord_no": self.buy_response["ord_no"], "ord_qty": "1", "cntr_qty": "0"}]


async def test_idle_entry_unconfirmed_when_buy_order_not_filled(monkeypatch):
    """PLAN.md §5.71 — 매수 주문이 접수는 됐지만 체결이 확인되지 않으면
    status를 holding으로 넘기지 않고 idle을 유지해야 한다(안 그러면 실제
    포지션이 없는데 있다고 착각해 손절 감시가 유령 포지션을 감시하게 됨)."""
    await _set_state(enabled=True, status="idle")
    _patch_signals(monkeypatch, ma_state="golden", is_spike=True, bars_close=16000.0)
    monkeypatch.setattr(auto_trader, "KiwoomClient", _UnfilledBuyClient)
    _FakeKiwoomClient.instances = []

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST)

    assert result["action"] == "buy_unconfirmed"
    fake = _FakeKiwoomClient.instances[-1]
    assert fake.buy_calls == [("0167A0", 1, 16100)]
    assert fake.cancel_calls == [("0167A0", "0099001", 1)]  # 미체결 주문 취소 시도함

    state = await _get_state()
    assert state.status == "idle"  # holding으로 안 넘어감 — 핵심 단언
    assert state.entry_price is None
    assert state.entry_qty is None

    logs = await _log_rows()
    assert len(logs) == 1
    assert logs[0].event_type == "buy_unconfirmed"
    assert "체결 미확인" in logs[0].reason


async def test_idle_entry_treats_unfilled_orders_query_failure_as_unfilled(monkeypatch):
    """get_unfilled_orders 조회 자체가 실패하면(네트워크 등) 보수적으로
    "미체결"로 취급해 상태를 넘기지 않아야 한다("확인 안 되면 이전 상태
    유지"가 항상 더 안전한 기본값)."""
    await _set_state(enabled=True, status="idle")
    _patch_signals(monkeypatch, ma_state="golden", is_spike=True, bars_close=16000.0)

    class _QueryFailsClient(_FakeKiwoomClient):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.get_unfilled_orders_error = RuntimeError("kiwoom 500")

    monkeypatch.setattr(auto_trader, "KiwoomClient", _QueryFailsClient)
    _QueryFailsClient.instances = []

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST)

    assert result["action"] == "buy_unconfirmed"
    state = await _get_state()
    assert state.status == "idle"


async def test_idle_skips_entry_silently_when_bars_are_not_from_today(monkeypatch):
    """2026-08-18(PLAN.md §5.70) — 공휴일 등 오늘 실거래 데이터가 없으면
    (`_warm_stock_intraday`가 준 마지막 봉의 날짜가 오늘과 다르면) 조건이
    충족돼 보여도 주문을 시도하지 않고, 로그도 안 남긴다(disabled/no_bars와
    동일한 노이즈 방지 원칙). 실제 사고 재현: 진입 조건은 충족(golden+spike)
    이지만 봉 날짜가 어제라 상황을 만든다."""
    await _set_state(enabled=True, status="idle")
    _patch_signals(monkeypatch, ma_state="golden", is_spike=True, bars_close=16000.0, bars_date="20260101")
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST, today_str="20260102")

    assert result["action"] == "stale_data"
    assert fake_cls.instances == []  # 주문 시도 자체가 없어야 함(가장 중요)
    assert await _log_rows() == []  # 노이즈 방지 — 공휴일 반복 시도가 로그를 채우던 사고 재현 확인
    state = await _get_state()
    assert state.status == "idle"


async def test_idle_enters_normally_when_bars_date_matches_injected_today(monkeypatch):
    """위 가드가 today_str을 명시적으로 주입해도 봉 날짜와 일치하면 평소처럼
    진입한다는 걸 확인(가드가 너무 공격적으로 막지 않는지 회귀 확인)."""
    await _set_state(enabled=True, status="idle")
    _patch_signals(monkeypatch, ma_state="golden", is_spike=True, bars_close=16000.0, bars_date="20260102")
    _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST, today_str="20260102")

    assert result["action"] == "entry"


async def test_idle_blocked_by_budget_guard_when_notional_exceeds_total_budget(monkeypatch):
    """AUTO_TRADE_TOTAL_BUDGET_KRW(25,000원)를 넘는 가격이면 진입 조건이
    충족돼도 매수를 절대 시도하지 않는다 — §5.54-2 완료 기준의 핵심 케이스."""
    await _set_state(enabled=True, status="idle")
    high_price = auto_trader.AUTO_TRADE_TOTAL_BUDGET_KRW + 5000
    _patch_signals(monkeypatch, ma_state="golden", is_spike=True, bars_close=float(high_price))
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST)

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
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST)

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
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST)

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


class _UnfilledSellClient(_FakeKiwoomClient):
    """PLAN.md §5.71 — place_sell_order의 기본 응답 ord_no("0099002")가
    체결 안 된 채(cntr_qty=0) 미체결 목록에 그대로 남아있는 상황을 흉내낸다
    (8/14 실사고 재현용). `_execute_sell`/`watch_stop_loss`는 매도 한 번에
    `async with KiwoomClient()` 블록 하나만 여므로 인스턴스는 하나뿐이다."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.unfilled_orders = [{"ord_no": self.sell_response["ord_no"], "ord_qty": "1", "cntr_qty": "0"}]


async def test_holding_stop_loss_sell_unconfirmed_keeps_position(monkeypatch):
    """PLAN.md §5.71 — 8/14 실사고 재현: 매도 주문은 접수됐지만 체결이
    확인되지 않으면(get_unfilled_orders에 그 주문이 여전히 남아있음)
    status를 idle로 넘기지 않고 holding을 그대로 유지해야 한다(안 그러면
    실제 사고처럼 손절 감시가 그대로 꺼져버림 — 이게 이번 안전장치의
    핵심 목적)."""
    await _set_state(enabled=True, status="holding", entry_price=16000, entry_qty=1)
    _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=16000 * 0.98)
    monkeypatch.setattr(auto_trader, "KiwoomClient", _UnfilledSellClient)
    _FakeKiwoomClient.instances = []

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST)

    assert result["action"] == "sell_unconfirmed"
    fake = _FakeKiwoomClient.instances[-1]
    assert fake.sell_calls == [("0167A0", 1, 16050)]
    assert fake.cancel_calls == [("0167A0", "0099002", 1)]  # 미체결 주문 취소 시도함

    state = await _get_state()
    assert state.status == "holding"  # idle로 안 넘어감 — 핵심 단언
    assert float(state.entry_price) == pytest.approx(16000.0)
    assert state.entry_qty == 1

    logs = await _log_rows()
    assert len(logs) == 1
    assert logs[0].event_type == "sell_unconfirmed"
    assert "체결 미확인" in logs[0].reason


async def test_holding_trail_activate_no_order_placed(monkeypatch):
    await _set_state(enabled=True, status="holding", entry_price=16000, entry_qty=1)
    current = 16000 * 1.02
    _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=current)
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST)

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
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST)

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
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST)

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
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST)

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
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST)

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
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST)

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
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST)

    assert result["action"] == "sell_failed"
    state = await _get_state()
    assert state.status == "holding"  # 잘못된 상태로 갱신되지 않음
    assert float(state.entry_price) == pytest.approx(16000.0)

    logs = await _log_rows()
    assert len(logs) == 1
    assert logs[0].event_type == "error"


# ---------------------------------------------------------------------------
# watch_stop_loss (PLAN.md §5.54-6, 30초 고빈도 손절 감시 — 실시간 호가 기반)
# ---------------------------------------------------------------------------


async def test_watch_disabled_makes_no_external_calls(monkeypatch):
    await _set_state(enabled=False, status="holding", entry_price=16000, entry_qty=1)
    monkeypatch.setattr(auto_trader, "KiwoomClient", _raising_kiwoom_client_factory)

    async with async_session_factory() as session:
        result = await auto_trader.watch_stop_loss(session)

    assert result == {"enabled": False, "action": "none"}
    assert await _log_rows() == []
    state = await _get_state()
    assert state.status == "holding"  # 건드리지 않음


async def test_watch_idle_status_makes_no_calls(monkeypatch):
    """이 잡은 포지션 관리 전용 — idle(진입 대기) 상태면 아무 것도 하지 않는다
    (진입 스캔은 60초 run_auto_trade가 전담)."""
    await _set_state(enabled=True, status="idle")
    monkeypatch.setattr(auto_trader, "KiwoomClient", _raising_kiwoom_client_factory)

    async with async_session_factory() as session:
        result = await auto_trader.watch_stop_loss(session)

    assert result["enabled"] is True
    assert result["action"] == "none"
    assert await _log_rows() == []


async def test_watch_holding_no_action_when_price_above_floor(monkeypatch):
    await _set_state(enabled=True, status="holding", entry_price=16000, entry_qty=1)
    fake_cls = _patch_kiwoom_client(monkeypatch)
    # FAKE_QUOTE_RAW의 매수1호가(buy_fpr_bid)는 -16050 -> abs 16050,
    # 16000 대비 (16050-16000)/16000*100 = +0.31% -> 손절(-1.5%) 아님.

    async with async_session_factory() as session:
        result = await auto_trader.watch_stop_loss(session)

    assert result["action"] == "none"
    fake = fake_cls.instances[-1]
    assert fake.sell_calls == []
    state = await _get_state()
    assert state.status == "holding"
    assert await _log_rows() == []


async def test_watch_holding_triggers_stop_loss_using_live_quote(monkeypatch):
    """실시간 호가(매수1호가)가 -1.5% 이하로 내려오면 즉시 매도 — 1분봉을
    전혀 쓰지 않는다(이 테스트는 `_warm_stock_intraday`를 아예 몽키패치하지
    않는다 — 호출되면 실제 키움을 두드리게 되므로, 호출된다면 이 테스트
    자체가 다른 이유로 실패해야 정상이다)."""
    entry_price = 20000.0  # 매수1호가 16050 -> (16050-20000)/20000*100 = -19.75% << -1.5%
    await _set_state(enabled=True, status="holding", entry_price=entry_price, entry_qty=1)
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.watch_stop_loss(session)

    assert result["action"] == "exit_stop_loss"
    fake = fake_cls.instances[-1]
    assert fake.sell_calls == [("0167A0", 1, 16050)]  # 방금 조회한 매수1호가 그대로 주문가

    state = await _get_state()
    assert state.status == "idle"
    assert state.entry_price is None
    assert state.entry_qty is None
    assert state.peak_price is None

    logs = await _log_rows()
    assert len(logs) == 1
    assert logs[0].event_type == "exit_stop_loss"
    assert "실시간" in logs[0].reason


async def test_watch_stop_loss_sell_unconfirmed_keeps_position(monkeypatch):
    """PLAN.md §5.71 — `watch_stop_loss`는 `_execute_sell`을 안 쓰고 자체
    구현이라(모듈 docstring "미체결 확인" 절 참고) 별도로 검증한다. 30초
    고빈도 손절 감시가 실제로 발동해도, 매도 주문이 미체결이면 idle로
    안 넘어가야 한다."""
    entry_price = 20000.0
    await _set_state(enabled=True, status="holding", entry_price=entry_price, entry_qty=1)
    monkeypatch.setattr(auto_trader, "KiwoomClient", _UnfilledSellClient)
    _FakeKiwoomClient.instances = []

    async with async_session_factory() as session:
        result = await auto_trader.watch_stop_loss(session)

    assert result["action"] == "sell_unconfirmed"
    fake = _FakeKiwoomClient.instances[-1]
    assert fake.sell_calls == [("0167A0", 1, 16050)]
    assert fake.cancel_calls == [("0167A0", "0099002", 1)]

    state = await _get_state()
    assert state.status == "holding"
    assert float(state.entry_price) == pytest.approx(entry_price)

    logs = await _log_rows()
    assert len(logs) == 1
    assert logs[0].event_type == "sell_unconfirmed"


async def test_watch_trailing_also_triggers_stop_loss(monkeypatch):
    """trailing 상태에서도(신고가를 찍은 뒤라도) 손절은 상태 무관하게 적용된다."""
    await _set_state(
        enabled=True, status="trailing", entry_price=20000.0, entry_qty=1, peak_price=21000.0
    )
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.watch_stop_loss(session)

    assert result["action"] == "exit_stop_loss"
    state = await _get_state()
    assert state.status == "idle"
    assert state.peak_price is None
    fake = fake_cls.instances[-1]
    assert fake.sell_calls == [("0167A0", 1, 16050)]


async def test_watch_sell_failure_keeps_state_unchanged(monkeypatch):
    await _set_state(enabled=True, status="holding", entry_price=20000.0, entry_qty=1)

    class _FailingSellClient(_FakeKiwoomClient):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.sell_error = RuntimeError("kiwoom 500")

    monkeypatch.setattr(auto_trader, "KiwoomClient", _FailingSellClient)

    async with async_session_factory() as session:
        result = await auto_trader.watch_stop_loss(session)

    assert result["action"] == "sell_failed"
    state = await _get_state()
    assert state.status == "holding"
    assert float(state.entry_price) == pytest.approx(20000.0)

    logs = await _log_rows()
    assert len(logs) == 1
    assert logs[0].event_type == "error"


async def test_watch_and_run_auto_trade_do_not_double_sell_concurrently(monkeypatch):
    """PLAN.md §5.54-6 핵심 안전장치 — `run_auto_trade`(60초)와 `watch_stop_loss`
    (30초)가 동시에 같은 포지션을 보고 동시에 매도를 시도해도(둘 다 손절
    조건이 충족된 상황을 흉내낸다), `_position_lock` 덕분에 실제 매도 주문은
    딱 1건만 나가야 한다."""
    entry_price = 20000.0
    await _set_state(enabled=True, status="holding", entry_price=entry_price, entry_qty=1)

    # run_auto_trade 쪽 신호(1분봉)도 손절 조건을 충족하도록 구성.
    _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=entry_price * 0.98)
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session_a, async_session_factory() as session_b:
        results = await asyncio.gather(
            auto_trader.run_auto_trade(session_a, now_kst=SAFE_NOW_KST),
            auto_trader.watch_stop_loss(session_b),
        )

    actions = {r["action"] for r in results}
    assert actions == {"stop_loss", "exit_stop_loss"} or actions == {"stop_loss", "none"} or actions == {
        "none",
        "exit_stop_loss",
    }
    # 핵심 단언 — 두 함수가 각자 KiwoomClient를 새로 열더라도(각자 async with
    # KiwoomClient()), 실제로 place_sell_order가 호출된 총 횟수는 1건이어야 한다.
    total_sell_calls = sum(len(c.sell_calls) for c in fake_cls.instances)
    assert total_sell_calls == 1

    state = await _get_state()
    assert state.status == "idle"
    assert state.entry_price is None

    logs = await _log_rows()
    assert len(logs) == 1  # 둘 중 하나만 실제로 로그를 남겼어야 한다(락으로 직렬화됨)
    assert logs[0].event_type == "exit_stop_loss"


# ---------------------------------------------------------------------------
# PLAN.md §5.55(2026-08-06, 실제 손실 사고 이후 안전 규칙) 통합 테스트
# ---------------------------------------------------------------------------


def _patch_risk_alert(monkeypatch, alerts: list | None = None, market_closed: bool = False):
    async def fn(session):
        return {"risk": None if market_closed else {"alerts": alerts or [], "has_data": True}, "market_closed": market_closed}

    monkeypatch.setattr(auto_trader, "_warm_index_tiles_live", fn)


def _patch_regime(monkeypatch, kosdaq_confirmed_streak: int = 0, raise_error: bool = False):
    async def fn(session):
        if raise_error:
            raise RuntimeError("regime 조회 실패(테스트)")
        return {"kosdaq": {"외국인": {"confirmed_streak": kosdaq_confirmed_streak}}}

    monkeypatch.setattr(auto_trader, "_warm_regime", fn)


def _patch_foreign_flow(monkeypatch, spot_value: float | None):
    async def fn(session, days=1):
        spot = [] if spot_value is None else [{"time": "10:00:00", "value": spot_value}]
        return {"date": "2026-08-06", "spot": spot, "futures": [], "market_closed": False}

    monkeypatch.setattr(auto_trader.intraday_snapshot, "get_foreign_position_series", fn)


# ---------------------------------------------------------------------------
# §5.55-1 — 진입 시간 필터(개장 후 10분/마감 전 10분엔 신규 진입만 금지)
# ---------------------------------------------------------------------------


async def test_entry_blocked_during_open_window(monkeypatch):
    await _set_state(enabled=True, status="idle")
    _patch_signals(monkeypatch, ma_state="golden", is_spike=True, bars_close=16000.0)
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=dt.time(9, 5))

    assert result["action"] == "entry_blocked_time"
    assert fake_cls.instances == []  # 매수 시도 자체가 없어야 함

    state = await _get_state()
    assert state.status == "idle"

    logs = await _log_rows()
    assert len(logs) == 1
    assert logs[0].event_type == "entry_blocked_time"


async def test_entry_blocked_during_close_window(monkeypatch):
    await _set_state(enabled=True, status="idle")
    _patch_signals(monkeypatch, ma_state="golden", is_spike=True, bars_close=16000.0)
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=dt.time(15, 25))

    assert result["action"] == "entry_blocked_time"
    assert fake_cls.instances == []

    state = await _get_state()
    assert state.status == "idle"


async def test_entry_allowed_just_outside_open_window(monkeypatch):
    await _set_state(enabled=True, status="idle")
    _patch_signals(monkeypatch, ma_state="golden", is_spike=True, bars_close=16000.0)
    _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=dt.time(9, 11))

    assert result["action"] == "entry"


async def test_stop_loss_not_affected_by_entry_time_block_open_window(monkeypatch):
    """손절은 진입 제한 시간대(09:00~09:10)에도 절대 영향받지 않는다 —
    `is_entry_blocked_by_time`은 idle(진입) 경로에서만 쓰인다."""
    await _set_state(enabled=True, status="holding", entry_price=16000, entry_qty=1)
    _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=16000 * 0.98)
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=dt.time(9, 5))

    assert result["action"] == "stop_loss"
    fake = fake_cls.instances[-1]
    assert fake.sell_calls == [("0167A0", 1, 16050)]


async def test_stop_loss_not_affected_by_entry_time_block_close_window(monkeypatch):
    """마감 전 10분(15:20~15:30)에도 손절은 정상 작동한다."""
    await _set_state(enabled=True, status="holding", entry_price=16000, entry_qty=1)
    _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=16000 * 0.98)
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=dt.time(15, 25))

    assert result["action"] == "stop_loss"
    fake = fake_cls.instances[-1]
    assert fake.sell_calls == [("0167A0", 1, 16050)]


# ---------------------------------------------------------------------------
# §5.55-3 — 리스크 경보 연동(신규 진입 금지 + 손절선 임시 축소)
# ---------------------------------------------------------------------------


async def test_entry_blocked_by_risk_alert(monkeypatch):
    await _set_state(enabled=True, status="idle")
    _patch_signals(monkeypatch, ma_state="golden", is_spike=True, bars_close=16000.0)
    _patch_risk_alert(monkeypatch, alerts=[{"kind": "circuit_breaker", "market": "kospi", "severity": 3}])
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST)

    assert result["action"] == "entry_blocked_risk"
    assert fake_cls.instances == []

    state = await _get_state()
    assert state.status == "idle"

    logs = await _log_rows()
    assert len(logs) == 1
    assert logs[0].event_type == "entry_blocked_risk"


async def test_holding_stop_loss_tightened_by_risk_alert(monkeypatch):
    """평상시라면 -1.5%가 아니라 손절이 아닌 -1% 하락이, 리스크 경보 활성
    중엔 임시 손절선(-0.8%)에 걸려 즉시 손절된다."""
    await _set_state(enabled=True, status="holding", entry_price=16000, entry_qty=1)
    _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=16000 * 0.99)  # -1%
    _patch_risk_alert(monkeypatch, alerts=[{"kind": "volume_surge", "market": "kosdaq", "severity": 1}])
    _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST)

    assert result["action"] == "stop_loss"
    logs = await _log_rows()
    assert len(logs) == 1
    assert logs[0].event_type == "exit_stop_loss"
    assert "리스크 경보" in logs[0].reason


async def test_holding_no_stop_loss_at_minus_1pct_without_risk_alert(monkeypatch):
    """대조군 — 리스크 경보가 없으면 같은 -1% 하락으로는 손절되지 않는다."""
    await _set_state(enabled=True, status="holding", entry_price=16000, entry_qty=1)
    _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=16000 * 0.99)
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST)

    assert result["action"] == "none"
    assert fake_cls.instances == []


# ---------------------------------------------------------------------------
# §5.55-2(최우선 규칙) — 장마감 전 조건부 오버나잇 청산
# ---------------------------------------------------------------------------


async def test_eod_forced_exit_reproduces_actual_incident(monkeypatch):
    """**2026-08-05 실제 손실 사고 재현 회귀 테스트**: 0167A0 1주 진입 후
    장중 한 번도 +1% 트레일 발동 없이("holding" 상태 그대로) 장 마감
    (15:20~15:30 KST)을 맞았다. 지금 이 순간의 평가손익이 플러스이고
    (+0.5%, 아직 트레일 전환 문턱 +1%에는 못 미침) 코스닥 외국인도 매도
    스트릭이 아니어도(리스크 경보 없음), status가 "trailing"이 아니라는
    사실 하나만으로 강제 청산돼야 한다 — 이 규칙이 있었다면 실제 사고
    (오버나잇 갭하락으로 다음날 -4.87% 손절)를 막았을 것이다."""
    entry_price = 16935.0
    current_price = entry_price * 1.005  # +0.5% -> 트레일 전환(+1%) 문턱 미달, status는 여전히 "holding"
    await _set_state(enabled=True, status="holding", entry_price=entry_price, entry_qty=1)
    _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=current_price)
    _patch_risk_alert(monkeypatch, alerts=[])  # 리스크 경보 없음
    _patch_regime(monkeypatch, kosdaq_confirmed_streak=0)  # 코스닥 외국인 매도 스트릭 아님
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=dt.time(15, 22))

    # 핵심 단언 — 가격이 플러스라도, 스트릭이 매도가 아니라도, status가
    # "trailing"이 아니라는 이유만으로 강제 청산돼야 한다.
    assert result["action"] == "exit_eod_forced"
    assert fake_cls.instances != []
    fake = fake_cls.instances[-1]
    assert fake.sell_calls == [("0167A0", 1, 16050)]  # 매수1호가로 지정가 매도(FAKE_QUOTE_RAW)

    state = await _get_state()
    assert state.status == "idle"
    assert state.entry_price is None

    logs = await _log_rows()
    assert len(logs) == 1
    assert logs[0].event_type == "exit_eod_forced"
    assert "trailing 아님" in logs[0].reason


async def test_eod_forced_exit_when_risk_alert_active_overrides_good_conditions(monkeypatch):
    """나머지 3개 조건(평가손익>0, trailing, 스트릭 정상)이 전부 통과여도
    리스크 경보가 활성 중이면 무조건 청산."""
    entry_price = 16000.0
    current_price = entry_price * 1.02
    await _set_state(
        enabled=True, status="trailing", entry_price=entry_price, entry_qty=1, peak_price=current_price
    )
    _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=current_price)
    _patch_risk_alert(monkeypatch, alerts=[{"kind": "circuit_breaker", "market": "kosdaq", "severity": 2}])
    _patch_regime(monkeypatch, kosdaq_confirmed_streak=3)
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=dt.time(15, 25))

    assert result["action"] == "exit_eod_forced"
    assert fake_cls.instances != []
    logs = await _log_rows()
    assert logs[0].event_type == "exit_eod_forced"
    assert "리스크 경보" in logs[0].reason


async def test_eod_forced_exit_when_kosdaq_foreign_selling_streak(monkeypatch):
    entry_price = 16000.0
    current_price = entry_price * 1.02
    await _set_state(
        enabled=True, status="trailing", entry_price=entry_price, entry_qty=1, peak_price=current_price
    )
    _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=current_price)
    _patch_risk_alert(monkeypatch, alerts=[])
    _patch_regime(monkeypatch, kosdaq_confirmed_streak=-2)  # 코스닥 외국인 2일 연속 매도
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=dt.time(15, 25))

    assert result["action"] == "exit_eod_forced"
    assert fake_cls.instances != []
    logs = await _log_rows()
    assert "코스닥 외국인" in logs[0].reason


async def test_eod_overnight_allowed_when_all_conditions_met(monkeypatch):
    """리스크 경보 없음 + 평가손익 플러스 + trailing + 코스닥 외국인 매도
    스트릭 아님 -> 오버나잇 허용, 매도하지 않는다."""
    entry_price = 16000.0
    peak_price = entry_price * 1.03
    current_price = entry_price * 1.02  # peak보다 낮아 dead cross 없이도 "none"
    await _set_state(
        enabled=True, status="trailing", entry_price=entry_price, entry_qty=1, peak_price=peak_price
    )
    _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=current_price)
    _patch_risk_alert(monkeypatch, alerts=[])
    _patch_regime(monkeypatch, kosdaq_confirmed_streak=2)  # 코스닥 외국인 매수 중
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=dt.time(15, 25))

    assert result["action"] == "none"
    assert fake_cls.instances == []  # 매도 시도 자체가 없어야 함

    state = await _get_state()
    assert state.status == "trailing"  # 그대로 오버나잇 보유
    assert await _log_rows() == []


async def test_eod_forced_exit_only_applies_within_time_window(monkeypatch):
    """같은 조건(holding, trailing 이력 없음)이라도 15:20~15:30 KST 밖이면
    EOD 강제청산 판정 자체를 하지 않는다."""
    entry_price = 16000.0
    current_price = entry_price * 1.005
    await _set_state(enabled=True, status="holding", entry_price=entry_price, entry_qty=1)
    _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=current_price)
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=dt.time(14, 0))

    assert result["action"] == "none"
    assert fake_cls.instances == []

    state = await _get_state()
    assert state.status == "holding"


async def test_eod_forced_exit_stop_loss_still_takes_priority(monkeypatch):
    """15:20~15:30 KST라도 손절 조건이 이미 충족되면 EOD 강제청산 판정 없이
    그냥 손절로 나간다(로그도 exit_stop_loss 하나만 남아야 한다 — 이중 판단/
    로그 없음)."""
    entry_price = 16000.0
    await _set_state(enabled=True, status="holding", entry_price=entry_price, entry_qty=1)
    _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=entry_price * 0.98)  # -2% -> 손절
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=dt.time(15, 25))

    assert result["action"] == "stop_loss"
    logs = await _log_rows()
    assert len(logs) == 1
    assert logs[0].event_type == "exit_stop_loss"


async def test_eod_forced_exit_regime_fetch_failure_falls_back_to_exit(monkeypatch):
    """regime 조회 자체가 실패하면 "확인 못 함"을 안전 측(청산)으로 폴백한다
    — 조건 충족 여부를 모르는 채로 오버나잇을 허용하지 않는다."""
    entry_price = 16000.0
    current_price = entry_price * 1.02
    await _set_state(
        enabled=True, status="trailing", entry_price=entry_price, entry_qty=1, peak_price=current_price
    )
    _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=current_price)
    _patch_risk_alert(monkeypatch, alerts=[])
    _patch_regime(monkeypatch, raise_error=True)
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=dt.time(15, 25))

    assert result["action"] == "exit_eod_forced"
    assert fake_cls.instances != []


# ---------------------------------------------------------------------------
# §5.55-4 — 수급 방향 전환 조기청산
# ---------------------------------------------------------------------------


async def test_entry_records_foreign_flow_sign(monkeypatch):
    await _set_state(enabled=True, status="idle")
    _patch_signals(monkeypatch, ma_state="golden", is_spike=True, bars_close=16000.0)
    _patch_foreign_flow(monkeypatch, spot_value=1234.0)  # 양수 -> "positive"
    _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST)

    assert result["action"] == "entry"
    state = await _get_state()
    assert state.entry_foreign_flow_sign == "positive"


async def test_holding_early_exit_on_foreign_flow_reversal(monkeypatch):
    """진입 시점엔 외인 현물 순매수(양수)였는데, 이후 폴링에서 순매도(음수)로
    반전되면 가격 조건과 무관하게(가격은 변화 없음) 조기 청산한다."""
    await _set_state(
        enabled=True, status="holding", entry_price=16000, entry_qty=1, entry_foreign_flow_sign="positive"
    )
    _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=16000.0)  # 가격 변화 없음
    _patch_foreign_flow(monkeypatch, spot_value=-500.0)  # 음수로 반전
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST)

    assert result["action"] == "exit_flow_reversal"
    assert fake_cls.instances != []

    state = await _get_state()
    assert state.status == "idle"
    assert state.entry_foreign_flow_sign is None

    logs = await _log_rows()
    assert len(logs) == 1
    assert logs[0].event_type == "exit_flow_reversal"
    assert "반전" in logs[0].reason


async def test_holding_no_early_exit_when_flow_sign_unchanged(monkeypatch):
    await _set_state(
        enabled=True, status="holding", entry_price=16000, entry_qty=1, entry_foreign_flow_sign="positive"
    )
    _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=16000.0)
    _patch_foreign_flow(monkeypatch, spot_value=500.0)  # 여전히 양수
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST)

    assert result["action"] == "none"
    assert fake_cls.instances == []
    state = await _get_state()
    assert state.status == "holding"


async def test_holding_no_early_exit_when_entry_sign_not_recorded(monkeypatch):
    """진입 시점 기록이 없는(레거시) 포지션은 반전 여부를 판정할 수 없으므로
    조기청산하지 않는다 — 데이터 부족을 "반전"으로 오판하지 않는다."""
    await _set_state(
        enabled=True, status="holding", entry_price=16000, entry_qty=1, entry_foreign_flow_sign=None
    )
    _patch_signals(monkeypatch, ma_state="none", is_spike=False, bars_close=16000.0)
    _patch_foreign_flow(monkeypatch, spot_value=-500.0)
    fake_cls = _patch_kiwoom_client(monkeypatch)

    async with async_session_factory() as session:
        result = await auto_trader.run_auto_trade(session, now_kst=SAFE_NOW_KST)

    assert result["action"] == "none"
    assert fake_cls.instances == []


# ---------------------------------------------------------------------------
# 매매일지 반복 노이즈 억제 — _log 자체 (PLAN.md §5.71)
# ---------------------------------------------------------------------------


async def test_log_dedup_suppresses_immediate_identical_repeat():
    async with async_session_factory() as session:
        await auto_trader._log(
            session, event_type="error", code="0167A0", price=100.0, reason="같은 사유"
        )
        await auto_trader._log(
            session, event_type="error", code="0167A0", price=100.0, reason="같은 사유"
        )

    logs = await _log_rows()
    assert len(logs) == 1


async def test_log_dedup_allows_immediately_different_reason():
    async with async_session_factory() as session:
        await auto_trader._log(session, event_type="error", code="0167A0", price=100.0, reason="사유 A")
        await auto_trader._log(session, event_type="error", code="0167A0", price=100.0, reason="사유 B")

    logs = await _log_rows()
    assert len(logs) == 2


async def test_log_dedup_allows_repeat_after_window_elapses():
    async with async_session_factory() as session:
        await auto_trader._log(
            session, event_type="error", code="0167A0", price=100.0, reason="같은 사유"
        )
        # 창(5분)이 지난 것처럼 in-process 기록의 타임스탬프를 과거로 되돌린다.
        event_type, reason, _ts = auto_trader._last_log_by_code["0167A0"]
        auto_trader._last_log_by_code["0167A0"] = (
            event_type,
            reason,
            dt.datetime.now(dt.timezone.utc) - auto_trader._LOG_DEDUP_WINDOW - dt.timedelta(seconds=1),
        )
        await auto_trader._log(
            session, event_type="error", code="0167A0", price=100.0, reason="같은 사유"
        )

    logs = await _log_rows()
    assert len(logs) == 2
