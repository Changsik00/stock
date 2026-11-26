"""Phase 5.118 수동 매수 청산 조건 테스트.

실제 `AutoTradeState` 싱글턴·킬스위치·키움에는 일절 닿지 않는다 — 가짜 상태 객체/세션과
몽키패치만 쓴다(실거래 DB 행을 건드리는 기존 collector/router 테스트와 달리 킬스위치가
켜져 있어도 실행된다).
"""

from __future__ import annotations

import datetime as dt
import inspect
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.collectors import auto_trader
from app.db import get_session
from app.main import app
from app.quant import auto_trade_performance
from app.quant.exit_rules import (
    decide_manual_exit,
    analyze_rules,
    describe_rules,
    trail_active,
    trigger_price,
    validate_rules,
)
from app.routers import auto_trade as router_mod

R = lambda kind, value, **kw: {"kind": kind, "value": value, **kw}  # noqa: E731


# -- 순수 판정 경계값 ---------------------------------------------------------


@pytest.mark.parametrize(
    "rule,cur,expected",
    [
        (R("take_profit_pct", 1), 10100, True),   # 정확히 +1% 경계 포함
        (R("take_profit_pct", 1), 10099, False),
        (R("stop_loss_pct", 1), 9900, True),      # 정확히 -1% 경계 포함
        (R("stop_loss_pct", 1), 9901, False),
        (R("target_price", 10300), 10300, True),
        (R("target_price", 10300), 10299, False),
        (R("stop_price", 9800), 9800, True),
        (R("stop_price", 9800), 9801, False),
    ],
)
def test_single_rule_boundaries(rule, cur, expected):
    d = decide_manual_exit([rule], 10000, None, cur)
    assert (d["action"] == "exit") is expected
    if expected:
        assert d["reason"]


def test_trail_immediate_uses_entry_as_initial_peak():
    rules = [R("trail_pct", 0.5)]
    assert decide_manual_exit(rules, 10000, None, 9950)["action"] == "exit"  # 진입가 대비 -0.5%
    assert decide_manual_exit(rules, 10000, None, 9951)["action"] != "exit"
    # 고점 갱신 후 고점 대비 -0.5%
    assert decide_manual_exit(rules, 10000, 10200, 10149)["action"] == "exit"
    assert decide_manual_exit(rules, 10000, 10200, 10150)["action"] != "exit"


def test_trail_with_activate_requires_peak_reached_first():
    rules = [R("trail_pct", 0.5, activate_pct=1)]
    # 활성화 전: 진입가 대비 -0.5%여도 트레일은 발동하지 않는다
    assert decide_manual_exit(rules, 10000, None, 9900)["action"] != "exit"
    # 고점이 +1%(10100)에 닿은 적이 있으면 활성, 고점 대비 -0.5% 하락 시 청산
    assert decide_manual_exit(rules, 10000, 10100, 10049)["action"] == "exit"
    assert decide_manual_exit(rules, 10000, 10100, 10050)["action"] != "exit"
    # 같은 틱에 현재가가 +1%를 처음 넘으면 고점 갱신만(청산 아님)
    d = decide_manual_exit(rules, 10000, 10050, 10110)
    assert d["action"] == "peak_update" and d["new_peak_price"] == 10110
    assert trail_active(rules[0], 10000, 10100) and not trail_active(rules[0], 10000, 10099)


def test_peak_update_and_none():
    rules = [R("take_profit_pct", 5)]
    assert decide_manual_exit(rules, 10000, None, 10000)["action"] == "peak_update"  # peak 최초 기록
    assert decide_manual_exit(rules, 10000, 10100, 10100)["action"] == "none"
    assert decide_manual_exit(rules, 10000, 10100, 10050)["action"] == "none"
    assert decide_manual_exit(rules, 10000, 10100, 10200)["new_peak_price"] == 10200


def test_multiple_rules_first_hit_and_priority():
    rules = [R("take_profit_pct", 1), R("stop_loss_pct", 1), R("trail_pct", 0.5)]
    assert decide_manual_exit(rules, 10000, None, 10100)["rule"]["kind"] == "take_profit_pct"
    assert decide_manual_exit(rules, 10000, None, 9900)["rule"]["kind"] == "stop_loss_pct"
    # 손절과 트레일이 동시에 닿으면 손절 사유
    assert decide_manual_exit(rules, 10000, None, 9890)["rule"]["kind"] == "stop_loss_pct"
    assert decide_manual_exit([], 10000, None, 5000)["action"] in ("none", "peak_update")  # 조건 없음


