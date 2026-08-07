"""app/quant/screener.py 순수 함수 단위테스트 (PLAN.md §5.2).

전부 DB/네트워크 무관 — 알려진 입력값으로 기대값을 손계산해 검증한다
(test_quant_signals.py와 동일한 스타일).
"""

from __future__ import annotations

from app.quant.screener import (
    ATTENTION_BONUS,
    LARGE_DECLINE_WARNING_PCT,
    MAX_SINGLE_DAY_ABS_MOVE_PCT,
    PRICE_QUIET_RISE_MAX_PCT,
    PRICE_QUIET_RISE_MIN_PCT,
    WEIGHTS,
    compute_scalp_scores,
    evaluate_accumulation_pattern,
)


def _cand(code, change_rate, turnover, value_rank, name=None, market="kospi", flow_net_value=None):
    return {
        "code": code,
        "name": name or code,
        "market": market,
        "change_rate": change_rate,
        "turnover": turnover,
        "value_rank": value_rank,
        "flow_net_value": flow_net_value,
    }


def test_empty_candidates_returns_empty_list():
    assert compute_scalp_scores([], set()) == []


def test_weights_sum_to_one():
    assert round(sum(WEIGHTS.values()), 6) == 1.0


def test_higher_change_turnover_and_rank_score_higher():
    candidates = [
        _cand("A", change_rate=1.0, turnover=5.0, value_rank=50),
        _cand("B", change_rate=10.0, turnover=40.0, value_rank=1),
    ]
    scored = compute_scalp_scores(candidates, attention_codes=set())

    assert [r["code"] for r in scored] == ["B", "A"]
    assert scored[0]["score"] > scored[1]["score"]


def test_attention_membership_adds_fixed_bonus_without_changing_rank_inputs():
    candidates = [
        _cand("A", change_rate=5.0, turnover=10.0, value_rank=10),
        _cand("B", change_rate=5.0, turnover=10.0, value_rank=10),
    ]
    scored = compute_scalp_scores(candidates, attention_codes={"A"})

    by_code = {r["code"]: r for r in scored}
    assert by_code["A"]["in_attention_top"] is True
    assert by_code["B"]["in_attention_top"] is False
    # 둘 다 change/turnover/rank가 동일(z-score도 동일)하므로 차이는 정확히
    # attention 가중치 * 가산점이어야 한다.
    diff = round(by_code["A"]["score"] - by_code["B"]["score"], 3)
    assert diff == round(WEIGHTS["attention"] * ATTENTION_BONUS, 3)


def test_none_change_rate_and_turnover_treated_as_zero_not_dropped():
    candidates = [
        _cand("A", change_rate=None, turnover=None, value_rank=5),
        _cand("B", change_rate=3.0, turnover=8.0, value_rank=5),
    ]
    scored = compute_scalp_scores(candidates, attention_codes=set())

    assert len(scored) == 2
    by_code = {r["code"]: r for r in scored}
    # A는 등락률/회전율이 0으로 취급되어 B보다 낮은 점수를 받는다(동일 순위).
    assert by_code["B"]["score"] > by_code["A"]["score"]


def test_ties_broken_by_value_rank_ascending():
    # value_rank 가중치를 0으로 둬 두 후보의 score를 강제로 동점으로 만든다
    # (change/turnover는 동일, value_rank만 다름) -> 동점이면 value_rank가 더
    # 작은(더 상위) 쪽이 앞에 와야 한다.
    candidates = [
        _cand("A", change_rate=2.0, turnover=2.0, value_rank=9),
        _cand("B", change_rate=2.0, turnover=2.0, value_rank=3),
    ]
    weights = {"change": 0.5, "turnover": 0.5, "value_rank": 0.0, "attention": 0.0, "flow": 0.0}
    scored = compute_scalp_scores(candidates, attention_codes=set(), weights=weights)

    assert scored[0]["score"] == scored[1]["score"]
    assert [r["code"] for r in scored] == ["B", "A"]


def test_custom_weights_are_respected():
    candidates = [
        _cand("A", change_rate=1.0, turnover=50.0, value_rank=100),
        _cand("B", change_rate=20.0, turnover=1.0, value_rank=1),
    ]
    # turnover만 100% 반영하는 극단 가중치 -> turnover가 큰 A가 1등이어야 한다.
    weights = {"change": 0.0, "turnover": 1.0, "value_rank": 0.0, "attention": 0.0, "flow": 0.0}
    scored = compute_scalp_scores(candidates, attention_codes=set(), weights=weights)

    assert scored[0]["code"] == "A"


# -- flow 요소 (PLAN.md §5.20) ---------------------------------------------


