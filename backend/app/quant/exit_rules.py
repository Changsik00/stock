"""수동 매수 포지션의 사용자 청산 조건(Phase 5.118) 순수 판정 함수.

이 모듈은 어떤 주문도 내지 않고 외부 호출도 하지 않는다 — 입력 숫자만 받아 "청산할지"와
사유를 돌려준다. 실제 매도는 ``collectors/auto_trader._execute_sell``이 한다(호출부가
이 모듈의 판정을 받아 넘긴다). 자동 진입 포지션에는 쓰이지 않는다(엔진 고정 규칙
``quant/auto_trade_rules`` 그대로).

조건 종류(rule = ``{"kind", "value"[, "activate_pct"]}``):
- ``take_profit_pct``: 진입가 대비 +value% 이상 → 익절
- ``stop_loss_pct``  : 진입가 대비 -value% 이하 → 손절
- ``trail_pct``      : 고점 대비 -value% 이하 → 청산. ``activate_pct``가 있으면 고점이 진입가
  대비 +activate_pct% 이상에 한 번 닿은 뒤부터만 유효(래칫: 한 번 닿으면 유지), 없으면 진입
  직후부터 고점(초기값=진입가) 기준으로 즉시 유효
- ``target_price``   : 현재가 ≥ value(원) → 목표가 도달
- ``stop_price``     : 현재가 ≤ value(원) → 손절가 이탈
경계값은 포함(≥, ≤)하고 부동소수 오차는 1e-9로 흡수한다. 여러 조건 중 먼저 닿는 것으로
청산하며, 같은 시점에 둘 이상 닿으면 손절 계열 → 익절 계열 → 트레일 순으로 사유를 정한다.
수익률은 진입가(지정가 주문가) 기준이며 수수료·세금은 반영하지 않는다.
"""

from __future__ import annotations

KINDS = ("take_profit_pct", "stop_loss_pct", "trail_pct", "target_price", "stop_price")
PCT_KINDS = ("take_profit_pct", "stop_loss_pct", "trail_pct")
PRICE_KINDS = ("target_price", "stop_price")
# 판정 우선순위(동시 충족 시 사유 선택 순서).
_PRIORITY = {"stop_loss_pct": 0, "stop_price": 0, "take_profit_pct": 1, "target_price": 1, "trail_pct": 2}

# 종류별 허용 범위(%) — 이 밖은 오입력으로 보고 차단한다.
PCT_RANGE = {"take_profit_pct": (0.2, 30.0), "stop_loss_pct": (0.2, 30.0), "trail_pct": (0.1, 10.0)}
ACTIVATE_RANGE = (0.1, 30.0)  # 트레일 '+% 도달 후' 값
MAX_RULES = 5
# 국내 ETF/ETN 호가단위는 가격대와 무관하게 5원(KRX 규정). 코드에 별도 함수가 없어 상수로 둔다.
TICK_ETF = 5
_EPS = 1e-9

KIND_LABEL = {
    "take_profit_pct": "익절",
    "stop_loss_pct": "손절",
    "trail_pct": "고점 대비 하락",
    "target_price": "목표가",
    "stop_price": "손절가",
}


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _normalize(raw: list | None) -> tuple[list[dict], list[str]]:
    """형식·종류·숫자·범위·중복 검증과 정규화(구조 검증). ``(rules, errors)``."""
    rules: list[dict] = []
    errors: list[str] = []
    seen: set[str] = set()
    items = list(raw or [])
    if len(items) > MAX_RULES:
        errors.append(f"조건은 최대 {MAX_RULES}개까지 걸 수 있습니다(현재 {len(items)}개).")
    for item in items:
        if not isinstance(item, dict):
            errors.append("조건 형식이 올바르지 않습니다.")
            continue
        kind = item.get("kind")
        if kind not in KINDS:
            errors.append(f"알 수 없는 조건 종류입니다: {kind}")
            continue
        label = KIND_LABEL[kind]
        if kind in seen:
            errors.append(f"같은 종류의 조건은 하나만 걸 수 있습니다: {label}")
            continue
        seen.add(kind)
        value = _num(item.get("value"))
        if value is None:
            errors.append(f"{label} 값이 숫자가 아닙니다.")
            continue
        if kind in PCT_RANGE:
            lo, hi = PCT_RANGE[kind]
            if not (lo - _EPS <= value <= hi + _EPS):
                errors.append(f"{label}은(는) {lo:g}~{hi:g}% 범위여야 합니다(입력 {value:g}%).")
                continue
        elif not value > 0:
            errors.append(f"{label}: 가격은 0보다 커야 합니다.")
            continue
        rule: dict = {"kind": kind, "value": value}
        if item.get("activate_pct") is not None:
            if kind != "trail_pct":
                errors.append("'도달 후' 조건(activate_pct)은 고점 대비 하락에만 쓸 수 있습니다.")
                continue
            act = _num(item.get("activate_pct"))
            lo, hi = ACTIVATE_RANGE
            if act is None or not (lo - _EPS <= act <= hi + _EPS):
                errors.append(f"'+% 도달 후' 값은 {lo:g}~{hi:g}% 범위여야 합니다.")
                continue
            rule["activate_pct"] = act
        rules.append(rule)
    return rules, errors