def test_trigger_price_and_describe():
    assert trigger_price(R("take_profit_pct", 1), 10000, None) == 10100
    assert trigger_price(R("stop_loss_pct", 1.5), 11050, None) == 10884  # 10884.25 내림
    assert trigger_price(R("trail_pct", 0.5), 10000, 10200) == 10149  # 10149 정확
    assert trigger_price(R("trail_pct", 0.5), 10000, None) == 9950
    assert trigger_price(R("target_price", 12345), 10000, None) == 12345
    assert "도달 후" in describe_rules([R("trail_pct", 0.5, activate_pct=1)])
    assert describe_rules([]) == "조건 없음"


@pytest.mark.parametrize(
    "raw,err",
    [
        ([R("take_profit_pct", 0.19)], True),   # 익절 하한 0.2
        ([R("take_profit_pct", 0.2)], False),
        ([R("take_profit_pct", 30)], False),
        ([R("take_profit_pct", 30.1)], True),
        ([R("stop_loss_pct", 0.19)], True),
        ([R("stop_loss_pct", 30)], False),
        ([R("stop_loss_pct", -1)], True),
        ([R("trail_pct", 0.09)], True),         # 고점 대비 하한 0.1
        ([R("trail_pct", 10)], False),
        ([R("trail_pct", 10.1)], True),
        ([R("trail_pct", 1, activate_pct=0.09)], True),
        ([R("trail_pct", 1, activate_pct=30)], False),
        ([R("trail_pct", 1, activate_pct=30.1)], True),
        ([R("target_price", 10000)], True),     # 목표가는 현재가 초과여야 함
        ([R("target_price", 10001)], False),
        ([R("stop_price", 10000)], True),       # 손절가는 현재가 미만이어야 함
        ([R("stop_price", 9999)], False),
        ([R("stop_price", 0)], True),
        ([R("foo", 1)], True),
        ([R("take_profit_pct", 1), R("take_profit_pct", 2)], True),  # 같은 종류 중복
        ([R("stop_loss_pct", 1, activate_pct=1)], True),             # activate는 trail 전용
        ([R("trail_pct", 1, activate_pct=0)], True),
        ([R("trail_pct", 1, activate_pct=1)], False),
        ([], False),
    ],
)
def test_validate_rules(raw, err):
    rules, msgs = validate_rules(raw, 10000)
    assert bool(msgs) is err
    if not err:
        assert len(rules) == len(raw)


# -- analyze_rules: errors / warnings / scenarios ------------------------------

STATS = {"avg_range_pct": 0.8, "max_up_pct": 2.5, "days": 20}


def _an(rules, ref=10000, **kw):
    return analyze_rules(rules, ref, **kw)


def _has(msgs, needle):
    return any(needle in m for m in msgs)


def test_error_max_five_rules():
    ok = _an([R("take_profit_pct", 2), R("stop_loss_pct", 1), R("trail_pct", 0.5), R("target_price", 10500)][:3])
    assert not ok["errors"]
    six = [R("take_profit_pct", 2)] * 3 + [R("stop_loss_pct", 1)] * 3
    assert _has(_an(six)["errors"], "최대 5개")


def test_error_tp_and_target_together_and_sl_and_stop_together():
    r = _an([R("take_profit_pct", 2), R("target_price", 10300)])
    assert _has(r["errors"], "익절은 % 또는 목표가 중 하나만")
    r = _an([R("stop_loss_pct", 1), R("stop_price", 9900)])
    assert _has(r["errors"], "손절은 % 또는 손절가 중 하나만")


@pytest.mark.parametrize(
    "tp,act,blocked",
    [(1, 1, True), (0.9, 1, True), (1.01, 1, False), (3, 1, False)],
)
def test_error_trail_never_active_boundary(tp, act, blocked):
    r = _an([R("take_profit_pct", tp), R("trail_pct", 0.5, activate_pct=act), R("stop_loss_pct", 1)])
    assert _has(r["errors"], "절대 작동하지 않습니다") is blocked


def test_error_trail_never_active_with_target_price():
    # 목표가 10100 = +1% → '+1% 도달 후'와 같아 차단
    r = _an([R("target_price", 10100), R("trail_pct", 0.5, activate_pct=1)])
    assert _has(r["errors"], "절대 작동하지 않습니다")


@pytest.mark.parametrize("ref,v,blocked", [(10000, 0.1, False), (10000, 0.1, False), (4999, 0.2, True), (5000, 0.2, False), (9000, 0.1, True)])
def test_error_trail_tick_noise(ref, v, blocked):
    # 호가단위 5원 → 2틱 = 10원. 하락폭 ref*v% < 10원이면 차단
    r = _an([R("trail_pct", v)], ref=ref)
    assert _has(r["errors"], "2틱 미만") is blocked


