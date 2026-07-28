"""거래량 프로파일(Volume Profile) 근사 + 지지/저항 후보 감지 — PLAN.md §5.34.

**사용자 요청**: "특정 지점에서 매수, 매도가 많은 것도 알아내야 해.. 지지선 혹은
저항선 말이야." — 특정 가격대에 과거 거래량이 몰려 있으면 그 가격대가 다시 오면
매수/매도 압력이 몰려 지지·저항으로 작용한다는 고전적 기술적분석 개념(거래량
프로파일/Volume Profile, POC=Point of Control)을 우리가 가진 데이터로 감지한다.

**(a) 이것은 근사치다 — 틱 데이터가 아니다**: 진짜 "가격대별 체결 거래량"(틱 단위
volume-at-price, Level 2 호가창 누적)은 이 프로젝트에 없다. 갖고 있는 건 일봉
(open/high/low/close/volume)과 온디맨드 분봉뿐이다. 그래서 여기서는 하루(또는
한 봉)의 거래량을 그 봉의 저가~고가 구간에 걸쳐 **균등분배**해 가격 구간(bin)별로
누적하는 방식을 쓴다 — 실전 HTS들도 틱 데이터가 없을 때 흔히 쓰는 근사 방식이다.
`compute_volume_profile`이 봉 하나가 겹치는 bin 개수만큼 거래량을 **똑같이 나눠**
담는다(예: 3개 bin에 걸치면 각 1/3) — 겹치는 폭 비율로 가중치를 주는 정교한
분포(삼각분포, 정규분포 등)는 일부러 쓰지 않는다. 이미 근사치인 지표에 정교한
분포 모델을 얹는 것은 실제로 없는 정밀도를 있는 것처럼 보이게 하는 과설계라고
판단했다(PLAN.md §5.34 설계 절 "단순함 우선" 참고) — 단순한 균등분배가 더
정직하다.

**(b) "지지/저항이 될 것이다"라는 예측이 아니다**: 이 모듈이 반환하는 것은
"과거에 이 가격대에서 거래량이 많이 몰렸다"는 관찰 사실뿐이다. 그 가격대가
미래에 실제로 지지/저항으로 작용할지는 이 모듈이 판정하지 않는다 — 판정하려면
백테스트가 필요하다.

**(c) 백테스트로 예측력이 검증된 적이 없다**: §5.15(백워데이션 배지가 그럴듯해
보였지만 3년 백테스트로 예측력이 없었던 사례), §5.23(쏠림 비율 백테스트가 신호로
승격되기 전 반드시 검증부터 거친 사례), §5.33(업종 로테이션 지표가 "로테이션
있다/없다" 판정을 반환하지 않고 숫자만 반환하는 house rule)과 동일한 원칙을
그대로 따른다 — 이 모듈은 관찰 지표(observational overlay)로만 제공하고, 매매
신호로 승격하려면 그때 가서 동일한 백테스트 검증을 거쳐야 한다(지금은 하지
않는다).

**입력 형태 — 종목/지수 공용**: `services.py::get_stock_series_from_db`(종목,
2026-07-28 전 `routers/stocks.py::_read_candles`에서 PLAN.md §5.35-2로 이전)와
`services.py::get_market_series_from_db`(지수)는 이미 동일한 응답 형태로
맞춰져 있다(각 모듈 docstring 참고) — `{"date", "open", "high", "low", "close",
"changeRate", "volume", "value"}`. 이 모듈은 그중 `high`/`low`/`volume` 세
필드만 읽으므로 두 함수의 출력을 변환 없이 그대로 넘길 수 있다(범용 함수, 신규
데이터 수집 없음 — 이미 읽어 둔 리스트를 재계산만 한다).

**방어적 처리**: `high`/`low`/`volume` 중 하나라도 없거나(None), 파싱 불가하거나,
`high < low`(데이터 이상)이거나 `volume < 0`이면 그 봉은 조용히 건너뛴다 — 나머지
유효한 봉만으로 프로파일을 만든다(크래시하지 않는다, §5 "정직한 실패" 원칙).

**표본 부족 시 정직한 실패**: `detect_levels`는 유효한 봉 수가
`MIN_BARS_FOR_LEVELS`(20 — 약 한 달치 거래일. 이보다 적으면 "구간별 거래량
누적"이라는 말 자체가 무의미해 개별 봉 몇 개의 우연한 크기가 그대로 "피크"로
잡혀버린다, sector_rotation.py의 MIN_BASELINE_DAYS와 동일한 판단 근거)보다
적으면 피크를 계산하지 않고 빈 리스트를 반환한다(침묵하는 실패가 아니라 명시적
빈 결과 — 호출측이 "표본 부족" 문구를 보여줄지는 자유).

**피크(국소 최댓값) 판정과 prominence 필터**: bin `i`가 "피크"이려면 양쪽
바로 이웃 bin보다 거래량이 **엄격히 커야** 한다(가장자리 bin은 하나뿐인 이웃만
이기면 됨). 그중에서도 전체 최댓값(`max_volume`)의 `min_prominence_ratio`
(기본 0.3 = 30%) 이상인 피크만 "의미 있는" 후보로 남긴다 — 진짜 위상학적
prominence(주변 골짜기 깊이 기준)를 계산하는 대신 "전체 최댓값 대비 상대
높이"라는 훨씬 단순한 대리 지표를 쓴다. 이 지표 자체가 근사치인 마당에 정교한
prominence 알고리즘을 얹는 것 역시 과설계라고 판단했다(모듈 docstring (a)절과
동일한 논리) — 사소한 잡음성 bin(거래량이 미미하게 이웃보다 높을 뿐인 bin)을
걸러내기에는 이 단순한 임계값으로 충분하다.
"""

