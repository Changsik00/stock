"""Unit tests for app.quant.auto_trade_rules (PLAN.md §5.54/§5.55) — 자동매매
상태 전이 순수 판정 함수. 네트워크/DB 없음, 신호값 dict + 상태만으로 판정 검증."""

from __future__ import annotations

import datetime as dt

from app.quant import auto_trade_rules as rules


# ---------------------------------------------------------------------------
# evaluate_entry / decide_idle_action — 진입 조건
# ---------------------------------------------------------------------------


def test_evaluate_entry_true_when_golden_and_spike():
    assert rules.evaluate_entry("golden", True) is True


def test_evaluate_entry_false_when_golden_but_no_spike():
    assert rules.evaluate_entry("golden", False) is False


def test_evaluate_entry_false_when_spike_but_not_golden():
    assert rules.evaluate_entry("dead", True) is False
    assert rules.evaluate_entry("none", True) is False


def test_evaluate_entry_false_when_data_missing():
    assert rules.evaluate_entry(None, None) is False
    assert rules.evaluate_entry("golden", None) is False


def test_decide_idle_action_enter():
    decision = rules.decide_idle_action("golden", True)
    assert decision["action"] == "enter"
    assert "golden" in decision["reason"]


def test_decide_idle_action_none_when_unmet():
    decision = rules.decide_idle_action("none", False)
    assert decision["action"] == "none"


# ---------------------------------------------------------------------------
# evaluate_stop_loss / evaluate_trail_activate / evaluate_trail_exit
# ---------------------------------------------------------------------------


def test_evaluate_stop_loss_triggers_at_threshold():
    entry = 10000.0
    # -1.5% 정확히 -> 트리거
    assert rules.evaluate_stop_loss(entry, entry * 0.985) is True


def test_evaluate_stop_loss_not_triggered_above_threshold():
    entry = 10000.0
    assert rules.evaluate_stop_loss(entry, entry * 0.99) is False


def test_evaluate_trail_activate_triggers_at_threshold():
    entry = 10000.0
    assert rules.evaluate_trail_activate(entry, entry * 1.01) is True


def test_evaluate_trail_activate_not_triggered_below_threshold():
    entry = 10000.0
    assert rules.evaluate_trail_activate(entry, entry * 1.005) is False


def test_evaluate_trail_exit_requires_both_dead_cross_and_floor():
    entry = 10000.0
    floor_price = entry * 1.005
    assert rules.evaluate_trail_exit("dead", entry, floor_price) is True
    assert rules.evaluate_trail_exit("none", entry, floor_price) is False  # dead cross 없음
    assert rules.evaluate_trail_exit("dead", entry, entry * 1.02) is False  # 플로어 도달 전


def test_next_peak_price_updates_only_on_new_high():
    assert rules.next_peak_price(10000.0, 10500.0) == 10500.0
    assert rules.next_peak_price(10500.0, 10200.0) == 10500.0


# ---------------------------------------------------------------------------
# decide_position_action — holding/trailing 상태 전이
# ---------------------------------------------------------------------------


def test_holding_stop_loss_has_priority_over_trail_activate():
    """이론상 동시에 성립할 수 없는 두 조건이지만, 손절이 항상 먼저 확인돼야
    한다는 계약을 명시적으로 검증(§5.54 "상태 무관 항상 감시" 요구)."""
    entry = 10000.0
    decision = rules.decide_position_action("holding", entry, None, "golden", entry * 0.98)
    assert decision["action"] == "stop_loss"


def test_holding_no_action_when_neither_condition_met():
    entry = 10000.0
    decision = rules.decide_position_action("holding", entry, None, "none", entry * 1.002)
    assert decision["action"] == "none"


def test_holding_trail_activate_sets_peak_to_current_price():
    entry = 10000.0
    current = entry * 1.02
    decision = rules.decide_position_action("holding", entry, None, "none", current)
    assert decision["action"] == "trail_activate"
    assert decision["new_peak_price"] == current


def test_holding_stop_loss_takes_priority_even_past_trail_threshold_then_crashing():
    """holding 상태에서 손절가까지 급락한 경우 — trail_activate가 아니라
    stop_loss가 나와야 한다(먼저 확인)."""
    entry = 10000.0
    decision = rules.decide_position_action("holding", entry, None, "none", entry * 0.98)
    assert decision["action"] == "stop_loss"


