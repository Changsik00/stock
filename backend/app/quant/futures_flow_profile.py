"""K200 선물 외국인/기관 순매매 가격대별 프로파일(근사) — PLAN.md §5.41.

**사용자 요청**: "외인, 기관의 포지션을 알려줄 수 있어? 즉 제일 많이 거래된 부분 말야..
상방 하방 포지션 모두 알려줘야 할 것 같은데?" — 이어서 "혹시 이런거 매매기법이 있다면
그것도 적용해 보는건 어때?"

**(a) 이건 미결제약정(open interest) 기반 "포지션"이 아니다**: 진짜 "포지션"을 말하려면
미결제약정 데이터가 있어야 하는데, PLAN.md §5.30에서 키움·네이버 양쪽을 실호출까지
동원해 조사했고 **국내에서 접근 가능한 소스가 없다는 결론을 이미 확정했다**(dead end,
재조사 불필요). 이 모듈은 그 대안으로 이미 갖고 있는 두 데이터 — K200 선물 일봉
(`index_ohlcv`, market='k200_futures')의 저가~고가와, 하루 단위 외국인/기관계 선물
순매매 **금액**(`market_flow`, market='k200_futures', source='naver',
`collectors/futures_flow.py`가 매일 수집)만으로 근사한다.

**(b) "가격대별 투자자 유형 태깅 매매기법"은 세상에 존재하지 않는다**: 사용자가 말한
"매매기법"을 웹 조사로 확인했으나, 주문 체결 단계에서 투자자 유형(외국인/기관/개인)을
가격대별로 태깅한 공개 데이터는 국내외 어디에도 없다(그런 태깅은 거래소·증권사 내부
정보다). TradingView 등의 "Institutional Volume Flow" 류 지표도 실제 투자자 식별이
아니라 거래량 패턴에서 추정하는 방식일 뿐이다. 그래서 이 모듈이 하는 일은 **자체
구성한 근사치**이지, 검증된 외부 기법을 이식한 게 아니다 — 이 사실을 숨기지 않는다.

**(c) 계산 방식 — `quant/volume_profile.py`(§5.34)와 동일한 binning, 값만 부호 있게**:
`compute_volume_profile`은 각 봉의 거래량(항상 양수)을 그 봉의 저가~고가 구간에 걸친
가격 bin들에 균등분배했다. 이 모듈의 `compute_flow_profile`은 똑같은 기하 구조(전체
가격구간을 `num_bins`개 등폭 bin으로 나누고, 봉이 겹치는 bin 수만큼 균등분배)를 그대로
쓰되, 분배하는 값이 그날의 **부호 있는** 순매매 금액(백만원)이라 bin 누적값이 음수가
될 수 있다. 양수 쪽 국소최댓값을 "매수 집중 구간", 음수 쪽 국소최솟값(절댓값 기준
국소최댓값)을 "매도 집중 구간"으로 각각 독립적으로 추출한다.

**(d) 정직하게 명시할 것 — 하루 단위 근사, 틱 데이터 아님**: 그날 실제로 어느 가격에서
얼마나 체결됐는지(틱 단위 체결가·체결량)는 모른다. 아는 것은 "그날의 저가~고가 범위"와
"그날 하루 전체의 순매매 금액 합계"뿐이라, 하루 안에서 매수가 어느 가격에 더 몰렸는지는
전혀 알 수 없고 그 범위 전체에 균등하게 있었다고 가정한다. 따라서 이 프로파일이 보여주는
것은 "과거 여러 날에 걸쳐 이 가격대가 순매수/순매도가 컸던 날들의 가격범위와 자주
겹쳤다"는 관찰 사실이지, 실제 체결 기반 volume-at-price가 아니다.

**(e) 예측도, 매매 신호도 아니다**: `volume_profile.py` 모듈 docstring (b)/(c)절과
동일한 원칙 — 이 모듈은 "과거에 순매수/순매도가 몰렸다"는 관찰 지표만 반환한다.
그 가격대가 미래에 실제로 의미 있게 작용할지는 백테스트로 검증된 적이 없다(하지
않았다). 매매 신호로 승격하려면 그때 가서 별도로 검증을 거쳐야 한다.
"""

from __future__ import annotations

from .volume_profile import DEFAULT_NUM_BINS, MIN_BARS_FOR_LEVELS

# 피크로 인정하는 최소 높이 = 각 방향(매수/매도) 최댓값(절댓값)의 이 비율 이상 —
# volume_profile.py의 MIN_PROMINENCE_RATIO(0.3)와 동일한 값·동일한 근거(전체
# 최댓값 대비 상대 높이라는 단순 대리 지표, 진짜 위상학적 prominence는 과설계로
# 판단해 쓰지 않는다 — 그 모듈 docstring 참고). 매수/매도 두 방향을 독립적으로
# 판정하므로 값 자체도 독립 상수로 둔다(우연히 같은 0.3이지만 서로 다른 이유로
# 바뀔 수 있어 volume_profile.MIN_PROMINENCE_RATIO를 재사용하지 않고 새로 정의).
MIN_PROMINENCE_RATIO = 0.3

