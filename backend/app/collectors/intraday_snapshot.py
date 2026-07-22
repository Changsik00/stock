"""장중 수급 스냅샷 인메모리 적립 버퍼 — PLAN.md §5.4-2/5.4-3.

**배경**: 투자자별 수급 요약·외인 양손 상세 모달이 지금까지 EOD(일별) 히스토리
차트만 보여줬다("3M" 토글) — 키움 ka10051에는 분단위 이력 자체가 없어 소스에서
직접 "오늘 장중 추이"를 당겨올 방법이 없다(routers/markets.py 모듈 docstring의
flow/live 절 참고, ka10063/ka10066도 종목별 배열이라 시장 합계에 비용이 크다는
동일한 사정). 그렇다고 이 기능만을 위해 새 외부 API를 두드리는 건 과하다.

**설계**: 이미 `collectors/live_refresh.py`의 60초/7분 잡이 `_warm_flow_live`/
`_warm_futures_flow_live`를 선제적으로 호출해 캐시를 채우고 있다 — 이 모듈은
그 두 warm 함수가 **이미 fetch를 마치고 반환한 값**을 받아서(추가 HTTP/키움/
네이버 호출 없음) 그날그날 메모리 리스트에 순서대로 append만 한다. 즉 이
모듈은 순수 저장소이고, 실제 갱신 트리거는 항상 live_refresh.py 쪽에 있다.

**왜 DB가 아니라 메모리인가**: routers/markets.py 곳곳에 이미 문서화된 §3.5
원칙("장중 값은 DB에 쌓지 않는다" — breadth/live, flow/live, attention 모두
동일)과 같은 이유다. 장중 잠정치는 확정치가 아니고, 하루가 지나면 의미가
없어지는 휘발성 데이터라 스키마 마이그레이션·테이블 부담 없이 프로세스
메모리로 충분하다. 서버가 재시작되면(코드 배포, `--reload` 등) 그날 적립분은
사라지고 다음 warm 호출부터 다시 쌓인다 — 이것도 §3.5 원칙과 일관된 트레이드
오프다.

**자정 리셋**: 진짜 자정 타이머를 두지 않는다. 대신 매 append 호출마다
`app.market_hours.KST` 기준 오늘 날짜를 계산해 `_buffer_date`와 비교하고,
날짜가 달라졌으면(다음 거래일 첫 append 시점) 모든 버퍼를 비우고
`_buffer_date`를 갱신한다 — "다음 append 때 지연 감지"라 자정 그 순간에 정확히
비워지진 않지만(장중에만 append가 발생하므로 실질적으로 다음날 09:00 첫 워밍
때 비워짐), 이 기능 목적("오늘 장중 누적")에는 그걸로 충분하고 별도 스케줄
잡을 추가하는 복잡도를 피한다.

**시리즈별 서로 다른 실제 갱신주기**: `record_flow_snapshot`(개인/외국인/
기관계)은 `_run_live_refresh`(60초 잡)가 부르므로 ~60초마다 점이 찍히고,
`record_futures_flow_snapshot`(외인선물)은 `_run_live_refresh_extra`(7분 잡)가
부르므로 ~7분마다 점이 찍힌다. 두 시리즈를 억지로 같은 틱 간격으로 맞추지
않는다 — PLAN.md §5.4-2에 명시된 의도이지 버그가 아니다(선물 소스 자체가
네이버 트렌드 API로 5~10분 캐시 티어에 속해 있어, 60초로 강제로 늘려 봐야
같은 값을 반복 append하는 것 이상의 의미가 없다).

**500포인트 캡**: 시리즈당 `MAX_POINTS_PER_SERIES`(500)를 넘으면 가장 오래된
포인트부터 버린다. 60초 틱 기준으로도 500포인트면 8시간 20분 분량이라 하루
정규장(6.5시간) 전체를 넉넉히 덮고, 메모리 사용량도 시리즈당 수백 KB 이하로
무시할 만한 수준이다.
"""

from __future__ import annotations

import datetime as dt

