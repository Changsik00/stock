"""Phase 5.121 — 매매 탭 조회 전용 노출(mode / entry_reason / exit_levels / last_eval).

안전: `AutoTradeState` id=1은 실거래 행이라 이 파일은 DB를 전혀 쓰지 않는다. 순수 함수는
메모리 객체로, `run_auto_trade`는 `_get_state`만 메모리 행으로 바꿔 실제 판정 코드를
그대로 돌린다(주문 클라이언트는 호출되면 실패하게 막고, 진입 조건 미충족 경로만 탄다).
가짜로 바꾸는 건 외부 소스(키움 분봉·리스크 경보)뿐이다.
"""

from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import pytest

from app.collectors import auto_trader
from app.market_hours import KST
from app.quant import auto_trade_rules as rules
from app.routers import auto_trade as router


# ---------------- derive_mode ----------------


@pytest.mark.parametrize(
    "enabled,status,source,expected",
    [
        (False, "idle", None, "stopped"),
        (True, "idle", None, "watching"),
        (True, "holding", "auto", "auto_holding"),
        (True, "trailing", "auto", "auto_holding"),
        (True, "holding", "manual", "manual_holding"),
        (False, "holding", "auto", "auto_holding"),  # 보유 중이면 킬스위치와 무관하게 보유 모드
    ],
)
def test_derive_mode(enabled, status, source, expected):
    assert router.derive_mode(enabled, status, source) == expected


# ---------------- entry_reason ----------------


def test_entry_reason_none_without_row():
    assert router.build_entry_reason(None) is None


def test_entry_reason_parses_signal_snapshot():
    ts = dt.datetime(2026, 11, 26, 1, 15, tzinfo=dt.timezone.utc)
    snap = {"golden_cross_recent": True, "volume_spike_recent": True, "current_price": 16100}
    row = SimpleNamespace(ts=ts, event_type="entry", reason="골든크로스+급증", signal_snapshot=json.dumps(snap))
    out = router.build_entry_reason(row)
    assert out["event_type"] == "entry"
    assert out["reason"] == "골든크로스+급증"
    assert out["signal"] == snap
    assert out["ts"] == ts.isoformat()


def test_entry_reason_broken_snapshot_is_null():
    row = SimpleNamespace(ts=None, event_type="manual_entry", reason="수동", signal_snapshot="{깨짐")
    out = router.build_entry_reason(row)
    assert out["signal"] is None and out["ts"] is None


# ---------------- exit_levels ----------------


def test_exit_levels_auto_uses_engine_constants():
    levels = {x["key"]: x for x in router.build_exit_levels(16000.0, None, "auto", None)}
    assert levels["stop_loss"]["price"] == int((16000 * (1 - abs(rules.STOP_LOSS_PCT) / 100) + 1e-6) // 1)
    assert levels["stop_loss"]["pct"] == pytest.approx(-1.5, abs=0.01)
    assert levels["stop_loss_risk_alert"]["price"] > levels["stop_loss"]["price"]
    assert levels["trail_activate"]["price"] > 16000
    assert levels["trail_activate"]["pct"] == pytest.approx(rules.TRAIL_ACTIVATE_PCT, abs=0.01)


def test_exit_levels_manual_uses_user_rules():
    manual = [{"kind": "stop_loss_pct", "value": 2.0}, {"kind": "target_price", "value": 17000}]
    out = router.build_exit_levels(16000.0, None, "manual", manual)
    assert [x["price"] for x in out] == [15680, 17000]
    assert not any(x["key"] in ("stop_loss", "trail_activate") for x in out)


def test_exit_levels_empty_without_entry():
    assert router.build_exit_levels(None, None, "auto", None) == []


def test_engine_constants_expose_entry_block_windows():
    c = router._engine_rule_constants()
    assert c["entry_block_windows"] == [["09:00", "09:10"], ["15:20", "15:30"]]


# ---------------- last_eval ----------------

AT = dt.datetime(2026, 11, 26, 10, 31, 5, tzinfo=KST)


def test_build_last_eval_none_when_disabled():
    assert auto_trader.build_last_eval({"enabled": False, "action": "none"}, AT) is None


def test_build_last_eval_block_reasons():
    base = {"enabled": True, "status": "idle", "golden_recent": True, "spike_recent": True, "current_price": 16100}
    blocked = auto_trader.build_last_eval({**base, "action": "entry_blocked_time"}, AT)
    assert blocked["block_reason"] == "entry_blocked_time"
    assert blocked["at"].startswith("2026-11-26T10:31:05")
    quiet = auto_trader.build_last_eval({**base, "action": "none"}, AT)
    assert quiet["block_reason"] is None and quiet["golden_recent"] is True


def test_record_last_eval_keeps_previous_when_disabled(monkeypatch):
    monkeypatch.setattr(auto_trader, "LAST_EVAL", {"at": "x"})
    auto_trader.record_last_eval({"enabled": False, "action": "none"})
    assert auto_trader.LAST_EVAL == {"at": "x"}
    auto_trader.record_last_eval({"enabled": True, "action": "none", "status": "idle"}, AT)
    assert auto_trader.LAST_EVAL["status"] == "idle"


async def test_run_auto_trade_idle_result_carries_signal_flags(monkeypatch):
    """진입 조건 미충족(idle) 경로가 golden_recent/spike_recent/current_price를 돌려준다.
    DB는 쓰지 않는다(상태 행은 메모리, 주문 클라이언트는 호출 시 실패)."""
    state = SimpleNamespace(enabled=True, status="idle", code=auto_trader.TARGET_CODE)
    today = dt.datetime.now(KST).strftime("%Y%m%d")
    bars = [
        {"date": today, "time": f"09{m:02d}00", "open": 16000, "high": 16000, "low": 16000, "close": 16000, "volume": 1000}
        for m in range(10, 50)
    ]

    async def fake_state(session):
        return state

    async def fake_intraday(code, interval):
        return {"bars": bars}

    async def no_risk(session):
        return False

    def forbidden(*a, **k):
        raise AssertionError("주문 클라이언트가 호출되면 안 된다")

    monkeypatch.setattr(auto_trader, "_get_state", fake_state)
    monkeypatch.setattr(auto_trader, "_warm_stock_intraday", fake_intraday)
    monkeypatch.setattr(auto_trader, "_get_risk_alert_active", no_risk)
    monkeypatch.setattr(auto_trader, "KiwoomClient", forbidden)

    result = await auto_trader.run_auto_trade(None, now_kst=dt.time(10, 0), today_str=today)
    assert result["action"] == "none"
    assert result["golden_recent"] is False
    assert result["spike_recent"] is False
    assert result["current_price"] == 16000
    assert result["status"] == "idle"