from __future__ import annotations

# 가격 구간(bin) 기본 개수 — 특별한 근거는 없고 "차트에서 봤을 때 너무 성기지도
# 너무 빽빽하지도 않은" 통상적인 volume profile 시각화 관례치(50개)를 그대로
# 따른다.
DEFAULT_NUM_BINS = 50

# 피크로 인정하는 최소 높이 = 전체 최댓값(bin 거래량)의 이 비율 이상 — 모듈
# docstring "피크 판정과 prominence 필터" 참고. 0.3(30%)은 "POC 대비 3분의 1
# 수준도 안 되는 bin은 사실상 잡음"이라는 실무적 판단의 값이며, 새로 튜닝하고
# 싶다면 이 상수만 바꾸면 된다(호출측 파라미터 기본값과 동일하게 유지).
MIN_PROMINENCE_RATIO = 0.3

# 반환하는 최대 레벨 개수 — 프런트 차트에 수평선을 8개 넘게 그리면 오히려
# 가독성이 떨어져 임의로 상한을 둔다.
MAX_LEVELS = 8

# detect_levels가 피크 계산을 시도하는 최소 유효 봉 수 — 모듈 docstring "표본
# 부족 시 정직한 실패" 참고.
MIN_BARS_FOR_LEVELS = 20


def _valid_bars(bars: list[dict]) -> list[tuple[float, float, float]]:
    """bars에서 (low, high, volume) 중 하나라도 없거나 파싱 불가하거나 앞뒤가
    맞지 않는(high < low, volume < 0) 봉을 걸러낸 (low, high, volume) 튜플
    리스트를 만든다. 모듈 docstring "방어적 처리" 참고."""
    out: list[tuple[float, float, float]] = []
    for bar in bars:
        low_raw = bar.get("low")
        high_raw = bar.get("high")
        volume_raw = bar.get("volume")
        if low_raw is None or high_raw is None or volume_raw is None:
            continue
        try:
            low = float(low_raw)
            high = float(high_raw)
            volume = float(volume_raw)
        except (TypeError, ValueError):
            continue
        if high < low or volume < 0:
            continue
        out.append((low, high, volume))
    return out