def test_trailing_stop_loss_has_priority_over_exit_trail():
    entry = 10000.0
    decision = rules.decide_position_action("trailing", entry, entry * 1.03, "dead", entry * 0.98)
    assert decision["action"] == "stop_loss"


def test_trailing_peak_update_when_new_high_and_no_exit():
    entry = 10000.0
    peak = entry * 1.02
    current = entry * 1.05  # 신고가지만 dead cross 아니라 청산 아님
    decision = rules.decide_position_action("trailing", entry, peak, "none", current)
    assert decision["action"] == "peak_update"
    assert decision["new_peak_price"] == current


def test_trailing_none_when_price_unchanged_and_no_dead_cross():
    entry = 10000.0
    peak = entry * 1.02
    decision = rules.decide_position_action("trailing", entry, peak, "none", peak)
    assert decision["action"] == "none"


def test_trailing_exit_when_dead_cross_and_floor_reached():
    entry = 10000.0
    peak = entry * 1.03
    current = entry * 1.003  # 진입가 대비 +0.3% <= +0.5% 플로어
    decision = rules.decide_position_action("trailing", entry, peak, "dead", current)
    assert decision["action"] == "exit_trail"
    assert decision["new_peak_price"] == peak  # 신고가 갱신 없음(현재가가 peak보다 낮음)


def test_trailing_no_exit_when_dead_cross_but_floor_not_reached():
    entry = 10000.0
    peak = entry * 1.03
    current = entry * 1.02  # dead cross 있지만 아직 +0.5% 플로어 위
    decision = rules.decide_position_action("trailing", entry, peak, "dead", current)
    assert decision["action"] == "none"


def test_trailing_peak_none_falls_back_to_entry_price():
    """방어적 처리 — peak_price가 아직 None인(비정상) trailing 상태에서도
    크래시하지 않고 entry_price를 기준으로 계산한다."""
    entry = 10000.0
    decision = rules.decide_position_action("trailing", entry, None, "none", entry * 1.01)
    assert decision["action"] == "peak_update"
    assert decision["new_peak_price"] == entry * 1.01


# ---------------------------------------------------------------------------
# check_entry_budget — 누적 예산 가드
# ---------------------------------------------------------------------------


def test_check_entry_budget_allows_when_idle_and_within_limits():
    assert rules.check_entry_budget("idle", 16000, 25000, 50000) is True


def test_check_entry_budget_rejects_when_not_idle():
    """이미 보유 중이면(holding/trailing) 추가 매수를 절대 허용하지 않는다 —
    §5.54-2 완료 기준의 핵심 케이스."""
    assert rules.check_entry_budget("holding", 16000, 25000, 50000) is False
    assert rules.check_entry_budget("trailing", 16000, 25000, 50000) is False


def test_check_entry_budget_rejects_when_over_total_budget():
    assert rules.check_entry_budget("idle", 26000, 25000, 50000) is False


def test_check_entry_budget_rejects_when_over_max_order_notional():
    assert rules.check_entry_budget("idle", 16000, 25000, 15000) is False


def test_check_entry_budget_boundary_exact_limits_allowed():
    assert rules.check_entry_budget("idle", 25000, 25000, 50000) is True


# ---------------------------------------------------------------------------
# PLAN.md §5.55 — 안전 규칙 (2026-08-06, 실제 손실 사고 이후)
# ---------------------------------------------------------------------------
# §5.55-1: is_entry_blocked_by_time
# ---------------------------------------------------------------------------


def test_entry_time_block_true_during_open_window():
    assert rules.is_entry_blocked_by_time(dt.time(9, 5)) is True


def test_entry_time_block_true_during_close_window():
    assert rules.is_entry_blocked_by_time(dt.time(15, 25)) is True


def test_entry_time_block_boundaries_inclusive():
    assert rules.is_entry_blocked_by_time(dt.time(9, 0)) is True
    assert rules.is_entry_blocked_by_time(dt.time(9, 10)) is True
    assert rules.is_entry_blocked_by_time(dt.time(15, 20)) is True
    assert rules.is_entry_blocked_by_time(dt.time(15, 30)) is True


