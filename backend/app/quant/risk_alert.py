"""시장 위험 경보 판정 — PLAN.md §5.36(2026-07-28, 사용자 요청: "하락폭 혹은
볼륨이 크면 뭔가 이슈를 알려줘야 하지 않을까? ... 상단 배너로 말야..").

**배경**: 오늘(2026-07-28) 코스피가 전일 대비 -8.1%까지 장중 급락해 서킷브레이커
1단계 발동 기준을 실측으로 확인한 직후, 사용자가 "하락폭·거래량이 크면 상단
배너로 경고해 달라"고 요청했다. 이 모듈은 그 판정 로직(순수 함수, DB/세션
무관)만 담당한다 — `routers/markets.py::_warm_index_tiles_live`가 이미 만든
`index-tiles/live` 응답(코스피/코스닥/선물 각각의 `close`/`change_rate`/
`volume_surge`)을 그대로 입력받아 위험 등급을 매길 뿐, 새 외부 호출이나 새
DB 테이블은 전혀 필요 없다.

**핵심 정직성 원칙 — "발동되었다"고 단정하지 않는다**: 서킷브레이커와 사이드카는
KRX가 실제로 운영하는 공식 제도이고, 이 모듈이 쓰는 8%/15%/20%/±5% 임계값도
임의로 정한 게 아니라 그 공식 규정 수치를 그대로 가져온 것이다. 하지만 실제
제도는 그 임계값 이탈이 "통상 1분간 지속"되어야 KRX가 공식으로 발동을
선언하는 구조인 반면, 이 프로젝트는 1분봉 종가 스냅샷만 가지고 있을 뿐 KRX의
실제 발동 선언(정확한 지속시간 판정, 정확한 발동 시각)을 수신하는 공식
채널이 아니다. 그래서 이 모듈의 반환값과 그걸 소비하는 모든 하위 계층
(API 응답, 프런트 문구)은 반드시 "발동 기준에 해당하는 변동폭이 관찰됨"
같은 관찰형 서술만 써야 하며, "서킷브레이커가 발동되었습니다"처럼 공식
이벤트가 실제로 일어났다고 단정하는 문구를 절대 쓰지 않는다(§5.36 설계
"정직한 표시" 절, `quant/volume_profile.py`의 "근사치 — 예측 아님",
`quant/sector_rotation.py`의 "판정 없음" 원칙과 동일한 house rule).

**서킷브레이커는 하락 전용 제도다**: 실제 KRX 규정도 지수가 "하락"할 때만
발동하는 편측(one-sided) 제도라 상승 8%는 절대 서킷브레이커 대상이 아니다 —
이 모듈은 `change_rate < 0`(하락)일 때만 단계를 매기고, 상승은 항상 0단계로
취급한다.

**사이드카(코스피)와 서킷브레이커(코스피 지수)는 서로 다른 트리거다**: 서킷
브레이커는 코스피/코스닥 "지수" 자체의 전일 대비 하락률을 보지만, 사이드카는
코스피200 선물(K200 선물) 가격의 기준가 대비 변동률(방향 무관, ±)을 본다 —
같은 "코스피" 계열이라도 완전히 다른 상품·다른 조건이라 이 모듈은 절대 하나로
합치지 않고 별도 필드(`kospi_sidecar`)로 분리해 반환한다.

**코스닥 사이드카는 "판정 불가"다 — 조용히 생략하지 않는다**: 코스닥 사이드카는
실제로는 코스닥150 선물 가격 기준인데, 이 프로젝트엔 코스닥150 선물 데이터가
전혀 없다(조사 완료, `backend/app/`, 특히 `clients/`·`collectors/` 전체에
코스닥150 선물 소스가 없음). 그래서 `kosdaq_sidecar`는 `active: False`처럼
"확인했는데 이상 없음"으로 보이는 값을 절대 반환하지 않고, 항상
`{"supported": False, "reason": ...}` 형태로 "애초에 판정할 수 없다"는 사실
자체를 명시적으로 드러낸다.

**거래량 급증은 KRX 공식 제도가 아니다**: `quant/volume_surge.py`(§5.24)가
계산하는 "최근 5분 평균 거래량이 직전 30분 평균 대비 몇 배인지"는 이
프로젝트가 자체적으로 정한 휴리스틱일 뿐, 서킷브레이커·사이드카처럼 KRX
규정에 근거한 값이 아니다. 그래서 이 모듈은 거래량 급증 판정에 쓰는 배율
임계값(`VOLUME_SURGE_ALERT_MULTIPLE`)에 별도 이름을 붙이고, 반환값에서도
서킷브레이커/사이드카와 뒤섞이지 않도록 별개 필드로 분리해 하위 계층이
"공식 제도"인 것처럼 착각하지 않게 한다. 봉이 부족해 `compute_volume_surge`가
`None`을 반환한 경우("판정 불가")와 배율이 임계값 미만인 경우("판정했고
급증 아님")를 구분하기 위해 `volume_surge_multiple`(원본 값, 판정 불가 시
`None`)과 `volume_surge_alert`(bool)를 분리해 반환한다.

**입력 데이터가 없을 때("판정 불가")와 "판정했더니 이상 없음"을 구분한다**:
장 마감 직후 라이브 호출이 실패해 `kospi`/`kosdaq`/`futures` 중 하나(또는
전부)가 `None`으로 들어올 수 있다. 이때 서킷브레이커 단계를 억지로 0으로
채우면 "0단계=하락 없음을 확인함"과 "애초에 데이터가 없어 확인 자체를
못함"이 겉보기에 똑같아져 코스닥 사이드카에서 지켜낸 것과 같은 정직성
원칙이 깨진다. 그래서 각 시장 결과에 `circuit_breaker_evaluable`(사이드카는
`evaluable`) 플래그를 별도로 둬서, `level`/`active`가 0/False이더라도
"실제로 데이터를 보고 판정한 결과"인지 "데이터가 없어 판정 자체가
불가능했던 기본값"인지 항상 구분할 수 있게 한다.

**추가(§5.36-4, 2026-07-28 같은 대화 중 추가 요청)**: 사용자 — "평소보다
외인 혹은 기관의 기울기가 낮으면 위험 경고를 보내주는것도 추가해봐.. 투자자별
수급 요약을 활용하면 되지 않을까해..". "기울기"는 §5.17
(`quant/flow_acceleration.py`)이 이미 계산해 둔 `recent_velocity`/
`prior_velocity`(각 30분 구간 순매수 변화량, `intraday_sample` 재사용, 새
수집 없음)와 정확히 같은 개념이라 새 지표를 만들지 않고 그 값을 그대로
재분류한다.

**§5.17 "절대 `_judge_regime`에 섞지 않는다" 원칙과의 관계**:
`flow_acceleration.py` 모듈 docstring은 가속도가 "아직 정규화/검증 안 된
신호"라 방향성 종합 판정(`routers.markets._judge_regime` — 코스닥우세/
코스피우세 같은 매매 신호에 가까운 판정)에는 절대 섞지 말라고 명시했다. 이
원칙은 이 모듈에서도 그대로 유지한다 — `_judge_regime`을 수정하지 않고,
그 함수를 전혀 건드리지도 않는다. 이번에 추가하는 소비처는 그 종합판정이
아니라 완전히 다른 성격의 소비처, 즉 이미 이 모듈이 제공하는 "순수 관찰
알림"(서킷브레이커/사이드카/거래량 급증과 같은 급의 위험 배너)이다 —
"매수/매도 중 어느 쪽이 우세한가"를 판정하는 방향성 신호가 아니라 "방금
매도 속도가 평소보다 빨라졌다"는 사실만 알리는 것이라 §5.17 원칙과 충돌하지
않는다. `flow_acceleration.py`가 스스로 밝힌 "아직 정규화/검증 안 된 신호"라는
한계도 그대로 계승한다 — 거래량 급증(§5.24, 이 역시 백테스트 검증 이력 없는
자체 휴리스틱)과 동일하게 "공식 제도 아님, 검증 안 됨, 참고용" 문구를 하위
계층(API 응답, 프런트 문구)에서 반드시 유지해야 하고, "매도가 계속될
것"처럼 예측성 주장을 하는 문구는 절대 쓰지 않는다.

경보 조건: 두 구간 모두 매도 중(`recent_velocity < 0`이고
`prior_velocity < 0`)이며 최근 30분 매도 속도가 직전 30분의
`FLOW_SLOPE_ALERT_MULTIPLE`배 이상으로 빨라지면 경보. 매수->매도 같은 방향
전환 케이스(`prior_velocity > 0`, `recent_velocity < 0`)는 부호가 다른 두
값의 배수 비교가 의미가 없어 이번 스코프에서 제외한다(PLAN.md §5.36-4
"단순함 우선, 필요하면 나중에 별도 추가" — `_classify_flow_slope` 참고).
"""