def compute_volume_profile(bars: list[dict], num_bins: int = DEFAULT_NUM_BINS) -> dict:
    """`bars`(일봉 또는 분봉 OHLCV 리스트, 순서 무관 — 각 원소는 최소
    `"high"`/`"low"`/`"volume"` 키를 가진 dict, `get_stock_series_from_db`/
    `get_market_series_from_db`의 출력 그대로 넘기면 됨)를 받아 전체 가격
    구간(모든 유효 봉의 low 최솟값 ~ high 최댓값)을 `num_bins`개 등폭
    구간(bin)으로 나누고, 각 봉의 거래량을 그 봉이 걸치는 bin들에 균등분배해
    누적한 프로파일을 만든다(모듈 docstring (a)절 — 근사 방식 설명 참고).

    Returns:
        ``{"bins": [{"price_low", "price_high", "price_mid", "volume"}, ...],
        "poc": {...} | None, "total_volume": float, "bar_count": int,
        "num_bins": int}``

        - ``bins``: 가격 오름차순, 길이는 보통 `num_bins`(유효 가격 구간이
          한 점으로 퇴화하면 1).
        - ``poc``(Point of Control): 거래량이 가장 많은 단일 bin. 유효한
          봉이 하나도 없거나 전체 거래량이 0이면 ``None``.
        - ``total_volume``: 유효 봉들의 거래량 합(= 모든 bin 거래량의 합).
        - ``bar_count``: 유효 봉(방어적 필터를 통과한 봉) 개수 —
          `detect_levels`가 표본 부족 판단에 쓴다.

        유효한 봉이 하나도 없으면 ``{"bins": [], "poc": None,
        "total_volume": 0.0, "bar_count": 0, "num_bins": num_bins}``.
    """
    valid = _valid_bars(bars)
    if not valid:
        return {
            "bins": [],
            "poc": None,
            "total_volume": 0.0,
            "bar_count": 0,
            "num_bins": num_bins,
        }

    price_min = min(low for low, _high, _volume in valid)
    price_max = max(high for _low, high, _volume in valid)
    total_volume = sum(volume for _low, _high, volume in valid)

    if price_max <= price_min:
        # 전 구간이 사실상 한 가격(모든 봉이 동일한 저가=고가)으로 퇴화한
        # 경우 — bin을 여러 개로 나누는 게 무의미하므로 단일 bin에 전체
        # 거래량을 담는다.
        single_bin = {
            "price_low": price_min,
            "price_high": price_max,
            "price_mid": price_min,
            "volume": round(total_volume, 4),
        }
        return {
            "bins": [single_bin],
            "poc": single_bin if total_volume > 0 else None,
            "total_volume": round(total_volume, 4),
            "bar_count": len(valid),
            "num_bins": 1,
        }

    bin_width = (price_max - price_min) / num_bins
    bin_volumes = [0.0] * num_bins

    def _bin_index(price: float) -> int:
        idx = int((price - price_min) / bin_width)
        return max(0, min(num_bins - 1, idx))

    for low, high, volume in valid:
        start_idx = _bin_index(low)
        end_idx = _bin_index(high)
        # 방어적 처리에서 이미 high >= low를 보장했지만 부동소수 경계에서
        # 혹시라도 역전되면 그대로 바로잡는다.
        if end_idx < start_idx:
            start_idx, end_idx = end_idx, start_idx
        span = end_idx - start_idx + 1
        share = volume / span  # 균등분배 — 모듈 docstring (a)절.
        for i in range(start_idx, end_idx + 1):
            bin_volumes[i] += share

    bins = []
    for i in range(num_bins):
        b_low = price_min + i * bin_width
        b_high = price_min + (i + 1) * bin_width
        bins.append(
            {
                "price_low": b_low,
                "price_high": b_high,
                "price_mid": (b_low + b_high) / 2,
                "volume": round(bin_volumes[i], 4),
            }
        )

    poc_idx = max(range(num_bins), key=lambda i: bins[i]["volume"])
    poc = bins[poc_idx] if bins[poc_idx]["volume"] > 0 else None

    return {
        "bins": bins,
        "poc": poc,
        "total_volume": round(total_volume, 4),
        "bar_count": len(valid),
        "num_bins": num_bins,
    }


def detect_levels(
    profile: dict,
    min_prominence_ratio: float = MIN_PROMINENCE_RATIO,
    max_levels: int = MAX_LEVELS,
) -> list[dict]:
    """`compute_volume_profile`의 반환값에서 지지/저항 후보(국소 최댓값 bin)를
    추출한다. 모듈 docstring "피크 판정과 prominence 필터"/"표본 부족 시 정직한
    실패" 참고.

    Returns:
        거래량 내림차순, 최대 `max_levels`개. 각 원소:
        ``{"price_low", "price_high", "price_mid", "volume", "is_poc"}``.
        표본 부족(`bar_count < MIN_BARS_FOR_LEVELS`) 또는 bin이 없거나 전체
        거래량이 0이면 빈 리스트.
    """
    bins = profile.get("bins") or []
    bar_count = profile.get("bar_count", 0)
    if bar_count < MIN_BARS_FOR_LEVELS or not bins:
        return []

    volumes = [b["volume"] for b in bins]
    max_volume = max(volumes)
    if max_volume <= 0:
        return []

    threshold = max_volume * min_prominence_ratio
    poc = profile.get("poc")
    n = len(bins)

    peaks: list[dict] = []
    for i, b in enumerate(bins):
        v = b["volume"]
        if v <= 0:
            continue
        # 가장자리 bin은 하나뿐인 이웃만 이기면 피크로 인정한다(모듈 docstring
        # "피크 판정" 참고).
        beats_left = True if i == 0 else v > volumes[i - 1]
        beats_right = True if i == n - 1 else v > volumes[i + 1]
        if beats_left and beats_right and v >= threshold:
            peaks.append(
                {
                    "price_low": b["price_low"],
                    "price_high": b["price_high"],
                    "price_mid": b["price_mid"],
                    "volume": v,
                    "is_poc": poc is not None and b is poc,
                }
            )

    peaks.sort(key=lambda p: p["volume"], reverse=True)
    return peaks[:max_levels]