# 매수/매도 각 방향에서 반환하는 최대 레벨 개수 — volume_profile.py의 MAX_LEVELS(8,
# 방향 구분 없는 단일 리스트)와 달리 이 모듈은 양방향이라 차트에 동시에 그리면 선이
# 2배로 늘어난다. 방향당 4개(합산 최대 8개)로 낮춰 가독성을 volume_profile.py와
# 비슷한 수준으로 맞춘다.
MAX_LEVELS_PER_SIDE = 4


def _valid_bars_with_flow(bars: list[dict], flow_by_date: dict[str, float]) -> list[tuple[float, float, float]]:
    """`bars`에서 (low, high, net_value) 중 하나라도 없거나 파싱 불가하거나
    `high < low`(데이터 이상)인 봉, 그리고 `flow_by_date`에 **그 날짜 키 자체가
    없는** 봉을 걸러낸 (low, high, net_value) 튜플 리스트를 만든다.

    "키가 없음"과 "그날 순매매가 정확히 0"은 다르다 — 후자는 실제로 발생 가능한
    관찰값(매수·매도가 정확히 상쇄됨)이라 그대로 0.0을 누적해야 하고, 전자는
    "그 날짜의 순매매 데이터를 아예 수집 못 했다"는 뜻이라 조용히 건너뛴다(0으로
    채워 넣으면 실제로 순매매가 없었던 날처럼 보이는 거짓 정보가 된다 — §5 "정직한
    실패" 원칙). 그래서 `flow_by_date.get(date, 0.0)`이 아니라 `in` 체크로
    구분한다.
    """
    out: list[tuple[float, float, float]] = []
    for bar in bars:
        date = bar.get("date")
        if date is None or date not in flow_by_date:
            continue
        low_raw = bar.get("low")
        high_raw = bar.get("high")
        if low_raw is None or high_raw is None:
            continue
        try:
            low = float(low_raw)
            high = float(high_raw)
            net_value = float(flow_by_date[date])
        except (TypeError, ValueError):
            continue
        if high < low:
            continue
        out.append((low, high, net_value))
    return out


def compute_flow_profile(
    bars: list[dict], flow_by_date: dict[str, float], num_bins: int = DEFAULT_NUM_BINS
) -> dict:
    """`bars`(지수 일봉 리스트, `get_market_series_from_db`의 출력 형태 —
    `{"date": "YYYYMMDD", "high", "low", ...}`)와 `flow_by_date`(같은 "YYYYMMDD"
    문자열을 키로 하는 그날의 부호 있는 순매매 금액(백만원) 매핑, 투자자 카테고리
    하나 분량)를 받아, `volume_profile.compute_volume_profile`과 동일한 기하
    구조로 부호 있는 순매매 금액을 가격 bin에 분배·누적한다(모듈 docstring
    (c)절 참고).

    Returns:
        ``{"bins": [{"price_low", "price_high", "price_mid", "net_value"}, ...],
        "total_net_value": float, "bar_count": int, "num_bins": int}``

        - ``bins``: 가격 오름차순, 길이는 보통 `num_bins`(유효 가격 구간이 한
          점으로 퇴화하면 1). `net_value`는 음수 가능.
        - ``total_net_value``: 매칭된 봉들의 순매매 금액 합(= 모든 bin의 합).
        - ``bar_count``: `flow_by_date`에 날짜가 매칭되고 가격 필드가 유효한
          봉 개수 — `detect_flow_levels`가 표본 부족 판단에 쓴다.

        매칭되는 유효 봉이 하나도 없으면 ``{"bins": [], "total_net_value": 0.0,
        "bar_count": 0, "num_bins": num_bins}``.
    """
    valid = _valid_bars_with_flow(bars, flow_by_date)
    if not valid:
        return {"bins": [], "total_net_value": 0.0, "bar_count": 0, "num_bins": num_bins}

    price_min = min(low for low, _high, _net in valid)
    price_max = max(high for _low, high, _net in valid)
    total_net_value = sum(net for _low, _high, net in valid)

    if price_max <= price_min:
        # volume_profile.py와 동일한 퇴화 케이스 처리 — 전 구간이 한 가격으로
        # 뭉개지면 bin을 나누는 게 무의미하므로 단일 bin에 전부 담는다.
        single_bin = {
            "price_low": price_min,
            "price_high": price_max,
            "price_mid": price_min,
            "net_value": round(total_net_value, 4),
        }
        return {
            "bins": [single_bin],
            "total_net_value": round(total_net_value, 4),
            "bar_count": len(valid),
            "num_bins": 1,
        }

    bin_width = (price_max - price_min) / num_bins
    bin_values = [0.0] * num_bins

    def _bin_index(price: float) -> int:
        idx = int((price - price_min) / bin_width)
        return max(0, min(num_bins - 1, idx))

    for low, high, net_value in valid:
        start_idx = _bin_index(low)
        end_idx = _bin_index(high)
        if end_idx < start_idx:
            start_idx, end_idx = end_idx, start_idx
        span = end_idx - start_idx + 1
        share = net_value / span  # 균등분배 — volume_profile.py (a)절과 동일한 원칙.
        for i in range(start_idx, end_idx + 1):
            bin_values[i] += share

    bins = []
    for i in range(num_bins):
        b_low = price_min + i * bin_width
        b_high = price_min + (i + 1) * bin_width
        bins.append(
            {
                "price_low": b_low,
                "price_high": b_high,
                "price_mid": (b_low + b_high) / 2,
                "net_value": round(bin_values[i], 4),
            }
        )

    return {
        "bins": bins,
        "total_net_value": round(total_net_value, 4),
        "bar_count": len(valid),
        "num_bins": num_bins,
    }