def test_entry_time_block_false_outside_windows():
    assert rules.is_entry_blocked_by_time(dt.time(9, 11)) is False
    assert rules.is_entry_blocked_by_time(dt.time(8, 59)) is False
    assert rules.is_entry_blocked_by_time(dt.time(10, 0)) is False
    assert rules.is_entry_blocked_by_time(dt.time(15, 19)) is False
    assert rules.is_entry_blocked_by_time(dt.time(15, 31)) is False


# ---------------------------------------------------------------------------
# §5.55-3: evaluate_stop_loss threshold_pct / decide_position_action risk_alert_active
# ---------------------------------------------------------------------------


def test_evaluate_stop_loss_default_threshold_unaffected():
    """threshold_pct를 넘기지 않으면 기존 STOP_LOSS_PCT(-1.5%) 그대로 —
    기존 호출부(watch_stop_loss 등 §5.55 이전 코드)와의 하위호환 확인."""
    entry = 10000.0
    assert rules.evaluate_stop_loss(entry, entry * 0.99) is False  # -1% -> 기존 임계값 미달
    assert rules.evaluate_stop_loss(entry, entry * 0.985) is True  # -1.5% -> 트리거


def test_evaluate_stop_loss_tighter_threshold_when_risk_alert():
    entry = 10000.0
    current = entry * 0.99  # -1% -> 평상시(-1.5%) 기준으로는 미손절
    assert rules.evaluate_stop_loss(entry, current) is False
    assert rules.evaluate_stop_loss(entry, current, rules.STOP_LOSS_PCT_RISK_ALERT) is True  # -0.8% 기준으로는 손절


def test_decide_position_action_uses_normal_threshold_by_default():
    entry = 10000.0
    current = entry * 0.99  # -1% -> 평상시 손절선(-1.5%) 안 닿음
    decision = rules.decide_position_action("holding", entry, None, "none", current)
    assert decision["action"] == "none"


def test_decide_position_action_risk_alert_tightens_stop_loss():
    """리스크 경보 활성 중엔 -1% 하락만으로도(평상시라면 손절 아님) 임시
    손절선(-0.8%)에 걸려 손절된다 — §5.55-3 핵심 케이스."""
    entry = 10000.0
    current = entry * 0.99  # -1%
    decision = rules.decide_position_action(
        "holding", entry, None, "none", current, risk_alert_active=True
    )
    assert decision["action"] == "stop_loss"
    assert "리스크 경보" in decision["reason"]


def test_decide_position_action_risk_alert_does_not_affect_trail_activate():
    """리스크 경보가 손절선만 조정할 뿐, 트레일 전환 등 다른 판정에는 영향
    없어야 한다."""
    entry = 10000.0
    current = entry * 1.02  # +2% -> 트레일 전환 조건(+1%↑), 손절과 무관
    decision = rules.decide_position_action(
        "holding", entry, None, "none", current, risk_alert_active=True
    )
    assert decision["action"] == "trail_activate"


# ---------------------------------------------------------------------------
# §5.55-2(최우선 규칙): evaluate_eod_forced_exit
# ---------------------------------------------------------------------------


def test_eod_forced_exit_not_applicable_outside_time_window():
    decision = rules.evaluate_eod_forced_exit(
        now_kst=dt.time(14, 0),
        status="holding",
        unrealized_pnl_positive=True,
        kosdaq_foreign_streak_ok=True,
        risk_alert_active=False,
    )
    assert decision["should_exit"] is False


def test_eod_forced_exit_not_applicable_when_idle():
    decision = rules.evaluate_eod_forced_exit(
        now_kst=dt.time(15, 25),
        status="idle",
        unrealized_pnl_positive=True,
        kosdaq_foreign_streak_ok=True,
        risk_alert_active=False,
    )
    assert decision["should_exit"] is False


def test_eod_forced_exit_risk_alert_forces_exit_unconditionally():
    """리스크 경보 활성 중이면 나머지 3개 조건이 전부 충족돼도 무조건 청산."""
    decision = rules.evaluate_eod_forced_exit(
        now_kst=dt.time(15, 25),
        status="trailing",
        unrealized_pnl_positive=True,
        kosdaq_foreign_streak_ok=True,
        risk_alert_active=True,
    )
    assert decision["should_exit"] is True
    assert "리스크 경보" in decision["reason"]