def _p(x: float) -> str:
    """부호 포함 퍼센트 문자열(소수 2자리까지, 불필요한 0 제거)."""
    return f"{round(x, 2):+g}%"


def _px(x: float) -> str:
    return f"{x:,.0f}원"


def _eff_pct(rule: dict, base: float | None) -> float | None:
    """조건 크기(%) 환산 — 익절·목표가는 상승폭, 손절·손절가는 하락폭(둘 다 양수)."""
    k, v = rule["kind"], rule["value"]
    if k in ("take_profit_pct", "stop_loss_pct"):
        return v
    if not base:
        return None
    if k == "target_price":
        return (v / base - 1) * 100
    if k == "stop_price":
        return (1 - v / base) * 100
    return None


def _simulate(rules: list[dict], entry: float, peak: float | None, waypoints: list[float]) -> dict:
    """가격 경로(꼭짓점)를 0.05% 간격으로 훑으며 ``decide_manual_exit``를 그대로 호출한다
    (별도 판정 구현 없음). 반환 ``{"exit": bool, "price", "rule", "reason"}``."""
    step = max(1, round(entry * 0.0005))
    cur_peak = peak
    last = waypoints[0]
    for nxt in waypoints[1:]:
        n = max(1, int(abs(nxt - last) // step))
        for i in range(1, n + 1):
            price = round(last + (nxt - last) * i / n)
            d = decide_manual_exit(rules, entry, cur_peak, price)
            if d["action"] == "exit":
                return {"exit": True, "price": price, "rule": d["rule"], "reason": d["reason"]}
            cur_peak = d["new_peak_price"]
        last = nxt
    return {"exit": False, "price": round(last), "rule": None, "reason": ""}


def _scenarios(rules: list[dict], start: float, base: float, peak: float | None) -> list[dict]:
    """대표 가격 경로 4개의 결과 문장. 경로는 진입가(base) 대비 %로 정의한다."""
    at = lambda pct: base * (1 + pct / 100)  # noqa: E731
    # 목표 지점은 1틱 넘겨 지나가게 한다(발동가는 올림/내림 정수라 경로 끝 반올림값이 1원 모자랄 수 있음).
    over = lambda pct: at(pct) + TICK_ETF  # noqa: E731
    under = lambda pct: at(pct) - TICK_ETF  # noqa: E731
    tp = [e for e in (_eff_pct(r, base) for r in rules if r["kind"] in ("take_profit_pct", "target_price")) if e and e > 0]
    sl = [e for e in (_eff_pct(r, base) for r in rules if r["kind"] in ("stop_loss_pct", "stop_price")) if e and e > 0]
    acts = [r["activate_pct"] for r in rules if r["kind"] == "trail_pct" and r.get("activate_pct") is not None]
    up_top = min(min(tp) if tp else (max(acts) + 2.0 if acts else 3.0), 30.0)
    down_bot = -min(max(sl) if sl else 3.0, 30.0)
    lowest_up = min(tp + acts) if (tp or acts) else 1.0
    if acts:
        mid = max(acts) * 1.5
    elif tp:
        mid = min(tp) * 0.6
    else:
        mid = 1.0
    if tp and mid >= min(tp):
        mid = min(tp) * 0.6
    after_peak = down_bot if sl else -3.0

    defs = [
        (f"곧장 상승 ({_p(up_top)}까지)", [start, over(up_top)], False),
        (f"{_p(mid)}까지 올랐다가 꺾임", [start, at(mid), under(after_peak)], False),
        (f"오르지 않고 하락 ({_p(down_bot)}까지)", [start, under(down_bot)], False),
        (f"{_p(lowest_up * 0.5)}까지 오른 뒤 15:20 도달", [start, at(lowest_up * 0.5)], True),
    ]
    out = []
    for title, way, eod in defs:
        way = [max(1.0, w) for w in way]
        res = _simulate(rules, base, peak, way)
        pct = (res["price"] / base - 1) * 100
        if res["exit"]:
            text = f"{describe_rule(res['rule'])} 조건으로 약 {_px(res['price'])}({_p(pct)})에 청산"
        elif eod:
            text = (
                f"조건에 닿지 않고 15:20 도달 — 약 {_px(res['price'])}({_p(pct)})에서 15:20~15:30 장마감 강제청산"
                "(수동 포지션은 오버나잇 예외 없음)"
            )
        else:
            text = f"조건에 닿지 않고 계속 보유 — 이 경로 끝 약 {_px(res['price'])}({_p(pct)})"
        out.append(
            {
                "title": title,
                "result": text,
                "exited": res["exit"],
                "exit_price": res["price"] if res["exit"] else None,
                "exit_pct": round(pct, 2) if res["exit"] else None,
                "rule": describe_rule(res["rule"]) if res["exit"] else None,
            }
        )
    return out


def analyze_rules(
    raw: list | None,
    reference_price: float | None,
    entry_price: float | None = None,
    peak: float | None = None,
    stats: dict | None = None,
) -> dict:
    """청산 조건 분석(순수 — 주문·DB·외부 호출 없음).

    - ``reference_price``: 즉시 발동 여부·호가 비교용 현재가(대기 중엔 최근 1분봉 종가). 모르면 None
    - ``entry_price``: 보유 중이면 진입가(없으면 기준가를 진입가로 가정)
    - ``stats``: 선택 ``{"avg_range_pct", "max_up_pct", "days"}`` 최근 일중 변동 통계(없으면 해당 문구 생략)

    Returns ``{"rules": 정규화된 조건, "errors": 차단 사유, "warnings": 확인 후 진행 가능,
    "infos": 참고 안내, "scenarios": 가격 경로별 결과}``. errors가 비어야 주문/수정을 받아준다.
    """
    rules, errors = _normalize(raw)
    ref = reference_price if reference_price and reference_price > 0 else None
    base = entry_price if entry_price and entry_price > 0 else ref
    by = {r["kind"]: r for r in rules}
    tp, sl, trail = by.get("take_profit_pct"), by.get("stop_loss_pct"), by.get("trail_pct")
    tgt, stp = by.get("target_price"), by.get("stop_price")
    structural_ok = not errors

    # -- errors ---------------------------------------------------------------
    if tp and tgt:
        errors.append("익절은 % 또는 목표가 중 하나만 걸 수 있습니다.")
    if sl and stp:
        errors.append("손절은 % 또는 손절가 중 하나만 걸 수 있습니다.")
    tgt_bad = bool(tgt and ref and tgt["value"] <= ref + _EPS)
    stp_bad = bool(stp and ref and stp["value"] >= ref - _EPS)
    if tgt_bad:
        errors.append(f"목표가({_px(tgt['value'])})가 현재가({_px(ref)}) 이하라 바로 발동합니다. 현재가보다 높게 정하세요.")
    if stp_bad:
        errors.append(f"손절가({_px(stp['value'])})가 현재가({_px(ref)}) 이상이라 바로 발동합니다. 현재가보다 낮게 정하세요.")
    up_rule = tp or tgt
    up_pct = _eff_pct(up_rule, base) if up_rule else None
    dn_rule = sl or stp
    dn_pct = _eff_pct(dn_rule, base) if dn_rule else None
    if up_rule and dn_rule and not (tgt_bad or stp_bad):
        # 가격 조건끼리는 기준가 없이도 비교할 수 있다.
        up_px = int(up_rule["value"]) if up_rule["kind"] in PRICE_KINDS else (trigger_price(up_rule, base, None) if base else None)
        dn_px = int(dn_rule["value"]) if dn_rule["kind"] in PRICE_KINDS else (trigger_price(dn_rule, base, None) if base else None)
        if up_px is not None and dn_px is not None and dn_px >= up_px:
            errors.append(f"손절({_px(dn_px)})이 익절({_px(up_px)})과 같거나 더 높아 모순입니다.")
    if trail and trail.get("activate_pct") is not None and up_pct is not None:
        act = trail["activate_pct"]
        if up_pct <= act + _EPS:
            errors.append(
                f"익절 {_p(up_pct)}가 먼저 닿아 '{_p(act)} 도달 후 고점 대비' 조건은 절대 작동하지 않습니다. "
                "익절 값을 더 크게 하거나 '도달 후' 값을 더 작게 하세요."
            )
    if trail and ref:
        drop_px = ref * trail["value"] / 100
        if drop_px < 2 * TICK_ETF - _EPS:
            errors.append(
                f"고점 대비 -{trail['value']:g}%는 약 {drop_px:.1f}원으로 호가단위({TICK_ETF}원) 2틱 미만입니다. "
                "호가 한두 칸 흔들림에도 바로 청산되므로 더 크게 정하세요."
            )

    # -- warnings / infos -----------------------------------------------------
    warnings: list[str] = []
    infos: list[str] = []
    if structural_ok:
        has_immediate_trail = trail is not None and trail.get("activate_pct") is None
        if not (sl or stp):
            if has_immediate_trail:
                warnings.append(
                    "손절 조건이 없습니다. '도달 후' 없는 고점 대비 하락이 사실상 손절 역할을 합니다 "
                    "(고점 기준이라 진입가 아래에서 청산될 수 있음)."
                )
            else:
                warnings.append(
                    "손절 조건이 없습니다. 손실 제한은 15:20 강제청산·외인 수급 반전뿐이며, 그 전까지는 얼마나 "
                    "떨어져도 팔리지 않습니다."
                )
        if has_immediate_trail:
            v = trail["value"]
            z = ((1.003 * (1 - v / 100)) - 1) * 100
            warnings.append(
                f"'도달 후' 없는 고점 대비 -{v:g}%는 진입 직후부터 작동합니다. 고점이 진입가 근처면 손실로 팔릴 수 "
                f"있습니다(예: +0.3%까지 갔다가 -{v:g}% 하락 시 약 {_p(z)}에 매도)."
            )
        if trail and trail.get("activate_pct") is not None and trail["value"] > trail["activate_pct"]:
            act, v = trail["activate_pct"], trail["value"]
            z = ((1 + act / 100) * (1 - v / 100) - 1) * 100
            warnings.append(
                f"고점 대비 하락폭(-{v:g}%)이 '도달 후' 값({_p(act)})보다 커서 트레일 청산가가 진입가 아래일 수 "
                f"있습니다(최악 약 {_p(z)})."
            )
        if up_pct is not None and dn_pct is not None and up_pct < dn_pct - _EPS:
            warnings.append(
                f"손익비가 1보다 작습니다: 익절 {_p(up_pct)} < 손절 {_p(-dn_pct)}. 이기는 횟수가 더 많아야 "
                "본전입니다."
            )
        if up_pct is not None and up_pct < 0.3:
            warnings.append(
                f"익절 {_p(up_pct)}는 수수료·슬리피지(체결 오차)에 비해 작아 실제로는 이익이 거의 남지 않을 수 있습니다."
            )
        if dn_pct is not None and dn_pct < 0.3:
            extra = ""
            if stats and stats.get("avg_range_pct") is not None:
                extra = f" (최근 {stats.get('days', 20)}일 평균 일중 변동 {stats['avg_range_pct']:.2f}%)"
            warnings.append(f"손절 {_p(-dn_pct)}는 일중 잡음만으로 쉽게 닿을 만큼 좁습니다{extra}.")
        if up_pct is not None and stats and stats.get("max_up_pct") is not None and up_pct > stats["max_up_pct"] + _EPS:
            tail = "사실상 고점 대비 하락 조건이 청산을 맡습니다." if trail else "이 익절은 대부분 닿지 않을 것입니다."
            warnings.append(
                f"익절 {_p(up_pct)}는 최근 {stats.get('days', 20)}일 일중 최대 상승폭({_p(stats['max_up_pct'])})보다 "
                f"큽니다. 거의 닿지 않으므로 {tail}"
            )
        if rules:
            infos.append(
                "엔진은 약 30초마다 가격을 확인합니다. 급변할 때는 조건 가격보다 불리하게 체결될 수 있습니다."
            )

    scenarios: list[dict] = []
    if structural_ok and rules and base and ref:
        scenarios = _scenarios(rules, ref if entry_price else base, base, peak)

    return {"rules": rules, "errors": errors, "warnings": warnings, "infos": infos, "scenarios": scenarios}


def validate_rules(raw: list | None, current_price: float | None) -> tuple[list[dict], list[str]]:
    """``analyze_rules``의 errors만 쓰는 호환 래퍼. ``(정규화 조건, 오류 목록)`` — 오류가 있으면 조건은 빈 목록."""
    res = analyze_rules(raw, current_price)
    return ([] if res["errors"] else res["rules"]), res["errors"]


def _base_peak(entry_price: float, peak: float | None) -> float:
    return max(peak, entry_price) if peak and peak > 0 else entry_price


def trail_active(rule: dict, entry_price: float, peak: float | None) -> bool:
    """트레일 조건이 유효한 상태인지(activate_pct 없으면 항상 True)."""
    act = rule.get("activate_pct")
    if act is None:
        return True
    return _base_peak(entry_price, peak) >= entry_price * (1 + act / 100) - _EPS


def trigger_price(rule: dict, entry_price: float, peak: float | None) -> int | None:
    """발동가(원, 정수). 이상 조건은 올림, 이하 조건은 내림(호가 단위 보정 없음).
    트레일은 현재 고점 기준(활성화 전이어도 '활성화되면 이 고점 기준' 값을 준다)."""
    kind, v = rule["kind"], float(rule["value"])
    if not entry_price or entry_price <= 0:
        return None
    if kind == "take_profit_pct":
        return int(-(-(entry_price * (1 + v / 100) - 1e-6) // 1))
    if kind == "stop_loss_pct":
        return int((entry_price * (1 - v / 100) + 1e-6) // 1)
    if kind == "trail_pct":
        return int((_base_peak(entry_price, peak) * (1 - v / 100) + 1e-6) // 1)
    if kind in PRICE_KINDS:
        return int(v)
    return None


def describe_rule(rule: dict) -> str:
    kind, v = rule["kind"], float(rule["value"])
    if kind == "take_profit_pct":
        return f"익절 +{v:g}%"
    if kind == "stop_loss_pct":
        return f"손절 -{v:g}%"
    if kind == "trail_pct":
        base = f"고점 대비 -{v:g}%"
        act = rule.get("activate_pct")
        return f"진입가 +{act:g}% 도달 후 {base}" if act is not None else base
    if kind == "target_price":
        return f"목표가 {v:,.0f}원"
    return f"손절가 {v:,.0f}원"


def describe_rules(rules: list[dict]) -> str:
    return " / ".join(describe_rule(r) for r in rules) if rules else "조건 없음"


def has_stop(rules: list[dict]) -> bool:
    """손실을 제한하는 조건(손절 %/손절가)이 하나라도 있는지."""
    return any(r["kind"] in ("stop_loss_pct", "stop_price") for r in rules)


def decide_manual_exit(
    rules: list[dict], entry_price: float, peak_price: float | None, current_price: float
) -> dict:
    """사용자 조건 판정(순수). ``peak_price``는 직전 저장 고점(None 가능).

    Returns ``{"action": "exit"|"peak_update"|"none", "new_peak_price": float,
    "reason": str, "rule": dict|None}`` — ``peak_update``는 고점이 올라 저장해야 함을 뜻한다.
    """
    prev_peak = _base_peak(entry_price, peak_price)
    new_peak = max(prev_peak, current_price)
    out: dict = {"action": "none", "new_peak_price": new_peak, "reason": "", "rule": None}
    if current_price is None or current_price <= 0 or not entry_price or entry_price <= 0:
        return out
    pct = (current_price / entry_price - 1) * 100

    hits: list[tuple[int, dict, str]] = []
    for rule in rules:
        kind, v = rule["kind"], float(rule["value"])
        text = None
        if kind == "take_profit_pct" and pct >= v - _EPS:
            text = f"익절 +{v:g}% 도달: 진입가 {entry_price:,.0f}원 대비 {pct:+.2f}%"
        elif kind == "stop_loss_pct" and pct <= -v + _EPS:
            text = f"손절 -{v:g}% 도달: 진입가 {entry_price:,.0f}원 대비 {pct:+.2f}%"
        elif kind == "target_price" and current_price >= v - _EPS:
            text = f"목표가 {v:,.0f}원 도달: 현재가 {current_price:,.0f}원 (진입가 대비 {pct:+.2f}%)"
        elif kind == "stop_price" and current_price <= v + _EPS:
            text = f"손절가 {v:,.0f}원 이탈: 현재가 {current_price:,.0f}원 (진입가 대비 {pct:+.2f}%)"
        elif kind == "trail_pct" and trail_active(rule, entry_price, new_peak):
            drop = (current_price / new_peak - 1) * 100
            if drop <= -v + _EPS:
                act = rule.get("activate_pct")
                pre = f"+{act:g}% 도달 후 " if act is not None else ""
                text = (
                    f"{pre}고점 대비 -{v:g}% 하락: 고점 {new_peak:,.0f}원 대비 {drop:+.2f}% "
                    f"(진입가 대비 {pct:+.2f}%)"
                )
        if text:
            hits.append((_PRIORITY[kind], rule, text))

    if hits:
        hits.sort(key=lambda h: h[0])
        _, rule, text = hits[0]
        out.update(action="exit", reason=text, rule=rule)
        return out
    if peak_price is None or new_peak > peak_price + _EPS:
        out["action"] = "peak_update"
    return out