def test_error_price_vs_reference():
    assert _has(_an([R("target_price", 10000)])["errors"], "바로 발동")
    assert not _an([R("target_price", 10001)])["errors"]
    assert _has(_an([R("stop_price", 10000)])["errors"], "바로 발동")
    assert not _an([R("stop_price", 9999)])["errors"]


def test_error_stop_above_target_contradiction():
    # 가격 조건끼리는 기준가를 몰라도 비교한다(손절가 >= 목표가는 모순)
    r = analyze_rules([R("target_price", 9900), R("stop_price", 9950)], None)
    assert _has(r["errors"], "모순")
    assert _has(analyze_rules([R("target_price", 9900), R("stop_price", 9900)], None)["errors"], "모순")  # 같아도 모순
    assert not analyze_rules([R("target_price", 9951), R("stop_price", 9950)], None)["errors"]
    # 정상 조합은 모순 아님
    assert not _an([R("take_profit_pct", 0.2), R("stop_price", 9990)])["errors"]


def test_warning_no_stop_and_trail_substitute():
    r = _an([R("take_profit_pct", 1)])
    assert not r["errors"] and _has(r["warnings"], "손절 조건이 없습니다")
    r = _an([R("trail_pct", 0.5)])
    assert _has(r["warnings"], "사실상 손절 역할") and _has(r["warnings"], "진입 직후부터 작동")
    assert _has(r["warnings"], "약 -0.2%에 매도")  # 1.003*(1-0.005)-1 = -0.2%
    r = _an([R("trail_pct", 0.5, activate_pct=1), R("take_profit_pct", 3)])
    assert _has(r["warnings"], "손절 조건이 없습니다") and not _has(r["warnings"], "사실상 손절")


def test_warning_trail_wider_than_activate():
    r = _an([R("stop_loss_pct", 1), R("trail_pct", 2, activate_pct=1)])
    assert _has(r["warnings"], "진입가 아래") and _has(r["warnings"], "-1.02%")  # 1.01*0.98-1
    r = _an([R("stop_loss_pct", 1), R("trail_pct", 1, activate_pct=1)])
    assert not _has(r["warnings"], "진입가 아래")  # 같으면 경고 없음(경계 포함 안 함)


def test_warning_risk_reward_boundary():
    assert _has(_an([R("take_profit_pct", 0.5), R("stop_loss_pct", 1)])["warnings"], "손익비")
    assert not _has(_an([R("take_profit_pct", 1), R("stop_loss_pct", 1)])["warnings"], "손익비")
    assert _has(_an([R("target_price", 10050), R("stop_price", 9900)])["warnings"], "손익비")


def test_warning_small_take_profit_and_stop_boundary():
    assert _has(_an([R("take_profit_pct", 0.29), R("stop_loss_pct", 0.2)])["warnings"], "수수료")
    assert not _has(_an([R("take_profit_pct", 0.3), R("stop_loss_pct", 0.3)])["warnings"], "수수료")
    w = _an([R("take_profit_pct", 1), R("stop_loss_pct", 0.29)], stats=STATS)["warnings"]
    assert _has(w, "일중 잡음") and _has(w, "평균 일중 변동 0.80%")
    w = _an([R("take_profit_pct", 1), R("stop_loss_pct", 0.29)])["warnings"]
    assert _has(w, "일중 잡음") and not _has(w, "평균 일중 변동")  # 통계 없으면 수치 생략
    assert not _has(_an([R("take_profit_pct", 1), R("stop_loss_pct", 0.3)])["warnings"], "일중 잡음")


def test_warning_take_profit_above_recent_max_up():
    w = _an([R("take_profit_pct", 2.6), R("stop_loss_pct", 1)], stats=STATS)["warnings"]
    assert _has(w, "거의 닿지 않") and _has(w, "이 익절은 대부분 닿지 않을")
    w = _an([R("take_profit_pct", 2.6), R("stop_loss_pct", 1), R("trail_pct", 0.5, activate_pct=1)], stats=STATS)["warnings"]
    assert _has(w, "고점 대비 하락 조건이 청산")
    assert not _has(_an([R("take_profit_pct", 2.5), R("stop_loss_pct", 1)], stats=STATS)["warnings"], "거의 닿지 않")
    assert not _has(_an([R("take_profit_pct", 9), R("stop_loss_pct", 1)])["warnings"], "거의 닿지 않")  # 통계 없음


def test_info_polling_only_when_rules_and_not_warning():
    r = _an([R("take_profit_pct", 1), R("stop_loss_pct", 1)])
    assert r["warnings"] == [] and _has(r["infos"], "30초")
    assert _an([])["infos"] == []


