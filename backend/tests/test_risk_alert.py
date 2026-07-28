"""Unit tests for app.quant.risk_alert (PLAN.md §5.36, 시장 위험 경보 상단 배너).

Same no-DB/no-network philosophy as tests/test_volume_surge.py — 순수 함수라
`index-tiles/live`가 만드는 kospi/kosdaq/futures dict 모양을 그대로 직접
구성해서 넘긴다.
"""

from __future__ import annotations

from app.quant import risk_alert


def _tile(change_rate: float | None, volume_surge: dict | None = None) -> dict:
    return {"close": 100.0, "change_rate": change_rate, "volume_surge": volume_surge}


def _vs(multiple: float) -> dict:
    return {
        "recent_minutes": 5,
        "baseline_minutes": 30,
        "recent_avg_volume": 100.0,
        "baseline_avg_volume": 100.0,
        "multiple": multiple,
    }


# --- 서킷브레이커 경계값 ---------------------------------------------------


def test_no_risk_when_flat():
    result = risk_alert.classify_market_risk(_tile(0.0), _tile(0.1), _tile(0.0))
    assert result["kospi"]["circuit_breaker_level"] == 0
    assert result["kosdaq"]["circuit_breaker_level"] == 0
    assert result["alerts"] == []
    assert result["has_data"] is True


def test_circuit_breaker_does_not_trigger_on_upside():
    # 상승 8%는 서킷브레이커 대상이 아니다(하락 전용 제도) — 모듈 docstring 참고.
    result = risk_alert.classify_market_risk(_tile(8.0), None, None)
    assert result["kospi"]["circuit_breaker_level"] == 0
    assert result["alerts"] == []


def test_circuit_breaker_level_1_exact_boundary():
    result = risk_alert.classify_market_risk(_tile(-8.0), None, None)
    assert result["kospi"]["circuit_breaker_level"] == 1


def test_circuit_breaker_level_1_just_under_boundary_does_not_trigger():
    result = risk_alert.classify_market_risk(_tile(-7.9), None, None)
    assert result["kospi"]["circuit_breaker_level"] == 0
    assert result["alerts"] == []


def test_circuit_breaker_level_2_exact_boundary():
    result = risk_alert.classify_market_risk(_tile(-15.0), None, None)
    assert result["kospi"]["circuit_breaker_level"] == 2


def test_circuit_breaker_level_2_just_under_boundary_stays_level_1():
    result = risk_alert.classify_market_risk(_tile(-14.9), None, None)
    assert result["kospi"]["circuit_breaker_level"] == 1


def test_circuit_breaker_level_3_exact_boundary():
    result = risk_alert.classify_market_risk(_tile(-20.0), None, None)
    assert result["kospi"]["circuit_breaker_level"] == 3


def test_circuit_breaker_level_3_just_under_boundary_stays_level_2():
    result = risk_alert.classify_market_risk(_tile(-19.9), None, None)
    assert result["kospi"]["circuit_breaker_level"] == 2


def test_circuit_breaker_evaluated_independently_for_kospi_and_kosdaq():
    result = risk_alert.classify_market_risk(_tile(-20.0), _tile(-1.0), None)
    assert result["kospi"]["circuit_breaker_level"] == 3
    assert result["kosdaq"]["circuit_breaker_level"] == 0
    kinds = [(a["kind"], a["market"], a["level"]) for a in result["alerts"]]
    assert kinds == [("circuit_breaker", "kospi", 3)]


# --- 사이드카(코스피) --------------------------------------------------------


def test_kospi_sidecar_exact_boundary_positive_direction():
    result = risk_alert.classify_market_risk(_tile(0.0), _tile(0.0), _tile(5.0))
    assert result["kospi_sidecar"] == {
        "supported": True,
        "evaluable": True,
        "active": True,
        "change_rate": 5.0,
    }


def test_kospi_sidecar_exact_boundary_negative_direction():
    result = risk_alert.classify_market_risk(_tile(0.0), _tile(0.0), _tile(-5.0))
    assert result["kospi_sidecar"]["active"] is True