from __future__ import annotations

# 서킷브레이커 단계별 하락률 기준(KRX 공식 규정 수치, 전일 종가 대비, 통상 1분
# 지속 시 발동) — 코스피/코스닥 공통. 1단계=매매거래 20분 중단, 2단계=매매거래
# 20분 중단, 3단계=당일 장 종료(실제 정지 시간·절차는 이 프로젝트가 알 필요
# 없는 KRX 운영 세부사항이라 여기선 임계값만 다룬다).
CIRCUIT_BREAKER_LEVEL_1_PCT = 8.0
CIRCUIT_BREAKER_LEVEL_2_PCT = 15.0
CIRCUIT_BREAKER_LEVEL_3_PCT = 20.0

# 사이드카(프로그램매매 호가효력 정지) 기준 — 코스피는 K200 선물이 기준가 대비
# ±5%(방향 무관) 변동, 통상 1분 지속 시 프로그램매매 호가효력 5분 정지(KRX
# 공식 규정 수치). 코스닥은 코스닥150 선물 ±6% 기준이 실제로 존재하지만, 이
# 프로젝트엔 코스닥150 선물 데이터가 없어 판정 자체가 불가능하다(모듈 docstring
# 참고) — 그래서 코스닥 쪽 임계값 상수는 정의하지 않는다(쓸 데가 없는데 상수만
# 만들면 "지원하는 척"하는 것이라 오히려 정직성 원칙에 어긋난다).
KOSPI_SIDECAR_PCT = 5.0