from ..market_hours import KST, is_market_closed

MAX_POINTS_PER_SERIES = 500

# flow/live에서 다루는 투자자 3종
_FLOW_INVESTORS = ("개인", "외국인", "기관계")
_FLOW_MARKETS = ("kospi", "kosdaq")

# 시장별 투자자 3종을 각자 따로 적립한다(PLAN.md §5.10 — 코스피/코스닥 분리).
# "외인선물"만 시장 구분이 없는 단일 시리즈로 남는다(코스피200 선물이라 시장별
# 개념이 없음). "등락비율"도 시장 구분 없는 단일 시리즈다(PLAN.md §5.13 —
# 사용자가 원한 건 "오늘 오르는 종목이 많은지"라는 전체 시장 관점이라 코스피/
# 코스닥을 합산해서 하나의 지표로만 적립한다). 시리즈 이름 -> [{"time": "HH:MM",
# "value": float}, ...] (시간순 append)
_buffers: dict[str, dict[str, list[dict[str, object]]] | list[dict[str, object]]] = {
    "kospi": {"개인": [], "외국인": [], "기관계": []},
    "kosdaq": {"개인": [], "외국인": [], "기관계": []},
    "외인선물": [],
    "등락비율": [],
}

# 이 버퍼들이 속한 KST 캘린더 날짜. None이면 아직 한 번도 append되지 않은 상태
# (앱 기동 직후 등) — 이 경우에도 get_*_series()는 오늘 날짜를 보고해야 하므로
# "date" 응답 값은 이 변수가 아니라 매 호출 시점의 오늘 날짜로 별도 계산한다.
_buffer_date: dt.date | None = None


def _today_kst() -> dt.date:
    return dt.datetime.now(KST).date()


def _now_hhmm_kst() -> str:
    return dt.datetime.now(KST).strftime("%H:%M")


def _reset_if_new_day() -> None:
    """오늘 KST 날짜가 마지막 적립 날짜와 다르면 모든 버퍼를 비운다(자정 리셋,
    모듈 docstring 참고). append 계열 함수 진입 시마다 호출한다 — 실시간
    타이머가 아니라 "다음 append 때 지연 감지"하는 방식이라 장중에만 실질적으로
    발동한다. 구조가 시장별로 한 단계 더 깊어져도(§5.10) "모든 series list를
    찾아서 비운다"는 개념은 그대로라, dict 값이 list(외인선물)든 시장별 dict
    (kospi/kosdaq)든 재귀적으로 순회한다."""
    global _buffer_date
    today = _today_kst()
    if _buffer_date != today:
        for value in _buffers.values():
            if isinstance(value, list):
                value.clear()
            else:
                for series in value.values():
                    series.clear()
        _buffer_date = today


def _append_point(series: list[dict[str, object]], value: float) -> None:
    """``series``(버퍼 안의 특정 시리즈 list)에 지금 시각 포인트를 append하고
    500포인트 캡을 적용한다. 시장별로 구조가 나뉘면서(§5.10) 이름으로 버퍼를
    다시 찾기보다 호출부가 이미 들고 있는 list 참조를 직접 넘기는 편이 더
    단순하다."""
    series.append({"time": _now_hhmm_kst(), "value": value})
    if len(series) > MAX_POINTS_PER_SERIES:
        del series[: len(series) - MAX_POINTS_PER_SERIES]