def test_clean_recommended_presets_have_no_errors():
    for rules in (
        [R("take_profit_pct", 1), R("stop_loss_pct", 1)],
        [R("stop_loss_pct", 1), R("trail_pct", 0.5, activate_pct=1)],
        [R("stop_loss_pct", 1), R("trail_pct", 0.5, activate_pct=1), R("take_profit_pct", 3)],
    ):
        r = _an(rules)
        assert r["errors"] == [] and r["warnings"] == []


def test_scenarios_match_decide_manual_exit():
    rules = [R("stop_loss_pct", 1), R("trail_pct", 0.5, activate_pct=1), R("take_profit_pct", 3)]
    sc = _an(rules)["scenarios"]
    assert len(sc) == 4
    up, bend, down, eod = sc
    assert up["rule"] == "익절 +3%" and up["exit_price"] == 10300
    assert down["rule"] == "손절 -1%" and down["exit_price"] == 9900
    # 꺾임: +1% 도달 후 고점 -0.5% — 반환된 가격에서 decide가 실제로 그 사유로 청산해야 한다
    assert "도달 후 고점 대비" in bend["rule"] and bend["exited"]
    peak = 10000 * 1.015
    d = decide_manual_exit(rules, 10000, peak, bend["exit_price"])
    assert d["action"] == "exit" and d["rule"]["kind"] == "trail_pct"
    assert not eod["exited"] and "15:20" in eod["result"]
    # 청산 직전 가격(한 호가 위)에서는 청산되지 않는다 — 동일 로직 재사용 확인
    assert decide_manual_exit(rules, 10000, peak, bend["exit_price"] + 5)["action"] != "exit"


@pytest.mark.parametrize("ref", [10000, 10885, 10880, 12347, 9999, 15551])
@pytest.mark.parametrize("n", [0.3, 1.5, 2, 3, 5, 7.7, 10, 30])
def test_straight_up_scenario_always_hits_take_profit(ref, n):
    rules = [R("take_profit_pct", n), R("stop_loss_pct", 1)]
    if n > 1:
        rules.append(R("trail_pct", 0.5, activate_pct=1))
    r = _an(rules, ref=ref)
    assert not r["errors"], r["errors"]
    up = r["scenarios"][0]
    assert up["exited"] and up["rule"] == f"익절 +{n:g}%", up
    down = r["scenarios"][2]
    assert down["exited"] and down["rule"] == "손절 -1%", down


def test_scenario_regression_reported_case():
    r = _an([R("take_profit_pct", 5), R("trail_pct", 0.5, activate_pct=1), R("stop_loss_pct", 1)], ref=10885)
    assert r["scenarios"][0]["rule"] == "익절 +5%"


def test_eod_scenario_text_has_no_overnight_exception_for_manual():
    r = _an([R("take_profit_pct", 2), R("stop_loss_pct", 1)])
    eod = r["scenarios"][3]["result"]
    assert "15:20~15:30" in eod and "오버나잇 예외 없음" in eod and "맞추면 보유" not in eod


def test_scenarios_skipped_when_structural_error_or_no_reference():
    assert _an([R("take_profit_pct", 99)])["scenarios"] == []
    assert analyze_rules([R("take_profit_pct", 1)], None)["scenarios"] == []
    assert _an([])["scenarios"] == []


def test_scenarios_for_holding_use_entry_and_peak():
    rules = [R("stop_loss_pct", 1), R("trail_pct", 0.5, activate_pct=1)]
    r = analyze_rules(rules, 10120, entry_price=10000, peak=10150)
    assert not r["errors"] and len(r["scenarios"]) == 4
    # 이미 활성화된 고점(10150)에서 시작 → 하락 경로는 트레일로 청산(10099)
    assert any(s["exited"] and s["rule"] and "고점 대비" in s["rule"] for s in r["scenarios"])


# -- 가짜 상태/세션 -----------------------------------------------------------


class FakeSession:
    def __init__(self, state=None):
        self.state = state
        self.commits = 0

    async def get(self, model, pk):
        return self.state

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        return None