# 거래량 급증 경보 배율 — KRX 공식 제도가 아니라 이 프로젝트가 자체적으로 정한
# 휴리스틱 임계값이다(§5.24 `quant/volume_surge.py`가 계산하는 `multiple`,
# "최근 5분 평균 거래량 / 직전 30분 평균 거래량"에 적용). 3배 이상이면 "평소
# 대비 눈에 띄게 튀었다"고 볼 만한 수준이라는 판단 — 백테스트로 검증된 신호가
# 아니므로(§5.15/§5.23의 "판정 전 반드시 백테스트" 원칙과 별개로, 이 배지는
# 애초에 "매매 신호"가 아니라 "관찰 알림"이라 그 검증 대상이 아니다) 향후
# 조정될 수 있는 값이라는 점을 이름으로 명시한다.
VOLUME_SURGE_ALERT_MULTIPLE = 3.0

# 수급 가속도(기울기) 경보 배율 — PLAN.md §5.36-4(2026-07-28 같은 대화 중 추가
# 요청). §5.17 `quant/flow_acceleration.py`가 이미 계산해 둔
# `recent_velocity`/`prior_velocity`(각 30분 구간 순매수 변화량)를 그대로
# 재사용한다 — 새 계산·새 수집 없음. 두 구간 모두 매도 중(둘 다 음수)이고
# 최근 구간 매도 속도가 직전 구간의 이 배수 이상으로 빨라지면 경보.
# VOLUME_SURGE_ALERT_MULTIPLE과 마찬가지로 백테스트로 검증된 값이 아니라 이
# 프로젝트가 임의로 정한 휴리스틱이다(이름을 "..._ALERT_MULTIPLE"로 맞춰
# 같은 검증 등급임을 드러낸다) — 향후 실측으로 조정될 수 있다.
FLOW_SLOPE_ALERT_MULTIPLE = 2.0