def record_flow_snapshot(payload: dict) -> None:
    """`routers.markets._warm_flow_live`가 이미 반환한 값을 받아 kospi/kosdaq
    각 시장의 개인/외국인/기관계 3개 시리즈에 순매수대금(net_value, 백만원)을
    각자 append한다(PLAN.md §5.10 — 더 이상 두 시장을 합산하지 않는다). 새
    외부 호출은 전혀 없다 — 인자는 warm 함수가 이미 fetch를 끝내고 반환한
    dict 그대로다.

    ``payload["market_closed"]``가 true면 아무 것도 하지 않는다 — 이 경우
    warm 함수 자체가 키움 라이브 호출을 생략하고 DB 확정치/직전 캐시를 재사용
    중이라(routers/markets.py `_warm_flow_live` docstring 참고), 그 값을
    "장중 새 스냅샷"인 것처럼 적립하면 잘못된 시계열이 된다.

    한 시장이 None이거나 해당 투자자 키가 없으면 그 시장의 그 투자자만
    append하지 않는다(다른 시장/다른 투자자는 영향받지 않는다) — 두 시장이
    항상 같은 warm 호출에서 함께 오므로(같은 payload), 같은 틱에 대해 한쪽만
    빠지는 상황이라도 시간(time) 키는 두 시장 다 지금 시각으로 동일하게
    찍힌다(get_foreign_position_series의 시간 매칭 전제와 일관)."""
    if payload.get("market_closed"):
        return

    _reset_if_new_day()

    for market_key in _FLOW_MARKETS:
        market_data = payload.get(market_key)
        if not market_data:
            continue
        investors = market_data.get("investors") or {}
        market_buffer = _buffers[market_key]
        for investor in _FLOW_INVESTORS:
            entry = investors.get(investor)
            if not entry:
                continue
            net_value = entry.get("net_value")
            if net_value is None:
                continue
            _append_point(market_buffer[investor], net_value)


def record_futures_flow_snapshot(payload: dict) -> None:
    """`routers.markets._warm_futures_flow_live`가 이미 반환한 값을 받아
    "외인선물" 시리즈에 외국인 투자자의 순매수대금(net_value, 백만원)을
    append한다. `record_flow_snapshot`과 동일하게 새 외부 호출은 없고,
    ``market_closed``면 스킵한다."""
    if payload.get("market_closed"):
        return

    _reset_if_new_day()

    investors = payload.get("investors") or {}
    entry = investors.get("외국인") or {}
    net_value = entry.get("net_value")
    if net_value is not None:
        _append_point(_buffers["외인선물"], net_value)


def record_breadth_snapshot(payload: dict) -> None:
    """`routers.markets._warm_breadth_live`가 이미 반환한 값을 받아 "등락비율"
    시리즈(코스피+코스닥 합산, 시장 구분 없는 단일 시리즈)에 상승비율(%)을
    append한다. PLAN.md §5.13 지표 정의: ``ratio = total_adv / (total_adv +
    total_dec) * 100`` — 보합(flat)은 분모에서 제외한다("50% 기준"이 자연스러운
    중립점이 되려면 상승 대 하락만의 비율이어야 한다는 사용자 요청 그대로).

    `record_flow_snapshot`과 동일하게 ``market_closed``면 스킵한다(장 마감
    시엔 warm 함수가 DB 확정치/직전 캐시를 재사용 중이라 "장중 새 스냅샷"으로
    적립하면 잘못된 시계열이 된다). kospi/kosdaq 중 한쪽이 None이면 있는 쪽만으로
    계산한다(다른 record_* 함수들의 "있는 쪽만" 관례와 동일). 둘 다 없거나
    adv+dec 합이 0이면(극단적 예외) 이번 틱은 append하지 않는다."""
    if payload.get("market_closed"):
        return

    _reset_if_new_day()

    total_adv = 0
    total_dec = 0
    for market_key in _FLOW_MARKETS:
        market_data = payload.get(market_key)
        if not market_data:
            continue
        adv = market_data.get("adv")
        dec = market_data.get("dec")
        if adv is not None:
            total_adv += adv
        if dec is not None:
            total_dec += dec

    denom = total_adv + total_dec
    if denom <= 0:
        return

    ratio = total_adv / denom * 100
    _append_point(_buffers["등락비율"], ratio)