def make_state(**kw):
    base = dict(
        enabled=True, status="holding", code="0167A0", entry_price=10000, entry_qty=1,
        entry_at=dt.datetime.now(dt.timezone.utc), entry_order_no="1", peak_price=None,
        entry_foreign_flow_sign="positive", entry_source=None, manual_exit_rules=None,
        updated_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def manual_state(rules, **kw):
    import json

    blob = json.dumps({"rules": rules, "original": rules, "modified_at": None})
    return make_state(entry_source="manual", manual_exit_rules=blob, **kw)


@pytest.fixture
def sells(monkeypatch):
    """`_execute_sell`을 기록용으로 대체 — 실제 주문 경로는 호출되지 않는다."""
    calls: list[dict] = []

    async def fake_sell(session, state, code, price, event_type, reason, snapshot, result):
        calls.append({"event": event_type, "reason": reason, "price": price})
        return result

    monkeypatch.setattr(auto_trader, "_execute_sell", fake_sell)
    return calls


async def _handle(state, price, now=dt.time(10, 0), risk=False):
    result = {"enabled": True, "action": "none"}
    return await auto_trader._handle_position(FakeSession(state), state, "0167A0", price, {}, result, now, risk)


@pytest.fixture
def no_flow(monkeypatch):
    """외인 수급 조회: 진입 시점과 같은 부호(반전 없음)."""

    async def fake_series(session, days=1):
        return {"spot": [{"value": 100.0}]}

    monkeypatch.setattr(auto_trader.intraday_snapshot, "get_foreign_position_series", fake_series)


# -- manual vs auto 분기 ------------------------------------------------------


async def test_auto_state_uses_fixed_stop_loss(sells, no_flow):
    state = make_state()  # entry_source None = 자동 진입
    await _handle(state, 9840)  # -1.6% -> 고정 손절(-1.5%)
    assert [c["event"] for c in sells] == ["exit_stop_loss"]


async def test_auto_state_risk_alert_stop(sells, no_flow):
    await _handle(make_state(), 9915, risk=True)  # -0.85% -> 경보 손절(-0.8%)
    assert [c["event"] for c in sells] == ["exit_stop_loss"]


async def test_manual_without_stop_does_not_apply_default_stop(sells, no_flow):
    state = manual_state([R("take_profit_pct", 2)])
    await _handle(state, 9500)  # -5%여도 사용자 조건에 손절이 없으니 청산 안 함
    await _handle(state, 9915, risk=True)  # 위험경보 -0.8%도 적용 안 함
    assert sells == []


async def test_manual_rule_exit_uses_execute_sell_with_event(sells, no_flow):
    state = manual_state([R("take_profit_pct", 1), R("stop_loss_pct", 1)])
    await _handle(state, 10100)
    assert [c["event"] for c in sells] == ["exit_manual_rule"]
    assert "익절 +1%" in sells[0]["reason"] and "+1.00%" in sells[0]["reason"]


async def test_manual_user_stop_applies(sells, no_flow):
    await _handle(manual_state([R("stop_loss_pct", 3)]), 9700)
    assert [c["event"] for c in sells] == ["exit_manual_rule"]


async def test_manual_peak_is_persisted(sells, no_flow):
    state = manual_state([R("take_profit_pct", 5)])
    await _handle(state, 10200)
    assert state.peak_price == 10200 and sells == []


async def test_manual_status_stays_holding_no_trail_activate(sells, no_flow):
    state = manual_state([R("take_profit_pct", 5)])
    await _handle(state, 10200)  # +2% (엔진이라면 trailing 전환)
    assert state.status == "holding"


async def test_manual_flow_reversal_still_applies(sells, monkeypatch):
    async def reversed_series(session, days=1):
        return {"spot": [{"value": -50.0}]}

    monkeypatch.setattr(auto_trader.intraday_snapshot, "get_foreign_position_series", reversed_series)
    await _handle(manual_state([R("take_profit_pct", 5)]), 10010)
    assert [c["event"] for c in sells] == ["exit_flow_reversal"]


async def test_manual_eod_forced_exit_still_applies(sells, no_flow, monkeypatch):
    async def regime(session):
        return {"kosdaq": {"외국인": {"confirmed_streak": -3}}}

    monkeypatch.setattr(auto_trader, "_warm_regime", regime)
    await _handle(manual_state([R("take_profit_pct", 5)]), 9990, now=dt.time(15, 25))
    assert [c["event"] for c in sells] == ["exit_eod_forced"]


async def test_manual_broken_json_falls_back_to_engine_rules(sells, no_flow):
    state = make_state(entry_source="manual", manual_exit_rules="{깨짐")
    await _handle(state, 9840)
    assert [c["event"] for c in sells] == ["exit_stop_loss"]  # 고정 손절로 폴백


# -- 30초 실시간 감시 경로 ----------------------------------------------------


class FakeKiwoom:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __getattr__(self, name):
        if name.startswith(("place_", "cancel_")):
            raise AssertionError(f"감시 경로에서 직접 주문 금지: {name}")
        raise AttributeError(name)


async def _watch(monkeypatch, state, live_price):
    session = FakeSession(state)

    async def get_state(s):
        return state

    async def best(client, code, side):
        return live_price

    async def risk(s):
        return False

    monkeypatch.setattr(auto_trader, "_get_state", get_state)
    monkeypatch.setattr(auto_trader, "_best_fill_price", best)
    monkeypatch.setattr(auto_trader, "_get_risk_alert_active", risk)
    monkeypatch.setattr(auto_trader, "KiwoomClient", FakeKiwoom)
    return await auto_trader.watch_stop_loss(session)


async def test_watch_manual_exit_via_execute_sell(monkeypatch, sells):
    state = manual_state([R("stop_price", 9800)])
    res = await _watch(monkeypatch, state, 9800)
    assert res["action"] == "exit_manual_rule"
    assert [c["event"] for c in sells] == ["exit_manual_rule"]
    assert "30초" in sells[0]["reason"]


async def test_watch_manual_ignores_default_stop(monkeypatch, sells):
    state = manual_state([R("take_profit_pct", 2)])
    res = await _watch(monkeypatch, state, 9700)  # -3%
    assert res["action"] == "none" and sells == []


async def test_watch_manual_updates_peak(monkeypatch, sells):
    state = manual_state([R("take_profit_pct", 9)])
    await _watch(monkeypatch, state, 10150)
    assert state.peak_price == 10150 and sells == []


async def test_watch_auto_path_unchanged_does_not_call_execute_sell(monkeypatch, sells):
    # 자동 진입: 기존 경로는 자체 주문 코드를 쓴다(여기선 가짜 키움이 주문을 거부하므로
    # sell_failed가 나오고, 수동 경로의 _execute_sell은 호출되지 않아야 한다).
    class AutoKiwoom(FakeKiwoom):
        async def place_sell_order(self, *a):
            raise RuntimeError("blocked in test")

    state = make_state()
    session = FakeSession(state)

    async def get_state(s):
        return state

    async def best(client, code, side):
        return 9800  # -2% -> 고정 손절

    async def risk(s):
        return False

    async def noop_log(*a, **k):
        return None

    monkeypatch.setattr(auto_trader, "_get_state", get_state)
    monkeypatch.setattr(auto_trader, "_best_fill_price", best)
    monkeypatch.setattr(auto_trader, "_get_risk_alert_active", risk)
    monkeypatch.setattr(auto_trader, "KiwoomClient", AutoKiwoom)
    monkeypatch.setattr(auto_trader, "_log", noop_log)
    res = await auto_trader.watch_stop_loss(session)
    assert res["action"] == "sell_failed" and sells == []


# -- 주문은 _execute_sell 경유만 / 리셋 ---------------------------------------


def test_manual_paths_have_no_direct_order_calls():
    for fn in (auto_trader._handle_manual_position, auto_trader._watch_manual_position):
        src = inspect.getsource(fn)
        assert "place_sell_order" not in src and "place_buy_order" not in src
        assert "_execute_sell" in src


async def test_execute_sell_resets_manual_fields(monkeypatch):
    class Client(FakeKiwoom):
        async def place_sell_order(self, code, qty, price):
            return {"ord_no": "9", "return_code": 0}

    async def best(client, code, side):
        return 10100

    async def confirm(client, code, qty):
        return True

    async def noop_log(*a, **k):
        return None

    monkeypatch.setattr(auto_trader, "KiwoomClient", Client)
    monkeypatch.setattr(auto_trader, "_best_fill_price", best)
    monkeypatch.setattr(auto_trader, "_confirm_holdings_qty", confirm)
    monkeypatch.setattr(auto_trader, "_log", noop_log)
    state = manual_state([R("take_profit_pct", 1)], peak_price=10100)
    await auto_trader._execute_sell(
        FakeSession(state), state, "0167A0", 10100, "exit_manual_rule", "r", {}, {"action": "x"}
    )
    assert state.status == "idle"
    assert state.entry_source is None and state.manual_exit_rules is None
    assert state.entry_price is None and state.peak_price is None


def test_performance_counts_manual_rule_exit():
    assert "exit_manual_rule" in auto_trade_performance.EXIT_EVENT_TYPES
    assert "exit_manual_rule" in auto_trader.TRADE_EVENT_TYPES


# -- 라우터: manual-buy body 검증 / PUT 수정 (가짜 세션, 실DB 미접촉) ----------


class FakeOrderClient:
    buys: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get_orderable_cash(self):
        return 10_010  # 매도1호가 10010 기준 1주

    async def place_buy_order(self, code, qty, price):
        FakeOrderClient.buys.append((code, qty, price))
        return {"ord_no": "77", "return_code": 0}

    def __getattr__(self, name):
        if name.startswith(("place_sell", "cancel_")):
            raise AssertionError(f"매도/취소 호출 금지: {name}")
        raise AttributeError(name)


@pytest.fixture
def api(monkeypatch):
    state = make_state(status="idle", entry_price=None, entry_qty=None, entry_at=None,
                       entry_order_no=None, entry_foreign_flow_sign=None)
    logs: list[dict] = []
    FakeOrderClient.buys = []

    async def override():
        yield FakeSession(state)

    async def intraday(code, interval):
        return {"bars": [{"close": 10000}]}

    async def best(client, code, side):
        return 10010

    async def confirm(client, code, qty):
        return True

    async def fake_log(session, **kw):
        logs.append(kw)

    async def series(session, days=1):
        return {"spot": [{"value": 5.0}]}

    app.dependency_overrides[get_session] = override
    monkeypatch.setattr(router_mod, "_warm_stock_intraday", intraday)
    monkeypatch.setattr(router_mod, "KiwoomClient", FakeOrderClient)
    monkeypatch.setattr(router_mod, "_best_fill_price", best)
    monkeypatch.setattr(router_mod, "_confirm_holdings_qty", confirm)
    monkeypatch.setattr(router_mod, "_log", fake_log)
    monkeypatch.setattr(router_mod.intraday_snapshot, "get_foreign_position_series", series)
    yield SimpleNamespace(state=state, logs=logs)
    app.dependency_overrides.pop(get_session, None)


def _http():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_manual_buy_with_rules_records_manual_source(api):
    rules = [R("take_profit_pct", 2), R("stop_loss_pct", 1), R("trail_pct", 0.5, activate_pct=1)]
    async with _http() as c:
        r = await c.post("/api/auto-trade/manual-buy", json={"rules": rules})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entry_source"] == "manual"
    mr = body["manual_exit_rules"]
    assert [x["kind"] for x in mr["rules"]] == ["take_profit_pct", "stop_loss_pct", "trail_pct"]
    # 진입가 10010 기준 발동가(원)
    assert mr["rules"][0]["trigger_price"] == 10211 and mr["rules"][1]["trigger_price"] == 9909
    assert mr["rules"][2]["active"] is False and mr["rules"][2]["activate_price"] == 10111
    assert mr["modified_at"] is None and mr["has_stop"] is True
    assert api.state.entry_source == "manual"
    entry_log = [lg for lg in api.logs if lg["event_type"] == "manual_entry"][0]
    assert "익절 +2%" in entry_log["reason"] and "손절 -1%" in entry_log["reason"]
    assert FakeOrderClient.buys == [("0167A0", 1, 10010)]


async def test_manual_buy_without_stop_logs_warning_text(api):
    async with _http() as c:
        r = await c.post("/api/auto-trade/manual-buy", json={"rules": [R("take_profit_pct", 1)], "acknowledge_warnings": True})
    assert r.status_code == 200
    assert r.json()["manual_exit_rules"]["has_stop"] is False
    assert "손절 조건 없음" in api.logs[-1]["reason"]


async def test_manual_buy_without_rules_stays_engine_rules(api):
    async with _http() as c:
        r = await c.post("/api/auto-trade/manual-buy", json={})
        assert r.status_code == 200
    assert r.json()["entry_source"] == "auto" and r.json()["manual_exit_rules"] is None
    assert api.state.entry_source is None


@pytest.mark.parametrize(
    "rules",
    [
        [R("take_profit_pct", 0)],
        [R("stop_loss_pct", 31)],
        [R("take_profit_pct", 1), R("trail_pct", 0.5, activate_pct=1)],  # 트레일 무력화
        [R("take_profit_pct", 1), R("take_profit_pct", 2)],
        [R("target_price", 9999)],   # 현재가 10000 이하
        [R("stop_price", 10001)],    # 현재가 10000 이상
        [R("bogus", 1)],
    ],
)
async def test_manual_buy_rejects_invalid_rules_before_ordering(api, rules):
    async with _http() as c:
        r = await c.post("/api/auto-trade/manual-buy", json={"rules": rules})
    assert r.status_code == 400
    assert FakeOrderClient.buys == [] and api.state.status == "idle"


async def test_put_rules_only_for_manual_position(api):
    async with _http() as c:
        # idle
        r = await c.put("/api/auto-trade/manual-exit-rules", json={"rules": []})
        assert r.status_code == 409
        # 자동 진입 보유
        api.state.status = "holding"
        api.state.entry_price = 10000
        r = await c.put("/api/auto-trade/manual-exit-rules", json={"rules": [R("stop_loss_pct", 1)]})
        assert r.status_code == 409


async def test_put_rules_updates_and_keeps_original(api):
    async with _http() as c:
        await c.post("/api/auto-trade/manual-buy", json={"rules": [R("take_profit_pct", 1), R("stop_loss_pct", 1)]})
        r = await c.put("/api/auto-trade/manual-exit-rules", json={"rules": [R("stop_loss_pct", 2)]})
        assert r.status_code == 200, r.text
        mr = r.json()["manual_exit_rules"]
        assert [x["kind"] for x in mr["rules"]] == ["stop_loss_pct"]
        assert [x["kind"] for x in mr["original"]] == ["take_profit_pct", "stop_loss_pct"]
        assert mr["modified_at"] is not None
        bad = await c.put("/api/auto-trade/manual-exit-rules", json={"rules": [R("stop_price", 99999)]})
        assert bad.status_code == 400
    upd = [lg for lg in api.logs if lg["event_type"] == "manual_rules_update"]
    assert len(upd) == 1 and "-> [손절 -2%]" in upd[0]["reason"]
    assert len(FakeOrderClient.buys) == 1  # PUT은 주문을 내지 않는다


async def test_manual_buy_errors_return_400_with_message_list(api):
    async with _http() as c:
        r = await c.post(
            "/api/auto-trade/manual-buy",
            json={"rules": [R("take_profit_pct", 1), R("trail_pct", 0.5, activate_pct=1)], "acknowledge_warnings": True},
        )
    assert r.status_code == 400
    d = r.json()["detail"]
    assert d["errors"] and "절대 작동하지 않습니다" in d["message"]
    assert FakeOrderClient.buys == []


async def test_manual_buy_warnings_need_acknowledge_then_proceed(api):
    body = {"rules": [R("take_profit_pct", 1)]}
    async with _http() as c:
        r = await c.post("/api/auto-trade/manual-buy", json=body)
        assert r.status_code == 409
        d = r.json()["detail"]
        assert d["needs_acknowledge"] and any("손절 조건이 없습니다" in w for w in d["warnings"])
        assert FakeOrderClient.buys == [] and api.state.status == "idle"  # 확인 전엔 주문 없음
        r = await c.post("/api/auto-trade/manual-buy", json={**body, "acknowledge_warnings": True})
        assert r.status_code == 200
    assert FakeOrderClient.buys == [("0167A0", 1, 10010)]


async def test_put_rules_errors_and_warnings_gate(api):
    async with _http() as c:
        await c.post("/api/auto-trade/manual-buy", json={"rules": [R("take_profit_pct", 2), R("stop_loss_pct", 1)]})
        bad = await c.put("/api/auto-trade/manual-exit-rules", json={"rules": [R("take_profit_pct", 1), R("take_profit_pct", 2)]})
        assert bad.status_code == 400 and bad.json()["detail"]["errors"]
        warn = await c.put("/api/auto-trade/manual-exit-rules", json={"rules": [R("take_profit_pct", 2)]})
        assert warn.status_code == 409 and warn.json()["detail"]["warnings"]
        assert [x["kind"] for x in api.state.manual_exit_rules and __import__("json").loads(api.state.manual_exit_rules)["rules"]] == [
            "take_profit_pct",
            "stop_loss_pct",
        ]  # 거부된 요청은 저장을 바꾸지 않는다
        ok = await c.put(
            "/api/auto-trade/manual-exit-rules",
            json={"rules": [R("take_profit_pct", 2)], "acknowledge_warnings": True},
        )
        assert ok.status_code == 200
    assert len(FakeOrderClient.buys) == 1


async def test_analyze_api_has_no_side_effects(api):
    before = (api.state.status, api.state.entry_source, api.state.manual_exit_rules, len(api.logs))
    async with _http() as c:
        r = await c.post(
            "/api/auto-trade/manual-exit-rules/analyze",
            json={"rules": [R("take_profit_pct", 1), R("trail_pct", 0.5, activate_pct=1)]},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["errors"] and body["reference_price"] == 10000
    assert "scenarios" in body and "warnings" in body
    assert (api.state.status, api.state.entry_source, api.state.manual_exit_rules, len(api.logs)) == before
    assert FakeOrderClient.buys == []


async def test_analyze_api_holding_uses_entry_and_peak(api):
    api.state.status = "holding"
    api.state.entry_price = 10000
    api.state.peak_price = 10150
    api.state.entry_source = "manual"
    async with _http() as c:
        r = await c.post(
            "/api/auto-trade/manual-exit-rules/analyze",
            json={"rules": [R("stop_loss_pct", 1), R("trail_pct", 0.5, activate_pct=1)]},
        )
    body = r.json()
    assert r.status_code == 200 and body["entry_price"] == 10000
    assert any(s["exited"] for s in body["scenarios"])


async def test_preset_save_rejects_error_combos():
    from app.routers.auto_trade import add_exit_preset, PresetIn, ExitRuleIn
    from fastapi import HTTPException

    body = PresetIn(
        name="x",
        rules=[ExitRuleIn(kind="take_profit_pct", value=1), ExitRuleIn(kind="trail_pct", value=0.5, activate_pct=1)],
    )
    with pytest.raises(HTTPException) as ei:
        await add_exit_preset(body, session=None)  # 검증 단계에서 거부 — 세션 접근 전
    assert ei.value.status_code == 400