# 경보 종류별 심각도 점수 — 프런트가 "여러 조건이 동시에 활성화됐을 때 가장
# 심각한 걸 헤드라인으로" 고를 수 있도록 이 모듈이 직접 정렬까지 해서
# `alerts` 필드로 반환한다(판정 로직과 표시 우선순위를 한 곳에 모아 두는 게
# 프런트·백엔드 두 곳에 같은 규칙을 중복 구현하는 것보다 안전하다). 서킷
# 브레이커는 단계가 높을수록, 그리고 사이드카·거래량 급증보다 항상 우선한다는
# PLAN.md §5.36 설계("서킷브레이커 3>2>1 > 사이드카 > 거래량 급증")를 그대로
# 숫자로 인코딩한다.
_SEVERITY_CIRCUIT_BREAKER_BASE = 10  # 1단계->11, 2단계->12, 3단계->13
_SEVERITY_SIDECAR = 5
_SEVERITY_VOLUME_SURGE = 1

# 수급 가속도(기울기) 경보 심각도 — 거래량 급증(§5.24)과 정확히 같은 값을
# 준다. 두 경보 모두 KRX 공식 제도가 아니라 이 프로젝트가 자체적으로 정한
# 미검증 휴리스틱이라는 같은 신뢰 등급이고, 서킷브레이커/사이드카(공식
# 제도)보다는 항상 낮아야 한다는 것만 확실할 뿐, 거래량 급증과 기울기 중
# 어느 쪽이 더 급박한지 판단할 근거가 없다 — 근거 없이 순서를 만들면
# "정한 사람 마음대로"가 되므로 동률로 둔다(PLAN.md §5.36-4 "정확한 상대
# 순서는 구현 시 판단"에 대한 답).
_SEVERITY_FLOW_SLOPE = _SEVERITY_VOLUME_SURGE

# §5.36-4가 평가하는 시장 x 투자자 조합 — `routers/markets.py`의
# `REGIME_MARKETS`/`REGIME_INVESTORS`와 정확히 같은 4개 조합이다(사용자
# 요청 "외인 혹은 기관" — 개인은 의도적으로 제외). 이 모듈은 순수 계산
# 모듈로 `routers/markets.py`를 import하면 그쪽이 이 모듈을 import하는
# 관계와 순환 참조가 생기므로, 상수를 각자 모듈에 독립적으로 정의해 둔다.
_FLOW_SLOPE_MARKETS = ("kospi", "kosdaq")
_FLOW_SLOPE_INVESTORS = ("외국인", "기관계")


def _circuit_breaker_level(change_rate: float) -> int:
    """`change_rate`(부호 있는 등락률, %)로 서킷브레이커 단계(0~3)를 판정한다.
    하락 전용 제도라 상승(`change_rate >= 0`)은 항상 0단계다. 경계값은
    "이상"(>=)이다 — 예: 정확히 -8.0%는 1단계, -7.9%는 0단계(모듈 docstring
    "하락 전용" 절, 단위테스트로 경계값 확인)."""
    if change_rate >= 0:
        return 0
    magnitude = abs(change_rate)
    if magnitude >= CIRCUIT_BREAKER_LEVEL_3_PCT:
        return 3
    if magnitude >= CIRCUIT_BREAKER_LEVEL_2_PCT:
        return 2
    if magnitude >= CIRCUIT_BREAKER_LEVEL_1_PCT:
        return 1
    return 0