def test_kospi_sidecar_just_under_boundary_does_not_trigger():
    result = risk_alert.classify_market_risk(_tile(0.0), _tile(0.0), _tile(4.9))
    assert result["kospi_sidecar"]["active"] is False


def test_kospi_sidecar_is_distinct_trigger_from_index_circuit_breaker():
    # 코스피 지수가 -8%(CB1)이면서 K200 선물은 -5%(사이드카)인 상황 — 서로 다른
    # 상품·다른 조건이라 둘 다 독립적으로 활성화돼야 한다(모듈 docstring 참고).
    result = risk_alert.classify_market_risk(_tile(-8.0), _tile(0.0), _tile(-5.0))
    assert result["kospi"]["circuit_breaker_level"] == 1
    assert result["kospi_sidecar"]["active"] is True
    kinds = {(a["kind"], a["market"]) for a in result["alerts"]}
    assert ("circuit_breaker", "kospi") in kinds
    assert ("sidecar", "kospi") in kinds


def test_kosdaq_sidecar_always_unsupported():
    result = risk_alert.classify_market_risk(_tile(0.0), _tile(0.0), _tile(0.0))
    assert result["kosdaq_sidecar"]["supported"] is False
    assert "reason" in result["kosdaq_sidecar"]
    # "확인했는데 문제 없음"으로 오인될 수 있는 false 단독 반환이 아니어야 한다.
    assert "active" not in result["kosdaq_sidecar"]


def test_kospi_sidecar_not_evaluable_when_futures_missing():
    result = risk_alert.classify_market_risk(_tile(0.0), _tile(0.0), None)
    assert result["kospi_sidecar"]["evaluable"] is False
    assert result["kospi_sidecar"]["active"] is False
    assert result["kospi_sidecar"]["change_rate"] is None


# --- 거래량 급증 -------------------------------------------------------------


def test_volume_surge_alert_above_threshold():
    result = risk_alert.classify_market_risk(_tile(0.0, _vs(3.0)), _tile(0.0), None)
    assert result["kospi"]["volume_surge_multiple"] == 3.0
    assert result["kospi"]["volume_surge_alert"] is True
    assert [(a["kind"], a["market"]) for a in result["alerts"]] == [("volume_surge", "kospi")]


def test_volume_surge_below_threshold_no_alert():
    result = risk_alert.classify_market_risk(_tile(0.0, _vs(2.99)), _tile(0.0), None)
    assert result["kospi"]["volume_surge_alert"] is False
    assert result["alerts"] == []


def test_volume_surge_none_means_insufficient_data_not_no_surge():
    # bars 부족(compute_volume_surge가 None) -> "급증 아님"이 아니라 "판정 불가".
    result = risk_alert.classify_market_risk(_tile(0.0, None), _tile(0.0), None)
    assert result["kospi"]["volume_surge_multiple"] is None
    assert result["kospi"]["volume_surge_alert"] is False


# --- None 입력 / 데이터 없음 --------------------------------------------------


def test_all_none_inputs_do_not_crash_and_report_no_data():
    result = risk_alert.classify_market_risk(None, None, None)
    assert result["has_data"] is False
    assert result["alerts"] == []
    assert result["kospi"]["circuit_breaker_evaluable"] is False
    assert result["kospi"]["circuit_breaker_level"] == 0
    assert result["kosdaq"]["circuit_breaker_evaluable"] is False
    assert result["kospi_sidecar"]["evaluable"] is False


def test_has_data_true_when_only_one_market_present():
    result = risk_alert.classify_market_risk(_tile(0.0), None, None)
    assert result["has_data"] is True


def test_change_rate_none_treated_as_not_evaluable_not_zero_risk():
    # tile 자체는 있지만 change_rate가 None인 경우(예: prev_close 조회 실패) —
    # 0단계(하락 없음 확인)로 오인되면 안 되고 evaluable=False로 구분돼야 한다.
    result = risk_alert.classify_market_risk(_tile(None), _tile(0.0), None)
    assert result["kospi"]["circuit_breaker_evaluable"] is False
    assert result["kospi"]["circuit_breaker_level"] == 0