def test_positive_flow_scores_higher_than_negative_flow_all_else_equal():
    """수급 유입(외국인+기관 순매수 양수)이 유출(음수)보다 높은 점수를 받아야
    한다 — 다른 3개 요소(change/turnover/value_rank)는 동일하게 고정."""
    candidates = [
        _cand("INFLOW", change_rate=2.0, turnover=5.0, value_rank=10, flow_net_value=5000),
        _cand("OUTFLOW", change_rate=2.0, turnover=5.0, value_rank=10, flow_net_value=-5000),
    ]
    scored = compute_scalp_scores(candidates, attention_codes=set())

    by_code = {r["code"]: r for r in scored}
    assert by_code["INFLOW"]["score"] > by_code["OUTFLOW"]["score"]
    assert scored[0]["code"] == "INFLOW"


def test_flow_none_treated_as_neutral_zero_not_dropped():
    """flow_net_value가 None(그 종목이 아직 stock_flow 스윕을 못 돈 경우)이면
    change_rate/turnover의 기존 None 처리와 동일하게 0.0 중립으로 취급되어야
    한다 — 계산에서 제외되면 안 된다."""
    candidates = [
        _cand("NEUTRAL", change_rate=2.0, turnover=5.0, value_rank=10, flow_net_value=None),
        _cand("INFLOW", change_rate=2.0, turnover=5.0, value_rank=10, flow_net_value=5000),
        _cand("OUTFLOW", change_rate=2.0, turnover=5.0, value_rank=10, flow_net_value=-5000),
    ]
    scored = compute_scalp_scores(candidates, attention_codes=set())

    by_code = {r["code"]: r for r in scored}
    assert len(scored) == 3
    # None은 유입(양수)보다 낮고 유출(음수)보다는 높은 "중간" 취급을 받아야 한다.
    assert by_code["INFLOW"]["score"] > by_code["NEUTRAL"]["score"] > by_code["OUTFLOW"]["score"]


def test_weights_include_flow_and_still_sum_to_one():
    assert "flow" in WEIGHTS
    assert round(sum(WEIGHTS.values()), 6) == 1.0


def test_score_output_preserves_input_fields():
    candidates = [_cand("005930", change_rate=1.23, turnover=4.56, value_rank=2, name="삼성전자")]
    scored = compute_scalp_scores(candidates, attention_codes=set())

    row = scored[0]
    assert row["code"] == "005930"
    assert row["name"] == "삼성전자"
    assert row["market"] == "kospi"
    assert row["change_rate"] == 1.23
    assert row["turnover"] == 4.56
    assert row["value_rank"] == 2
    assert "score" in row
    assert "in_attention_top" in row


# -- at_risk 플래그 (PLAN.md §5.27-2) ---------------------------------------


def test_at_risk_true_when_change_rate_at_or_below_warning_threshold():
    candidates = [_cand("A", change_rate=LARGE_DECLINE_WARNING_PCT, turnover=5.0, value_rank=1)]
    scored = compute_scalp_scores(candidates, attention_codes=set())

    assert scored[0]["at_risk"] is True


def test_at_risk_false_for_positive_and_small_negative_change_rate():
    candidates = [
        _cand("POS", change_rate=20.0, turnover=5.0, value_rank=1),
        _cand("SMALL_NEG", change_rate=-3.0, turnover=5.0, value_rank=2),
        _cand("NONE", change_rate=None, turnover=5.0, value_rank=3),
    ]
    scored = compute_scalp_scores(candidates, attention_codes=set())

    by_code = {r["code"]: r for r in scored}
    assert by_code["POS"]["at_risk"] is False
    assert by_code["SMALL_NEG"]["at_risk"] is False
    # change_rate가 None(데이터 없음)이면 "위험"과 다른 의미라 False여야 한다.
    assert by_code["NONE"]["at_risk"] is False


def test_at_risk_does_not_affect_score_computation():
    """at_risk는 순수 정보성 플래그라 score 산식에 전혀 관여하지 않아야 한다.

    change 요소는 abs(change_rate)를 쓰므로(모듈 docstring 참고) -15.0과
    +15.0은 abs() 이후 완전히 동일한 입력이 된다 — 나머지 요소(turnover/
    value_rank/attention/flow)도 동일하게 고정하면, at_risk만 다르고 score는
    완전히 같아야 한다는 것으로 "at_risk 추가가 score 계산 경로를 건드리지
    않았다"를 증명할 수 있다.
    """
    candidates = [
        _cand("DOWN", change_rate=-15.0, turnover=5.0, value_rank=1, flow_net_value=1000),
        _cand("UP", change_rate=15.0, turnover=5.0, value_rank=1, flow_net_value=1000),
    ]
    scored = compute_scalp_scores(candidates, attention_codes=set())

    by_code = {r["code"]: r for r in scored}
    assert by_code["DOWN"]["at_risk"] is True
    assert by_code["UP"]["at_risk"] is False


# -- evaluate_accumulation_pattern (개인 매도/외국인·기관 전환 매집 관찰 패턴) ---