def _classify_index_market(tile: dict | None) -> dict:
    """kospi/kosdaq 공용 — 서킷브레이커 단계 + 거래량 급증 판정.

    `tile`은 `index-tiles/live`의 kospi/kosdaq 항목 그대로
    (`{"change_rate", "volume_surge": {...}|None, ...}`) 또는 데이터 자체가
    없으면 `None`."""
    change_rate = tile.get("change_rate") if tile else None
    evaluable = change_rate is not None
    level = _circuit_breaker_level(change_rate) if evaluable else 0

    volume_surge = (tile or {}).get("volume_surge")
    multiple = volume_surge.get("multiple") if volume_surge else None
    alert = multiple is not None and multiple >= VOLUME_SURGE_ALERT_MULTIPLE

    return {
        "change_rate": change_rate,
        "circuit_breaker_level": level,
        "circuit_breaker_evaluable": evaluable,
        "volume_surge_multiple": multiple,
        "volume_surge_alert": alert,
    }


def _classify_kospi_sidecar(futures: dict | None) -> dict:
    """코스피 사이드카 — K200 선물 `change_rate`(방향 무관) 절댓값이
    `KOSPI_SIDECAR_PCT` 이상이면 `active: True`."""
    change_rate = futures.get("change_rate") if futures else None
    evaluable = change_rate is not None
    active = evaluable and abs(change_rate) >= KOSPI_SIDECAR_PCT
    return {
        "supported": True,
        "evaluable": evaluable,
        "active": active,
        "change_rate": change_rate,
    }


def _kosdaq_sidecar_unsupported() -> dict:
    """코스닥 사이드카 — 코스닥150 선물 데이터 소스가 이 프로젝트에 없어 판정
    자체가 불가능하다(모듈 docstring 참고). `active: False`처럼 "확인했는데
    이상 없음"으로 보이는 값을 반환하면 "판정 불가"를 "판정했고 문제 없음"으로
    거짓 표시하는 셈이라 절대 그렇게 하지 않는다."""
    return {
        "supported": False,
        "reason": (
            "코스닥150 선물 데이터 소스가 이 프로젝트에 없어 코스닥 사이드카는 "
            "판정할 수 없음(코스피와 달리 지원 대상 아님, PLAN.md §5.36 조사 결과)"
        ),
    }


def _classify_flow_slope(market: str, investor: str, acceleration: dict | None) -> dict:
    """market x investor 한 조합의 수급 가속도(기울기) 경보 판정(PLAN.md
    §5.36-4). `acceleration`은 `flow_acceleration.compute_flow_acceleration`의
    반환값 그대로(`{"recent_velocity", "prior_velocity", ...}`) 또는 필요한
    세 시점 중 하나라도 데이터가 없어 `None`.

    **§5.17 원칙과의 관계**: `flow_acceleration.py` 모듈 docstring은 이 값을
    방향성 종합 판정(`routers.markets._judge_regime`)에 절대 섞지 말라고
    명시했다 — 이 함수는 그 판정이 아니라 §5.36의 관찰용 위험 배너에만 쓰이는
    별도 소비처이므로 그 원칙과 충돌하지 않는다(모듈 docstring 참고). 여기서
    나온 결과를 노출하는 모든 하위 계층은 "매도가 계속될 것"같은 예측을
    주장해선 안 되고, 방금까지 관찰된 사실만 서술해야 한다.

    경보 조건: `recent_velocity < 0`이고 `prior_velocity < 0`(둘 다 매도
    구간)이며 `abs(recent_velocity) >= FLOW_SLOPE_ALERT_MULTIPLE *
    abs(prior_velocity)`(매도 속도가 그 배수 이상 빨라짐)이면 `alert: True`.
    `prior_velocity`가 0 이상(매수 중이었거나 변화 없음)인 경우는 "매수->매도
    방향 전환" 또는 "변화 없음" 케이스인데, 부호가 다른 두 값의 배수 비교는
    해석이 불가능해 이번 스코프에서는 다루지 않는다(PLAN.md §5.36-4 "단순함
    우선, 필요하면 나중에 별도 추가") — `prior_velocity < 0`을 조건에 그대로
    두면 0인 경우도 자연스럽게 걸러져(0은 0보다 작지 않음) 0으로 나누는
    예외도 별도 분기 없이 함께 피해진다."""
    evaluable = acceleration is not None
    recent_velocity = acceleration.get("recent_velocity") if acceleration else None
    prior_velocity = acceleration.get("prior_velocity") if acceleration else None

    alert = False
    if evaluable and recent_velocity < 0 and prior_velocity < 0:
        alert = abs(recent_velocity) >= FLOW_SLOPE_ALERT_MULTIPLE * abs(prior_velocity)

    return {
        "market": market,
        "investor": investor,
        "evaluable": evaluable,
        "recent_velocity": recent_velocity,
        "prior_velocity": prior_velocity,
        "alert": alert,
    }