# --- 심각도 정렬(서킷브레이커 > 사이드카 > 거래량 급증) ------------------------


def test_alerts_sorted_by_severity_circuit_breaker_beats_sidecar_and_volume_surge():
    kospi = _tile(-8.0, _vs(5.0))  # CB1 + 거래량 급증 동시 활성
    kosdaq = _tile(0.0)
    futures = _tile(5.0)  # 사이드카 활성
    result = risk_alert.classify_market_risk(kospi, kosdaq, futures)

    kinds = [a["kind"] for a in result["alerts"]]
    assert kinds[0] == "circuit_breaker"
    assert "sidecar" in kinds
    assert "volume_surge" in kinds
    assert kinds.index("circuit_breaker") < kinds.index("sidecar") < kinds.index("volume_surge")


def test_alerts_sorted_higher_circuit_breaker_level_wins_regardless_of_market():
    # 코스닥 CB3(가장 심각)가 코스피 CB1보다 항상 먼저 와야 한다 — "시장 무관,
    # 단계가 곧 심각도" 원칙(모듈 docstring "심각도 점수" 절).
    result = risk_alert.classify_market_risk(_tile(-8.0), _tile(-20.0), None)
    assert result["alerts"][0]["market"] == "kosdaq"
    assert result["alerts"][0]["level"] == 3
    assert result["alerts"][1]["market"] == "kospi"
    assert result["alerts"][1]["level"] == 1


# --- 수급 가속도(기울기) 경보 (§5.36-4, 2026-07-28 같은 대화 중 추가 요청) -------


def _accel(recent_velocity: float, prior_velocity: float) -> dict:
    return {
        "window_minutes": 30,
        "recent_velocity": recent_velocity,
        "prior_velocity": prior_velocity,
        "acceleration": recent_velocity - prior_velocity,
    }


def _flow_accels(
    kospi_foreign: dict | None = None,
    kospi_inst: dict | None = None,
    kosdaq_foreign: dict | None = None,
    kosdaq_inst: dict | None = None,
) -> dict:
    return {
        "kospi": {"외국인": kospi_foreign, "기관계": kospi_inst},
        "kosdaq": {"외국인": kosdaq_foreign, "기관계": kosdaq_inst},
    }


def test_flow_slope_alert_fires_at_exact_multiple_boundary():
    # 직전 30분 매도속도 -10, 최근 30분 매도속도 -20 -> 정확히 2.0배(경계값).
    flow_accels = _flow_accels(kospi_foreign=_accel(-20.0, -10.0))
    result = risk_alert.classify_market_risk(_tile(0.0), _tile(0.0), None, flow_accels)
    assert result["flow_slope"]["kospi"]["외국인"]["alert"] is True
    kinds = [(a["kind"], a["market"], a["investor"]) for a in result["alerts"]]
    assert ("flow_slope", "kospi", "외국인") in kinds


def test_flow_slope_alert_does_not_fire_just_under_boundary():
    flow_accels = _flow_accels(kospi_foreign=_accel(-19.9, -10.0))
    result = risk_alert.classify_market_risk(_tile(0.0), _tile(0.0), None, flow_accels)
    assert result["flow_slope"]["kospi"]["외국인"]["alert"] is False
    assert result["alerts"] == []


def test_flow_slope_does_not_fire_when_prior_velocity_is_zero():
    # prior_velocity==0 -> 배수가 무한대로 발산하는 경계 케이스, 크래시 없이
    # "경보 아님"으로 처리돼야 한다(모듈 docstring "0으로 나누는 예외 회피" 절).
    flow_accels = _flow_accels(kospi_foreign=_accel(-50.0, 0.0))
    result = risk_alert.classify_market_risk(_tile(0.0), _tile(0.0), None, flow_accels)
    assert result["flow_slope"]["kospi"]["외국인"]["alert"] is False
    assert result["flow_slope"]["kospi"]["외국인"]["evaluable"] is True
    assert result["alerts"] == []