def test_eod_forced_exit_allows_overnight_when_all_three_conditions_met():
    decision = rules.evaluate_eod_forced_exit(
        now_kst=dt.time(15, 25),
        status="trailing",
        unrealized_pnl_positive=True,
        kosdaq_foreign_streak_ok=True,
        risk_alert_active=False,
    )
    assert decision["should_exit"] is False


def test_eod_forced_exit_boundary_times_inclusive():
    for t in (dt.time(15, 20), dt.time(15, 30)):
        decision = rules.evaluate_eod_forced_exit(
            now_kst=t,
            status="holding",
            unrealized_pnl_positive=True,
            kosdaq_foreign_streak_ok=True,
            risk_alert_active=False,
        )
        assert decision["should_exit"] is True  # holding(트레일 이력 없음) -> 강제청산


def test_eod_forced_exit_negative_pnl_forces_exit():
    decision = rules.evaluate_eod_forced_exit(
        now_kst=dt.time(15, 25),
        status="trailing",
        unrealized_pnl_positive=False,
        kosdaq_foreign_streak_ok=True,
        risk_alert_active=False,
    )
    assert decision["should_exit"] is True
    assert "평가손익" in decision["reason"]


def test_eod_forced_exit_kosdaq_foreign_selling_streak_forces_exit():
    decision = rules.evaluate_eod_forced_exit(
        now_kst=dt.time(15, 25),
        status="trailing",
        unrealized_pnl_positive=True,
        kosdaq_foreign_streak_ok=False,
        risk_alert_active=False,
    )
    assert decision["should_exit"] is True
    assert "코스닥 외국인" in decision["reason"]


def test_eod_forced_exit_holding_without_trail_history_forces_exit_even_if_pnl_positive():
    """2026-08-05 실제 손실 사고를 그대로 재현하는 규칙 레벨 케이스 — 장중
    한 번도 +1%를 못 넘어 trailing으로 전환된 적 없이("holding"인 채로)
    15:20을 맞으면, 지금 당장은 평가손익이 플러스이고 코스닥 외국인도
    매도 중이 아니어도(나머지 조건 다 통과) status가 "trailing"이 아니라는
    이유만으로 강제 청산돼야 한다."""
    decision = rules.evaluate_eod_forced_exit(
        now_kst=dt.time(15, 22),
        status="holding",
        unrealized_pnl_positive=True,
        kosdaq_foreign_streak_ok=True,
        risk_alert_active=False,
    )
    assert decision["should_exit"] is True
    assert "trailing 아님" in decision["reason"]


# ---------------------------------------------------------------------------
# §5.55-4: foreign_flow_sign / evaluate_foreign_flow_reversal_exit
# ---------------------------------------------------------------------------


def test_foreign_flow_sign_encoding():
    assert rules.foreign_flow_sign(100.0) == "positive"
    assert rules.foreign_flow_sign(-100.0) == "negative"
    assert rules.foreign_flow_sign(0.0) is None
    assert rules.foreign_flow_sign(None) is None


def test_foreign_flow_reversal_exit_triggers_on_sign_flip():
    decision = rules.evaluate_foreign_flow_reversal_exit("positive", "negative")
    assert decision["should_exit"] is True
    assert "반전" in decision["reason"]


def test_foreign_flow_reversal_exit_no_trigger_when_sign_unchanged():
    decision = rules.evaluate_foreign_flow_reversal_exit("positive", "positive")
    assert decision["should_exit"] is False


def test_foreign_flow_reversal_exit_no_trigger_when_data_missing():
    """진입 시점 기록이 없거나(None) 현재 값이 없으면(0/None) 반전 여부를
    판정할 수 없으므로 청산하지 않는다 — 데이터 부족을 "반전"으로 오판하지
    않기 위해."""
    assert rules.evaluate_foreign_flow_reversal_exit(None, "negative")["should_exit"] is False
    assert rules.evaluate_foreign_flow_reversal_exit("positive", None)["should_exit"] is False
    assert rules.evaluate_foreign_flow_reversal_exit(None, None)["should_exit"] is False