def _build_alerts(
    kospi_c: dict, kosdaq_c: dict, kospi_sidecar: dict, flow_slopes: list[dict]
) -> list[dict]:
    """활성 조건만 심각도 내림차순으로 모은 리스트 — 프런트는 `alerts[0]`을
    헤드라인, 나머지를 보조 텍스트로 쓰면 된다(모듈 상단 "경보 종류별 심각도
    점수" 절 참고). 서킷브레이커 0단계·사이드카 비활성·거래량 급증/기울기
    미달은 "활성 조건"이 아니므로 리스트에 아예 넣지 않는다(호출자가 매번
    level>0 체크를 반복하지 않도록). `flow_slopes`는 `_classify_flow_slope`
    반환값들의 리스트(시장 x 투자자 조합 개수만큼, 순서 무관 — 심각도로 다시
    정렬한다)."""
    alerts: list[dict] = []

    for market, classified in (("kospi", kospi_c), ("kosdaq", kosdaq_c)):
        level = classified["circuit_breaker_level"]
        if classified["circuit_breaker_evaluable"] and level > 0:
            alerts.append(
                {
                    "kind": "circuit_breaker",
                    "market": market,
                    "severity": _SEVERITY_CIRCUIT_BREAKER_BASE + level,
                    "level": level,
                    "change_rate": classified["change_rate"],
                }
            )

    if kospi_sidecar["evaluable"] and kospi_sidecar["active"]:
        alerts.append(
            {
                "kind": "sidecar",
                "market": "kospi",
                "severity": _SEVERITY_SIDECAR,
                "level": None,
                "change_rate": kospi_sidecar["change_rate"],
            }
        )

    for market, classified in (("kospi", kospi_c), ("kosdaq", kosdaq_c)):
        if classified["volume_surge_alert"]:
            alerts.append(
                {
                    "kind": "volume_surge",
                    "market": market,
                    "severity": _SEVERITY_VOLUME_SURGE,
                    "level": None,
                    "multiple": classified["volume_surge_multiple"],
                }
            )

    for fs in flow_slopes:
        if fs["evaluable"] and fs["alert"]:
            recent_velocity = fs["recent_velocity"]
            prior_velocity = fs["prior_velocity"]
            alerts.append(
                {
                    "kind": "flow_slope",
                    "market": fs["market"],
                    "investor": fs["investor"],
                    "severity": _SEVERITY_FLOW_SLOPE,
                    "level": None,
                    "recent_velocity": recent_velocity,
                    "prior_velocity": prior_velocity,
                    # prior_velocity는 alert=True일 때 항상 < 0(0이 아님)이라
                    # _classify_flow_slope의 조건에 의해 division-by-zero 걱정 없이
                    # 안전하게 나눌 수 있다.
                    "multiple": abs(recent_velocity / prior_velocity),
                }
            )

    alerts.sort(key=lambda a: a["severity"], reverse=True)
    return alerts


