"""§5.50 포지셔닝 프레임 사후 검증 — 그룹별 버킷 통계 (PLAN.md §5.52).

`collectors/positioning_snapshot.py`가 매일 쌓아 온 `positioning_snapshot`
행들을 4가지 기준(regime, 상대강도 부호, 외인 현물/선물 부호, 나스닥선물 부호)
으로 그룹핑해 표본수(n)·평균 다음날 수익률·상승확률을 계산한다 —
`quant/regime_backtest.py::compute_streak_buckets`가 스트릭을 버킷화하는 것과
정확히 같은 형태의 통계(순수 계산부는 이 모듈, 세션 조회는 라우터가 담당하는
`compute_streak_buckets`와 달리, 이 모듈은 **완전한 순수 함수**로 둔다 — 호출자
(routers/markets.py)가 이미 `positioning_snapshot` 전체 행을 dict 리스트로 조회해
넘겨주므로, `quant/volume_surge.py`/`quant/risk_alert.py`와 같은 이유로 세션이
필요 없다).

**house rule(§5) 그대로**: 이 모듈의 어떤 반환값도 "이 조합이 유리하다/불리하다"
같은 결론을 담지 않는다 — 그룹별 평균/표본수 숫자만 계산해 반환할 뿐, 해석은
전적으로 호출자(최종적으로는 사람)의 몫이다.

**표본 부족이면 통계를 숨긴다(값을 None으로)** — `quant/volume_profile.py`의
`MIN_BARS_FOR_LEVELS`, `quant/sector_rotation.py`의 `MIN_BASELINE_DAYS`와 동일한
원칙: `n < MIN_SAMPLES`인 그룹은 `avg_next_day_change_rate`/`positive_rate_pct`를
`None`으로 두고 `n`만 정직하게 보여준다 — 작은 표본으로 그럴듯한 평균을 보여주지
않는다. 표본이 부족하다는 사실 자체를 숨기지 않는 것이 이 원칙의 핵심이라, `n`은
항상 계산해 반환한다(값만 가릴 뿐 개수는 가리지 않는다).
"""

from __future__ import annotations

# 이 값 미만인 그룹은 avg_next_day_change_rate/positive_rate_pct를 None으로
# 가린다(§5.52 설계 — "최소 한 달 가량의 매일 수집" 기준을 그대로 상수화).
MIN_SAMPLES = 20


def _bucket_stats(values: list[float]) -> dict:
    """단일 그룹의 n/평균/상승확률을 계산한다. `n < MIN_SAMPLES`면 평균/상승확률을
    None으로 가리고 n만 반환한다(모듈 docstring "표본 부족" 원칙)."""
    n = len(values)
    if n < MIN_SAMPLES:
        return {"n": n, "avg_next_day_change_rate": None, "positive_rate_pct": None}
    avg = sum(values) / n
    positive = sum(1 for v in values if v > 0) / n * 100
    return {
        "n": n,
        "avg_next_day_change_rate": round(avg, 3),
        "positive_rate_pct": round(positive, 1),
    }


def _group_by(
    rows: list[dict], key_fn
) -> dict[str, dict]:
    """`rows`(각 원소는 최소 ``{"next_day_change_rate": float}`` + 그룹핑에 쓰일
    필드를 담은 dict)를 `key_fn`이 반환하는 라벨로 나눠 그룹별 통계를 계산한다.
    `key_fn`이 None을 반환하면(해당 없음 — 예: regime 필드 자체가 없는 행) 그
    행은 어느 그룹에도 속하지 않고 건너뛴다."""
    buckets: dict[str, list[float]] = {}
    for row in rows:
        label = key_fn(row)
        if label is None:
            continue
        buckets.setdefault(label, []).append(row["next_day_change_rate"])
    return {label: _bucket_stats(values) for label, values in buckets.items()}


def _sign_label(value: float | None) -> str | None:
    """부호 그룹 라벨 — 양수="positive", 음수="negative", 0 또는 None은 어느
    그룹에도 넣지 않는다(§5.52 설계 "0은 제외" — 방향성이 없는 날을 억지로 양쪽
    중 하나에 끼워 넣지 않는다)."""
    if value is None or value == 0:
        return None
    return "positive" if value > 0 else "negative"


def compute_positioning_hitrate(rows: list[dict]) -> dict:
    """§5.52 사후 검증 집계 — 4가지 그룹핑 기준을 한 번에 계산한다.

    `rows`는 `positioning_snapshot`에서 ``next_day_change_rate is not None``인
    행만 골라(아직 결과가 안 채워진 오늘/최근 행은 호출자가 미리 제외) dict로
    변환한 리스트여야 한다 — 각 dict는 최소 ``{"regime", "relative_strength_pct",
    "foreign_spot_cum", "foreign_futures_cum", "nasdaq_futures_change_pct",
    "next_day_change_rate"}`` 키를 가져야 한다(값은 전부 float|None, "regime"만
    str|None).

    Returns ``{"by_regime": {label: {n, avg_next_day_change_rate,
    positive_rate_pct}}, "by_relative_strength_sign": {"positive"|"negative": {...}},
    "by_foreign_spot_sign": {...}, "by_foreign_futures_sign": {...},
    "by_nasdaq_futures_sign": {...}, "min_samples": MIN_SAMPLES}`` — 표본이
    아예 없는 그룹(예: 아직 한 번도 등장하지 않은 regime 라벨)은 결과 dict에
    키 자체가 없다(음성 데이터를 n=0으로 지어내지 않는다 — 실제로 그 그룹이
    한 번도 관측되지 않았다는 사실 그대로)."""
    by_regime = _group_by(rows, lambda r: r.get("regime"))
    by_relative_strength_sign = _group_by(rows, lambda r: _sign_label(r.get("relative_strength_pct")))
    by_foreign_spot_sign = _group_by(rows, lambda r: _sign_label(r.get("foreign_spot_cum")))
    by_foreign_futures_sign = _group_by(rows, lambda r: _sign_label(r.get("foreign_futures_cum")))
    by_nasdaq_futures_sign = _group_by(rows, lambda r: _sign_label(r.get("nasdaq_futures_change_pct")))

    return {
        "by_regime": by_regime,
        "by_relative_strength_sign": by_relative_strength_sign,
        "by_foreign_spot_sign": by_foreign_spot_sign,
        "by_foreign_futures_sign": by_foreign_futures_sign,
        "by_nasdaq_futures_sign": by_nasdaq_futures_sign,
        "min_samples": MIN_SAMPLES,
    }
