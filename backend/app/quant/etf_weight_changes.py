"""ETF 비중 변화 감지 (PLAN.md §5.25, 2026-07-24 사용자 요청·알테오젠 사례).

**배경**: 사용자가 알테오젠(196170)이 "개별 종목 매수가 아니라 ETF로 수급을
받는 것 같다"고 관찰했다. `stock_flow`(투자자별 직접 수급)로 직접 확인한
결과 그 관찰과는 반대로 그날은 오히려 외국인·기관이 사고 개인이 판 날이었지만
(§5.25 "알테오젠 직접 확인 결과" 참고), **ETF 비중 변화는 실제로 사용자
관찰이 맞았다** — `etf_holdings`(§4.5, ETF별 일별 top10 구성 스냅샷)를 조회해
보니 알테오젠을 담은 액티브 펀드 여럿이 최근 며칠 사이 비중을 꾸준히 늘리고
있었다(RISE 바이오TOP10액티브 8.75%→9.40%, KoAct 바이오헬스케어액티브가
하루 만에 +0.76%p, ACE K바이오코스닥액티브가 새로 top10에 등장 등). 이
모듈은 그 수작업 조회를 "어떤 종목이든" 반복 실행할 수 있는 재사용 가능한
스크리너로 일반화한다 — 알테오젠 전용이 아니라, `code`를 지정하면 특정
종목, 지정하지 않으면 시장 전체를 스크리닝한다(§5.25 "범위" 절 — 엔드포인트
하나로 두 용도를 겸한다, 과설계 방지).

**비교 기준: "최근 2개 스냅샷 날짜", 고정 일수 아님**: `etf_master` 수집은
매일 정확히 실행되지 않는다 — 실제로 2026-07-18 다음 수집이 07-21까지
3일 비어 있었다(§5.25 실측). "어제 대비 오늘"처럼 날짜 차이를 고정하면 수집이
비는 날에는 비교 자체가 불가능해지므로, `etf_holdings`에 **실제로 존재하는
날짜 중 가장 최근 2개**를 찾아 그 사이의 변화를 본다(`_two_most_recent_dates`).
두 날짜 사이 실제 간격(1일이든 3일이든)은 호출자에게 그대로 노출된다
(`prev_date`/`curr_date`) — 침묵하지 않고 정직하게 보여준다.

**핵심 한계 — top10 스냅샷이라는 것을 항상 명시한다** (collectors/etf_master.py
모듈독스트링 "기존 (etf_code, date) 행을 지우고 다시 넣는다" 참고): `etf_holdings`는
ETF의 **전체 포트폴리오가 아니라 top10 구성종목**만 매일 스냅샷한다. 따라서:

- "신규편입"(이전 스냅샷의 top10에 없다가 이번에 나타남)은 "이 ETF가 이
  종목을 방금 처음 샀다"는 뜻이 **아닐 수 있다** — 이미 작게 보유하고
  있었는데 비중이 커져서 top10 안으로 올라왔을 가능성과 구분할 수 없다.
- "편출"(있었다가 사라짐)도 마찬가지로 "이 ETF가 이 종목을 팔았다"는 뜻이
  **아닐 수 있다** — 다른 보유 종목이 더 커져서 순위가 밀려났을 가능성과
  구분할 수 없다.
- 이 데이터만으로는 두 가능성 중 어느 쪽인지 판정할 수 없다(§5.25 실측 각주,
  flow_path.py가 이미 쓰는 것과 동일한 "관측 사실만 서술, 과대해석 금지"
  원칙). 이 모듈의 반환값·이 값을 노출하는 라우터·프런트 문구 어디에도
  "매수"/"매도"라는 확정적 표현을 쓰지 않고 "신규편입"/"편출"이라는 중립적
  이벤트명만 쓴다.

**액티브 펀드 식별 — 이름 기반 휴리스틱, 근거 있음**: `stocks`/`etf_holdings`
스키마에는 "이 ETF가 액티브 펀드인가"를 나타내는 전용 컬럼이 없다(models.py
참고). 대신 한국 ETF 네이밍 규정상 액티브 펀드는 상품명에 "액티브"를 반드시
포함해야 하므로, `stocks.name`에 `ACTIVE_ETF_NAME_MARKER`("액티브")가 들어
있는지로 판정한다 — 편의상의 근사가 아니라 **실제 수집 데이터로 검증한 신뢰할
수 있는 기준**이다(§5.25 실측: 추적 중인 ETF 358개 중 65개가 이름에 "액티브"를
포함, 전부 실제로 액티브 펀드였다). 액티브 펀드의 비중 변화는 지수 추종 패시브
ETF의 기계적 리밸런싱과 달리 펀드매니저의 재량적 종목 선택을 반영할 가능성이
커서 `is_active` 플래그로 구분해 노출한다.

**노이즈 필터(`MIN_WEIGHT_DELTA_PCT`)**: 두 스냅샷 모두에 존재하는 종목의
비중차가 작으면(반올림·미세 리밸런싱) 매일 대부분의 (ETF, 종목) 쌍이
"비중확대"/"비중축소" 이벤트로 잡혀 정말 의미 있는 변화가 묻힌다. 임계값
미만인 변화는 이벤트로 분류하지 않고 결과에서 완전히 제외한다("변화없음"이라는
이벤트 타입을 만들지 않는다 — 그냥 조용히 뺀다). 신규편입/편출은 애초에 비교할
"더 작은 쪽" 크기가 없으므로(한쪽이 아예 없음) 이 임계값을 적용하지 않고
항상 포함한다.

**레버리지/인버스 판정 + 실제 수급·가격 컨텍스트(PLAN.md §5.26, 하이닉스
사례)**: 사용자가 SK하이닉스(000660)에서 "KODEX 반도체레버리지"의 비중이
하루 만에 46.87%→53.41%(+6.54%p)로 급등한 사례를 지적했다 — 레버리지 ETF는
목표 배율(예: 2배)을 유지하려고 **매일 기계적으로 리밸런싱**하므로, 이런
변화는 "의미 있는 자금 유입(스마트 머니 신호)"이 아니라 레버리지 구조 자체의
부산물일 가능성이 크다. `is_leveraged`(이름에 "레버리지" 또는 "인버스" 포함 —
`is_active`와 동일한 이름 기반 휴리스틱, §5.26 실측: 추적 358개 ETF 중 48개가
해당)로 이 가능성을 플래그해 노출한다. `exclude_leveraged=True`면 이 플래그가
True인 행을 결과에서 아예 뺀다(`active_only`와 같은 방식의 필터, 기본값은
False — 숨기지 않고 항상 플래그로 보여주는 쪽을 기본으로 한다).

추가로 각 변화 행에는 그 종목의 같은 기간(`prev_date`~`curr_date`, 양끝 포함)
**실제** 수급·가격 변동을 나란히 붙인다 — `stock_flow_delta`(외국인+기관계
net_value 합, routers/scalp.py `_stock_flow_lookup`과 동일한 투자자 조합·NULL
스킵 관례를 날짜 범위로 넓힌 것)와 `price_change_pct`(`stock_ohlcv` 종가 기준
등락률, 소수 2자리). **이 두 값은 통계적 검증이 아니다** — ETF 비중 변화가
실제로 그 종목의 수급/가격에 반영됐는지 사람이 눈으로 대조 판단할 수 있게
곁들이는 참고 수치일 뿐, 상관관계를 증명하거나 반증하지 않는다(엄밀한 통계
검증은 `etf_weight_backtest.py`가 별도로 다루며, 그마저도 현재 표본(n≈3
날짜쌍)으로는 신뢰할 수 없다 — 그 모듈 독스트링 참고). 둘 다 **결측이면
`None`이지 `0`이 아니다** — 해당 구간에 `stock_flow` 행이 전혀 없거나
`stock_ohlcv`에 두 날짜 중 하나라도 없으면(수집 공백) 억지로 0이나 다른
날짜로 대체하지 않고 정직하게 "알 수 없음"으로 남긴다(대체하면 이 비중
변화 구간과 다른 기간을 비교하는 셈이 되어 침묵하는 것보다 나쁘다).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import EtfHolding, Stock, StockFlow, StockOhlcv

# 한국 ETF 네이밍 규정상 액티브 펀드는 상품명에 이 문자열을 반드시 포함한다
# (모듈 독스트링 "액티브 펀드 식별" 참고 — §5.25 실측: 358개 중 65개).
ACTIVE_ETF_NAME_MARKER = "액티브"

# 레버리지/인버스 ETF는 상품명에 이 중 하나를 반드시 포함한다(모듈 독스트링
# "레버리지/인버스 판정" 참고 — §5.26 실측: 358개 중 48개). 둘 중 어느 쪽
# 문자열이든 매칭되면 "기계적 리밸런싱 가능성 있음"으로 간주한다.
LEVERAGED_INVERSE_NAME_MARKERS = ("레버리지", "인버스")

# stock_flow_delta 집계에 쓰는 투자자 조합 — routers/scalp.py `_stock_flow_lookup`
# (`_FLOW_INVESTORS`)과 동일한 관례를 이 모듈에서도 그대로 쓴다.
FLOW_INVESTORS = ("외국인", "기관계")

# 이 값(%p) 미만인 비중 변화는 노이즈로 간주해 비중확대/비중축소 분류에서
# 제외한다(모듈 독스트링 "노이즈 필터" 참고). 신규편입/편출에는 적용하지 않는다.
MIN_WEIGHT_DELTA_PCT = 0.2

EVENT_NEW = "신규편입"
EVENT_EXPAND = "비중확대"
EVENT_SHRINK = "비중축소"
EVENT_REMOVED = "편출"


async def _two_most_recent_dates(session: AsyncSession) -> tuple[dt.date, dt.date] | None:
    """`etf_holdings`에 실제로 존재하는 날짜 중 가장 최근 2개를 (이전, 최근)
    순서로 반환한다. 서로 다른 날짜가 2개 미만이면(수집이 아직 한 번뿐이거나
    전혀 없음) 비교 자체가 불가능하므로 ``None``."""
    stmt = select(EtfHolding.date).distinct().order_by(EtfHolding.date.desc()).limit(2)
    dates = (await session.execute(stmt)).scalars().all()
    if len(dates) < 2:
        return None
    curr_date, prev_date = dates[0], dates[1]
    return prev_date, curr_date


def _classify(prev_weight: float | None, curr_weight: float | None) -> tuple[str, float | None] | None:
    """(prev, curr) 비중 쌍 -> (이벤트명, delta) | None(노이즈라 제외).

    한쪽에만 존재하면 신규편입/편출(delta=None). 둘 다 존재하면 차이가
    MIN_WEIGHT_DELTA_PCT 이상일 때만 비중확대/비중축소로 분류하고, 그
    미만이면 None을 반환해 호출자가 완전히 건너뛰게 한다(모듈 독스트링
    "노이즈 필터" 참고)."""
    if prev_weight is None and curr_weight is not None:
        return EVENT_NEW, None
    if prev_weight is not None and curr_weight is None:
        return EVENT_REMOVED, None
    if prev_weight is None and curr_weight is None:
        return None  # 이론상 도달 불가(호출자가 둘 중 하나라도 있는 키만 넘김)

    delta = curr_weight - prev_weight
    if delta >= MIN_WEIGHT_DELTA_PCT:
        return EVENT_EXPAND, delta
    if delta <= -MIN_WEIGHT_DELTA_PCT:
        return EVENT_SHRINK, delta
    return None


def _sort_key(change: dict) -> tuple[int, float]:
    """정렬 키 — delta가 있으면(비중확대/비중축소) |delta| 내림차순을 최우선으로
    하고, delta가 없는 신규편입/편출은 그 뒤로 밀어(1군), 그 안에서는 남아있는
    쪽 비중(신규편입=curr_weight, 편출=prev_weight) 내림차순으로 2차 정렬한다
    (판단 근거: delta가 없는 이벤트끼리는 "변화 크기"를 정의할 수 없으니 대신
    "그 시점 비중이 얼마나 큰 편입/이탈이었는가"를 대리 지표로 쓴다 — §5.25-1
    작업 지시가 명시적으로 허용한 판단 재량)."""
    if change["delta"] is not None:
        return (0, -abs(change["delta"]))
    weight = change["curr_weight"] if change["curr_weight"] is not None else change["prev_weight"]
    return (1, -(weight or 0.0))


async def _stock_flow_delta_map(
    session: AsyncSession, codes: list[str], prev_date: dt.date, curr_date: dt.date
) -> dict[str, int]:
    """code -> [prev_date, curr_date] 구간(양끝 포함) 외국인+기관계 net_value 합계
    (모듈 독스트링 "레버리지/인버스 판정 + 실제 수급·가격 컨텍스트" 참고 —
    routers/scalp.py `_stock_flow_lookup`의 단일 날짜 조회를 날짜 범위로 넓힌
    버전, 투자자 조합·NULL 스킵 관례는 그대로 재사용). net_value가 NULL인 행은
    합계에서 제외한다(0으로 취급하면 진짜 순매수 0과 구분이 안 된다). 그 구간에
    해당 code의 유효한 행이 전혀 없으면 반환 dict에 그 code가 아예 없다 —
    호출부가 ``.get(code)``로 자연스럽게 ``None``(값 없음, 0이 아님)을 받는다.
    """
    if not codes:
        return {}
    stmt = (
        select(StockFlow.code, func.sum(StockFlow.net_value))
        .where(
            StockFlow.code.in_(codes),
            StockFlow.date.between(prev_date, curr_date),
            StockFlow.investor.in_(FLOW_INVESTORS),
            StockFlow.net_value.isnot(None),
        )
        .group_by(StockFlow.code)
    )
    rows = (await session.execute(stmt)).all()
    # Postgres SUM(bigint) -> numeric(Decimal) — int로 캐스트해 이 모듈의 나머지
    # 정수/실수 연산과 깔끔히 섞이게 한다(routers/flow_rank.py의 동일 캐스팅
    # 주석 참고).
    return {code: int(total) for code, total in rows}


async def _price_change_pct_map(
    session: AsyncSession, codes: list[str], prev_date: dt.date, curr_date: dt.date
) -> dict[str, float]:
    """code -> prev_date 대비 curr_date `stock_ohlcv.close` 변동률(%, 소수 2자리).
    두 날짜 중 하나라도 그 code의 행이 없으면(수집 공백) 그 code는 반환 dict에
    없다 — 다른 가까운 날짜로 대체하지 않는다(모듈 독스트링 참고: 대체하면 이
    비중 변화 구간과 다른 기간을 비교하게 되어 "알 수 없음"보다 나쁘다)."""
    if not codes:
        return {}
    stmt = select(StockOhlcv.code, StockOhlcv.date, StockOhlcv.close).where(
        StockOhlcv.code.in_(codes), StockOhlcv.date.in_((prev_date, curr_date))
    )
    rows = (await session.execute(stmt)).all()
    close_map: dict[tuple[str, dt.date], int] = {
        (code, date): close for code, date, close in rows if close is not None
    }

    result: dict[str, float] = {}
    for code in codes:
        prev_close = close_map.get((code, prev_date))
        curr_close = close_map.get((code, curr_date))
        if prev_close is None or curr_close is None or prev_close == 0:
            continue
        result[code] = round((curr_close - prev_close) / prev_close * 100, 2)
    return result


async def compute_etf_weight_changes(
    session: AsyncSession,
    code: str | None = None,
    active_only: bool = False,
    exclude_leveraged: bool = False,
    event: str | None = None,
    limit: int = 50,
) -> dict:
    """최근 2개 `etf_holdings` 스냅샷을 비교해 (ETF, 종목) 쌍별 비중 변화를
    분류한다(모듈 독스트링 참고 — top10 스냅샷 한계는 항상 결과와 함께
    해석해야 한다).

    Args:
        code: 지정하면 이 종목코드를 담은 ETF들의 변화만(§5.25 "종목 지정
            조회" — 예: 196170). None이면 시장 전체 스크리닝.
        active_only: True면 액티브 펀드(이름에 "액티브" 포함)의 변화만 남긴다.
        exclude_leveraged: True면 레버리지/인버스 ETF(이름에 "레버리지" 또는
            "인버스" 포함, 모듈 독스트링 참고)를 경유한 변화를 결과에서 뺀다.
            기본 False — 숨기지 않고 `is_leveraged` 플래그로 항상 노출한다.
        event: "신규편입"/"비중확대"/"비중축소"/"편출" 중 하나로 필터. None이면
            4종 전부.
        limit: 반환할 최대 행 수(정렬 후 자름).

    Returns:
        비교할 스냅샷이 2개 미만이면 ``{"prev_date": None, "curr_date": None,
        "changes": [], "reason": "..."}`` (에러가 아니라 "아직 이력이 부족한
        정상 상태" — 예외를 던지지 않는다).

        그렇지 않으면 ``{"prev_date": iso, "curr_date": iso, "changes": [
        {"code", "name", "etf_code", "etf_name", "is_active", "is_leveraged",
        "event", "prev_weight", "curr_weight", "delta", "stock_flow_delta",
        "price_change_pct"}, ...]}``. prev_weight/curr_weight/delta는 이벤트가
        신규편입/편출이면 한쪽이 None(모듈 독스트링 "delta = None" 참고).
        abs(delta) 내림차순 정렬(신규편입/편출은 `_sort_key` 참고).
        `stock_flow_delta`/`price_change_pct`는 정렬·`limit` 절단 **이후** 최종
        노출 행에 대해서만 조회한다(어차피 화면에 안 보일 후보까지 조회하지
        않기 위함) — 모듈 독스트링 "레버리지/인버스 판정 + 실제 수급·가격
        컨텍스트" 참고, 둘 다 결측이면 None(0이 아니다).
    """
    dates = await _two_most_recent_dates(session)
    if dates is None:
        return {
            "prev_date": None,
            "curr_date": None,
            "changes": [],
            "reason": "etf_holdings 스냅샷이 2개 미만이라 비중 변화를 계산할 수 없음",
        }
    prev_date, curr_date = dates

    prev_filters = [EtfHolding.date == prev_date]
    curr_filters = [EtfHolding.date == curr_date]
    if code is not None:
        prev_filters.append(EtfHolding.stock_code == code)
        curr_filters.append(EtfHolding.stock_code == code)

    prev_rows = (await session.execute(select(EtfHolding).where(*prev_filters))).scalars().all()
    curr_rows = (await session.execute(select(EtfHolding).where(*curr_filters))).scalars().all()

    prev_map: dict[tuple[str, str], float] = {
        (r.etf_code, r.stock_code): float(r.weight) for r in prev_rows if r.weight is not None
    }
    curr_map: dict[tuple[str, str], float] = {
        (r.etf_code, r.stock_code): float(r.weight) for r in curr_rows if r.weight is not None
    }

    raw_changes: list[dict] = []
    for key in set(prev_map) | set(curr_map):
        etf_code, stock_code = key
        prev_weight = prev_map.get(key)
        curr_weight = curr_map.get(key)
        classified = _classify(prev_weight, curr_weight)
        if classified is None:
            continue  # 노이즈(임계값 미만) — 완전히 제외
        event_type, delta = classified
        raw_changes.append(
            {
                "etf_code": etf_code,
                "stock_code": stock_code,
                "event": event_type,
                "prev_weight": prev_weight,
                "curr_weight": curr_weight,
                "delta": delta,
            }
        )

    # 이름 해석(ETF 자신 + 보유 종목 모두 stocks 테이블에서) — 한 번의 쿼리로.
    all_codes = {c["etf_code"] for c in raw_changes} | {c["stock_code"] for c in raw_changes}
    name_map: dict[str, str] = {}
    if all_codes:
        rows = (
            await session.execute(select(Stock.code, Stock.name).where(Stock.code.in_(all_codes)))
        ).all()
        name_map = dict(rows)

    changes: list[dict] = []
    for c in raw_changes:
        etf_name = name_map.get(c["etf_code"], c["etf_code"])
        is_active = ACTIVE_ETF_NAME_MARKER in etf_name
        is_leveraged = any(marker in etf_name for marker in LEVERAGED_INVERSE_NAME_MARKERS)
        if active_only and not is_active:
            continue
        if exclude_leveraged and is_leveraged:
            continue
        if event is not None and c["event"] != event:
            continue
        changes.append(
            {
                "code": c["stock_code"],
                "name": name_map.get(c["stock_code"], c["stock_code"]),
                "etf_code": c["etf_code"],
                "etf_name": etf_name,
                "is_active": is_active,
                "is_leveraged": is_leveraged,
                "event": c["event"],
                "prev_weight": c["prev_weight"],
                "curr_weight": c["curr_weight"],
                "delta": c["delta"],
            }
        )

    changes.sort(key=_sort_key)
    changes = changes[:limit]

    # 실제 수급/가격 컨텍스트는 최종 노출 행(정렬+limit 절단 이후)에 대해서만
    # 조회한다(모듈 독스트링 참고 — 화면에 안 보일 후보까지 쿼리하지 않는다).
    # 같은 종목이 여러 ETF 행에 걸쳐 나타나도 그 종목 자체의 수급/가격 컨텍스트는
    # 동일하므로 종목당 한 번만 조회해 모든 매칭 행에 그대로 붙인다.
    if changes:
        stock_codes = sorted({c["code"] for c in changes})
        flow_map = await _stock_flow_delta_map(session, stock_codes, prev_date, curr_date)
        price_map = await _price_change_pct_map(session, stock_codes, prev_date, curr_date)
        for c in changes:
            c["stock_flow_delta"] = flow_map.get(c["code"])
            c["price_change_pct"] = price_map.get(c["code"])

    return {
        "prev_date": prev_date.isoformat(),
        "curr_date": curr_date.isoformat(),
        "changes": changes,
    }