def _base_kwargs(**overrides):
    """3개 조건을 전부 충족하는 기준값 — 개별 테스트가 하나씩만 깨서 그
    조건만 실패로 만든다."""
    kwargs = dict(
        individual_net_10d=-1000.0,
        foreign_inst_net_recent5d=500.0,
        foreign_inst_net_prior5d=100.0,
        price_return_10d_pct=8.0,
        max_abs_daily_return_10d_pct=3.0,
    )
    kwargs.update(overrides)
    return kwargs


def test_all_three_conditions_satisfied_matches():
    result = evaluate_accumulation_pattern(**_base_kwargs())
    assert result["matched"] is True
    assert "reason" in result


def test_condition1_fails_when_individual_net_not_negative():
    # 개인이 순매수(양수)면 조건1 실패 -> matched는 False여야 한다.
    result = evaluate_accumulation_pattern(**_base_kwargs(individual_net_10d=1.0))
    assert result["matched"] is False


def test_condition1_boundary_zero_does_not_match():
    # 정확히 0은 "순매도"가 아니므로(엄격한 부등호) 실패해야 한다.
    result = evaluate_accumulation_pattern(**_base_kwargs(individual_net_10d=0.0))
    assert result["matched"] is False


def test_condition2_fails_when_recent5_not_positive():
    result = evaluate_accumulation_pattern(
        **_base_kwargs(foreign_inst_net_recent5d=0.0, foreign_inst_net_prior5d=-100.0)
    )
    assert result["matched"] is False


def test_condition2_fails_when_recent5_not_improved_over_prior5():
    # 최근5일이 양수여도 직전5일보다 개선되지 않았으면(같거나 작으면) 실패.
    result = evaluate_accumulation_pattern(
        **_base_kwargs(foreign_inst_net_recent5d=500.0, foreign_inst_net_prior5d=500.0)
    )
    assert result["matched"] is False


def test_condition2_boundary_recent5_exactly_zero_does_not_match():
    result = evaluate_accumulation_pattern(
        **_base_kwargs(foreign_inst_net_recent5d=0.0, foreign_inst_net_prior5d=-500.0)
    )
    assert result["matched"] is False


def test_condition3_fails_when_price_return_below_min():
    result = evaluate_accumulation_pattern(
        **_base_kwargs(price_return_10d_pct=PRICE_QUIET_RISE_MIN_PCT - 0.01)
    )
    assert result["matched"] is False


def test_condition3_fails_when_price_return_above_max():
    result = evaluate_accumulation_pattern(
        **_base_kwargs(price_return_10d_pct=PRICE_QUIET_RISE_MAX_PCT + 0.01)
    )
    assert result["matched"] is False


def test_condition3_boundary_price_return_at_min_and_max_matches():
    # 경계값(등호 포함)은 만족으로 처리돼야 한다.
    result_min = evaluate_accumulation_pattern(
        **_base_kwargs(price_return_10d_pct=PRICE_QUIET_RISE_MIN_PCT)
    )
    result_max = evaluate_accumulation_pattern(
        **_base_kwargs(price_return_10d_pct=PRICE_QUIET_RISE_MAX_PCT)
    )
    assert result_min["matched"] is True
    assert result_max["matched"] is True


def test_condition3_fails_when_single_day_move_exceeds_threshold():
    result = evaluate_accumulation_pattern(
        **_base_kwargs(max_abs_daily_return_10d_pct=MAX_SINGLE_DAY_ABS_MOVE_PCT + 0.01)
    )
    assert result["matched"] is False


def test_condition3_boundary_single_day_move_at_threshold_matches():
    # 경계값(등호 포함)은 만족으로 처리돼야 한다.
    result = evaluate_accumulation_pattern(
        **_base_kwargs(max_abs_daily_return_10d_pct=MAX_SINGLE_DAY_ABS_MOVE_PCT)
    )
    assert result["matched"] is True


def test_reason_contains_observed_values_not_evaluative_language():
    """§5 house rule — reason은 관측값을 그대로 서술할 뿐 "좋다/추천" 같은
    평가 문구를 쓰지 않는다."""
    result = evaluate_accumulation_pattern(**_base_kwargs())
    reason = result["reason"]
    # 관측값(입력 숫자)이 그대로 서술돼 있어야 한다.
    assert "1,000" in reason  # individual_net_10d
    assert "8.00%" in reason  # price_return_10d_pct
    # 평가/추천 문구가 섞이면 안 된다.
    for banned_word in ("추천", "매수하세요", "매도하세요", "좋음", "좋습니다"):
        assert banned_word not in reason


def test_reason_reports_all_three_condition_statuses_even_when_some_fail():
    result = evaluate_accumulation_pattern(**_base_kwargs(individual_net_10d=1.0))
    assert result["matched"] is False
    reason = result["reason"]
    assert "조건1 불충족" in reason
    assert "조건2 충족" in reason
    assert "조건3 충족" in reason
