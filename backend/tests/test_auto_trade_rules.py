"""Unit tests for app.quant.auto_trade_rules (PLAN.md §5.54) — 자동매매 상태
전이 순수 판정 함수. 네트워크/DB 없음, 신호값 dict + 상태만으로 판정 검증."""

from __future__ import annotations

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