def classify_market_risk(
    kospi: dict | None,
    kosdaq: dict | None,
    futures: dict | None,
    flow_accelerations: dict[str, dict[str, dict | None]] | None = None,
) -> dict:
    """`index-tiles/live`가 이미 만든 코스피/코스닥/선물 3개 dict(각각 `None`
    가능)를 그대로 입력받아 서킷브레이커/사이드카/거래량 급증/수급 가속도
    (기울기) 위험을 판정한다. DB·세션·외부 호출 전혀 없는 순수 함수(§5.17
    `flow_acceleration.py`와 동일한 compute/collect 분리 관례).

    `flow_accelerations`(PLAN.md §5.36-4, 2026-07-28 추가)는
    ``{"kospi": {"외국인": {...}|None, "기관계": {...}|None},
    "kosdaq": {...동일 구조...}}`` 모양 — 각 leaf는
    `flow_acceleration.compute_flow_acceleration`의 반환값 그대로. 생략하면
    (기본값 `None`) "이 차원은 아예 평가하지 않음"으로 취급해 그 4개 조합
    모두 `evaluable: False`로 처리한다 — 기존 호출부(이 함수를 이미 쓰던
    §5.36-1/2/3 코드·테스트)가 이 새 인자 없이도 그대로 동작하게 하기 위한
    하위호환 기본값이다.

    Returns::

        {
            "kospi": {change_rate, circuit_breaker_level(0-3),
                      circuit_breaker_evaluable, volume_surge_multiple,
                      volume_surge_alert},
            "kosdaq": {...동일 구조...},
            "kospi_sidecar": {supported: True, evaluable, active, change_rate},
            "kosdaq_sidecar": {supported: False, reason},
            "flow_slope": {"kospi": {"외국인": {market, investor, evaluable,
                            recent_velocity, prior_velocity, alert}, "기관계": {...}},
                            "kosdaq": {...동일 구조...}},
            "alerts": [{kind, market, severity, level,
                        change_rate|multiple|(investor, recent_velocity,
                        prior_velocity, multiple)}, ...],
            "has_data": bool,
        }

    `alerts`는 이미 심각도 내림차순 정렬(서킷브레이커 3>2>1 > 사이드카 >
    거래량 급증 == 기울기, 모듈 상단 상수 절 참고) — 비어 있으면 "지금
    활성화된 위험 조건 없음"(전부 데이터가 없어 아무것도 판정 못 한 경우도
    포함, `has_data`로 구분).

    입력 3개가 전부 `None`이어도 예외 없이 "아무것도 판정 못 함" 상태
    (`has_data: False`, `alerts: []`)를 반환한다 — 모듈 docstring "판정 불가와
    이상 없음을 구분한다" 절 참고, 크래시는 언제나 최악의 선택이다. `has_data`는
    기존과 동일하게 kospi/kosdaq/futures 3개 기준으로만 판단한다(기울기는
    보조 차원이라 이 플래그의 의미를 바꾸지 않는다)."""
    kospi_c = _classify_index_market(kospi)
    kosdaq_c = _classify_index_market(kosdaq)
    kospi_sidecar = _classify_kospi_sidecar(futures)
    kosdaq_sidecar = _kosdaq_sidecar_unsupported()

    flow_accelerations = flow_accelerations or {}
    flow_slope_by_market: dict[str, dict[str, dict]] = {}
    flow_slopes: list[dict] = []
    for market in _FLOW_SLOPE_MARKETS:
        flow_slope_by_market[market] = {}
        for investor in _FLOW_SLOPE_INVESTORS:
            acceleration = flow_accelerations.get(market, {}).get(investor)
            classified = _classify_flow_slope(market, investor, acceleration)
            flow_slope_by_market[market][investor] = classified
            flow_slopes.append(classified)

    return {
        "kospi": kospi_c,
        "kosdaq": kosdaq_c,
        "kospi_sidecar": kospi_sidecar,
        "kosdaq_sidecar": kosdaq_sidecar,
        "flow_slope": flow_slope_by_market,
        "alerts": _build_alerts(kospi_c, kosdaq_c, kospi_sidecar, flow_slopes),
        "has_data": kospi is not None or kosdaq is not None or futures is not None,
    }
