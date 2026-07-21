"""app/quant/screener.py 순수 함수 단위테스트 (PLAN.md §5.2).

전부 DB/네트워크 무관 — 알려진 입력값으로 기대값을 손계산해 검증한다
(test_quant_signals.py와 동일한 스타일).
"""

from __future__ import annotations

from app.quant.screener import ATTENTION_BONUS, WEIGHTS, compute_scalp_scores


def _cand(code, change_rate, turnover, value_rank, name=None, market="kospi"):
    return {
        "code": code,
        "name": name or code,
        "market": market,
        "change_rate": change_rate,
        "turnover": turnover,
        "value_rank": value_rank,
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
    weights = {"change": 0.5, "turnover": 0.5, "value_rank": 0.0, "attention": 0.0}
    scored = compute_scalp_scores(candidates, attention_codes=set(), weights=weights)

    assert scored[0]["score"] == scored[1]["score"]
    assert [r["code"] for r in scored] == ["B", "A"]


def test_custom_weights_are_respected():
    candidates = [
        _cand("A", change_rate=1.0, turnover=50.0, value_rank=100),
        _cand("B", change_rate=20.0, turnover=1.0, value_rank=1),
    ]
    # turnover만 100% 반영하는 극단 가중치 -> turnover가 큰 A가 1등이어야 한다.
    weights = {"change": 0.0, "turnover": 1.0, "value_rank": 0.0, "attention": 0.0}
    scored = compute_scalp_scores(candidates, attention_codes=set(), weights=weights)

    assert scored[0]["code"] == "A"


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