def _local_extrema_indices(values: list[float]) -> list[int]:
    """`values`에서 국소 최댓값 인덱스를 찾는다 — `volume_profile.detect_levels`의
    피크 판정 로직과 동일(양쪽 바로 이웃보다 **엄격히** 커야 하고, 가장자리는
    하나뿐인 이웃만 이기면 됨). 매수 쪽은 이 함수를 그대로 쓰고, 매도 쪽은 값을
    부호 반전(`[-v for v in values]`)해서 넘기면 "가장 음수인(=반전하면 가장
    양수인) bin"이 국소 최댓값으로 잡힌다."""
    n = len(values)
    idx = []
    for i, v in enumerate(values):
        beats_left = True if i == 0 else v > values[i - 1]
        beats_right = True if i == n - 1 else v > values[i + 1]
        if beats_left and beats_right:
            idx.append(i)
    return idx


def _pick_side_levels(
    bins: list[dict], side_values: list[float], min_prominence_ratio: float, max_levels: int
) -> list[dict]:
    """`side_values`(매수면 원래 net_value, 매도면 부호 반전한 net_value — 둘 다
    "이 방향으로 클수록 강함"이 되도록 맞춘 배열)에서 국소최댓값 중 전체
    최댓값의 `min_prominence_ratio` 이상인 bin만 골라 절댓값 내림차순으로
    `max_levels`개까지 반환한다. `bins[i]["net_value"]`(부호 있는 원래 값)를
    그대로 실어 반환하므로 매도 쪽도 음수 그대로 나온다."""
    candidates = [i for i in _local_extrema_indices(side_values) if side_values[i] > 0]
    if not candidates:
        return []

    max_value = max(side_values[i] for i in candidates)
    threshold = max_value * min_prominence_ratio

    levels = [
        {
            "price_low": bins[i]["price_low"],
            "price_high": bins[i]["price_high"],
            "price_mid": bins[i]["price_mid"],
            "net_value": bins[i]["net_value"],
        }
        for i in candidates
        if side_values[i] >= threshold
    ]
    levels.sort(key=lambda lv: abs(lv["net_value"]), reverse=True)
    return levels[:max_levels]


def detect_flow_levels(
    profile: dict,
    min_prominence_ratio: float = MIN_PROMINENCE_RATIO,
    max_levels_per_side: int = MAX_LEVELS_PER_SIDE,
) -> dict:
    """`compute_flow_profile`의 반환값에서 매수 집중 구간(양수 쪽 국소최댓값)과
    매도 집중 구간(음수 쪽 국소최솟값, 절댓값 기준 국소최댓값)을 각각 독립적으로
    추출한다. 판정 로직은 `volume_profile.detect_levels`의 "피크 + prominence
    비율" 방식을 그대로 방향별로 적용한 것 — 모듈 docstring (c)절 참고.

    Returns:
        ``{"buy_levels": [...], "sell_levels": [...]}`` — 각 원소는
        ``{"price_low", "price_high", "price_mid", "net_value"}``
        (`net_value`는 부호 있는 원래 값 — sell_levels도 음수 그대로),
        절댓값 내림차순, 각 쪽 최대 `max_levels_per_side`개.

        표본 부족(`bar_count < MIN_BARS_FOR_LEVELS`, volume_profile.py와 동일한
        임계값 재사용 — 근거도 동일: 한 달 미만 표본으로는 "구간별 순매매 누적"이
        무의미) 또는 bin이 없으면 양쪽 다 빈 리스트(§5 "정직한 실패" 원칙 —
        표본이 부족한데 억지로 레벨을 만들지 않는다).
    """
    bins = profile.get("bins") or []
    bar_count = profile.get("bar_count", 0)
    if bar_count < MIN_BARS_FOR_LEVELS or not bins:
        return {"buy_levels": [], "sell_levels": []}

    net_values = [b["net_value"] for b in bins]
    negated = [-v for v in net_values]

    buy_levels = _pick_side_levels(bins, net_values, min_prominence_ratio, max_levels_per_side)
    sell_levels = _pick_side_levels(bins, negated, min_prominence_ratio, max_levels_per_side)

    return {"buy_levels": buy_levels, "sell_levels": sell_levels}