def get_flow_series() -> dict:
    """1D 조회 API(`GET /api/markets/flow/intraday-accumulated`)가 그대로
    반환할 payload. ``date``는 오늘 KST 날짜(버퍼가 비어 있어도 항상 오늘
    날짜를 보고한다 — 프런트가 빈 차트에도 "오늘 날짜"를 라벨로 쓸 수 있도록,
    `_buffer_date`가 아직 None이거나 리셋 직후라도 이 값은 흔들리지 않는다).
    ``market_closed``는 저장된 값이 아니라 호출 시점에 새로 계산한다(버퍼에
    마지막으로 찍힌 시점의 장 상태가 아니라 "지금" 장이 열려 있는지가 프런트가
    알고 싶은 정보이기 때문).

    **PLAN.md §5.10**: ``series``는 더 이상 투자자 3종을 바로 담지 않고,
    ``kospi``/``kosdaq`` 두 시장 블록 아래에 각각 투자자 3종을 담는다 —
    코스피에 수급이 쏠려 있어 코스닥만의 흐름을 못 보던 문제를 해소하기 위해
    합산을 프런트로 미뤘다(백엔드는 "합계" 블록을 미리 계산해 얹지 않는다)."""
    return {
        "date": _today_kst().isoformat(),
        "series": {
            market_key: {investor: list(_buffers[market_key][investor]) for investor in _FLOW_INVESTORS}
            for market_key in _FLOW_MARKETS
        },
        "market_closed": is_market_closed(dt.datetime.now(KST)),
    }


def get_foreign_position_series() -> dict:
    """1D 조회 API(`GET /api/markets/foreign-position/intraday-accumulated`)가
    그대로 반환할 payload. ``spot``은 kospi/kosdaq 버퍼의 "외국인" 시리즈를
    시간(time) 키 기준으로 매칭해 합산한 값이다(외인 양손 모달의 "현물" 쪽은
    flow/live의 외국인 투자자 kospi+kosdaq 합계와 동일한 지표라 §5.10 분리
    이후에도 이 모달은 회귀 없이 그대로 유지 — PLAN.md §5.10 참고). 두 시장은
    항상 같은 warm 호출(`record_flow_snapshot`)에서 함께 append되므로 같은
    시각 문자열끼리 짝을 맞추면 된다 — 한쪽 시장에만 찍힌 시각이 있으면(예:
    한쪽 fetch만 실패) 있는 쪽 값 그대로 사용한다. ``futures``는
    `record_futures_flow_snapshot`이 채우는 "외인선물" 시리즈(그대로, 시장
    구분 없음)."""
    return {
        "date": _today_kst().isoformat(),
        "spot": _merge_foreign_spot_series(),
        "futures": list(_buffers["외인선물"]),
        "market_closed": is_market_closed(dt.datetime.now(KST)),
    }


def get_breadth_series() -> dict:
    """1D 조회 API(`GET /api/markets/breadth/intraday-accumulated`)가 그대로
    반환할 payload(PLAN.md §5.13). `get_flow_series`와 동일한 모양이지만
    ``series``가 투자자별 중첩이 아니라 바로 포인트 리스트다(단일 시리즈라서).
    ``date``/``market_closed`` 계산 방식은 `get_flow_series`와 동일하다."""
    return {
        "date": _today_kst().isoformat(),
        "series": list(_buffers["등락비율"]),
        "market_closed": is_market_closed(dt.datetime.now(KST)),
    }


def _merge_foreign_spot_series() -> list[dict[str, object]]:
    """kospi/kosdaq 버퍼의 "외국인" 시리즈를 time 키로 매칭해 값을 더한다.
    두 시리즈는 항상 같은 warm 호출에서 함께 append되어 인덱스/개수가 보통
    일치하지만, 시간 문자열을 키로 매칭해 순서에 의존하지 않고 합산한다
    (원래 등장 순서를 보존하기 위해 먼저 등장한 time 순서를 따른다)."""
    order: list[str] = []
    totals: dict[str, float] = {}
    for market_key in _FLOW_MARKETS:
        for point in _buffers[market_key]["외국인"]:
            time_key = point["time"]
            if time_key not in totals:
                order.append(time_key)
                totals[time_key] = 0.0
            totals[time_key] += point["value"]
    return [{"time": t, "value": totals[t]} for t in order]