def test_flow_slope_does_not_fire_on_reversal_prior_positive():
    # prior_velocity > 0(매수 중이었다가 매도로 전환)인 방향 전환 케이스는
    # 배수 비교가 무의미해 이번 스코프에서 제외한다(PLAN.md §5.36-4).
    flow_accels = _flow_accels(kospi_foreign=_accel(-50.0, 10.0))
    result = risk_alert.classify_market_risk(_tile(0.0), _tile(0.0), None, flow_accels)
    assert result["flow_slope"]["kospi"]["외국인"]["alert"] is False
    assert result["alerts"] == []


def test_flow_slope_does_not_fire_when_recent_velocity_positive():
    # 최근 구간이 매수(+)면 "매도 속도가 빨라짐"이 아니므로 경보 대상이 아니다.
    flow_accels = _flow_accels(kospi_foreign=_accel(50.0, -10.0))
    result = risk_alert.classify_market_risk(_tile(0.0), _tile(0.0), None, flow_accels)
    assert result["flow_slope"]["kospi"]["외국인"]["alert"] is False
    assert result["alerts"] == []


def test_flow_slope_none_acceleration_is_not_evaluable_and_does_not_crash():
    # 4개 조합 모두 None(예: 데이터 부족) -> 크래시 없이 evaluable=False로
    # 구분돼야 한다(circuit_breaker_evaluable 등 기존 판정 불가 표시와 동일한
    # 정직성 원칙).
    result = risk_alert.classify_market_risk(_tile(0.0), _tile(0.0), None, None)
    for market in ("kospi", "kosdaq"):
        for investor in ("외국인", "기관계"):
            classified = result["flow_slope"][market][investor]
            assert classified["evaluable"] is False
            assert classified["alert"] is False
    assert result["alerts"] == []


def test_flow_slope_default_param_omitted_behaves_like_none():
    # flow_accelerations 인자를 아예 생략해도(하위호환 기본값) 크래시 없이
    # "이 차원은 평가 안 함" 상태를 반환해야 한다 — 기존(§5.36-1/2/3) 호출부가
    # 이 인자를 전혀 모른 채로도 계속 동작해야 하기 때문.
    result = risk_alert.classify_market_risk(_tile(0.0), _tile(0.0), None)
    assert result["flow_slope"]["kospi"]["외국인"]["evaluable"] is False
    assert result["alerts"] == []


def test_flow_slope_severity_ranks_below_circuit_breaker_and_sidecar():
    # CB1 + 사이드카 + 기울기 경보가 동시에 활성화돼도 CB/사이드카가 항상
    # 기울기보다 먼저 와야 한다(공식 제도 > 자체 관찰 휴리스틱, PLAN.md
    # §5.36-4 "심각도는 사이드카/서킷브레이커보다 낮고" 절).
    flow_accels = _flow_accels(kospi_foreign=_accel(-20.0, -10.0))
    result = risk_alert.classify_market_risk(_tile(-8.0), _tile(0.0), _tile(5.0), flow_accels)

    kinds = [a["kind"] for a in result["alerts"]]
    assert kinds[0] == "circuit_breaker"
    assert "sidecar" in kinds
    assert "flow_slope" in kinds
    assert kinds.index("circuit_breaker") < kinds.index("flow_slope")
    assert kinds.index("sidecar") < kinds.index("flow_slope")


def test_flow_slope_alert_includes_expected_fields():
    flow_accels = _flow_accels(kosdaq_inst=_accel(-30.0, -10.0))
    result = risk_alert.classify_market_risk(_tile(0.0), _tile(0.0), None, flow_accels)
    alert = result["alerts"][0]
    assert alert["kind"] == "flow_slope"
    assert alert["market"] == "kosdaq"
    assert alert["investor"] == "기관계"
    assert alert["recent_velocity"] == -30.0
    assert alert["prior_velocity"] == -10.0
    assert alert["multiple"] == 3.0
    assert "severity" in alert
