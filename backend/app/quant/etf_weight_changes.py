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
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import EtfHolding, Stock

# 한국 ETF 네이밍 규정상 액티브 펀드는 상품명에 이 문자열을 반드시 포함한다
# (모듈 독스트링 "액티브 펀드 식별" 참고 — §5.25 실측: 358개 중 65개).
ACTIVE_ETF_NAME_MARKER = "액티브"

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


async def compute_etf_weight_changes(
    session: AsyncSession,
    code: str | None = None,
    active_only: bool = False,
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
        event: "신규편입"/"비중확대"/"비중축소"/"편출" 중 하나로 필터. None이면
            4종 전부.
        limit: 반환할 최대 행 수(정렬 후 자름).

    Returns:
        비교할 스냅샷이 2개 미만이면 ``{"prev_date": None, "curr_date": None,
        "changes": [], "reason": "..."}`` (에러가 아니라 "아직 이력이 부족한
        정상 상태" — 예외를 던지지 않는다).

        그렇지 않으면 ``{"prev_date": iso, "curr_date": iso, "changes": [
        {"code", "name", "etf_code", "etf_name", "is_active", "event",
        "prev_weight", "curr_weight", "delta"}, ...]}``. prev_weight/curr_weight/
        delta는 이벤트가 신규편입/편출이면 한쪽이 None(모듈 독스트링 "delta = None"
        참고). abs(delta) 내림차순 정렬(신규편입/편출은 `_sort_key` 참고).
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
        if active_only and not is_active:
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
                "event": c["event"],
                "prev_weight": c["prev_weight"],
                "curr_weight": c["curr_weight"],
                "delta": c["delta"],
            }
        )

    changes.sort(key=_sort_key)
    changes = changes[:limit]

    return {
        "prev_date": prev_date.isoformat(),
        "curr_date": curr_date.isoformat(),
        "changes": changes,
    }
