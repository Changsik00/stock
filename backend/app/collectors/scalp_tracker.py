"""스켈핑 후보 추적 기록 — 관찰 로그, 매매 신호 아님 (PLAN.md §5.7).

사용자 제안 배경: "스켈핑 후보 스코어(quant/screener.py::compute_scalp_scores)가
실제로 유효한지 검증하고 싶다" — 지금은 검증 없이 쓰고 있으므로, 후보를 DB에
기록해두고 이후 등락률을 정해진 시점(5/15/30/60분·당일 마감)마다 다시 조회해
채워두면 나중에 score와 실제 추이의 상관관계를 볼 근거가 쌓인다. **실제 매매를
시뮬레이션하지 않는다** — 진입/청산가, 매매 수수료·슬리피지 등은 반영하지
않는다(§5.7 원칙, §5 전체 원칙 "관찰 사실만 서술" 계승).

**§5.6에서 겪은 사고 교훈**: 이 앱에 이미 있던 "장중 누적" 기능
(``collectors/intraday_snapshot.py``)이 메모리에만 저장하다가 하루 동안
backend가 수십 차례 재시작(``--reload``)돼 데이터가 전부 날아갔다 — 이 모듈은
반드시 DB(``models.ScalpPick``)에 저장한다. 메모리 버퍼는 절대 쓰지 않는다.

## 설계 (PLAN.md §5.7 그대로)

1. **신규 진입 기록(1일 1종목 1회)** — ``routers.scalp._scored_candidates``로
   그날 스켈핑 후보를 스코어링(§5.2 그대로, 새 외부 호출 없음 — 이미 워밍된
   attention/value-rank 캐시만 재사용)한 뒤, 상위 ``TOP_N`` 중 오늘 아직
   ``scalp_pick``에 없는 종목만 INSERT한다. 같은 날 재등장은 자기상관을
   피하려고 건드리지 않는다.
2. **호라이즌 샘플링** — 오늘 행 중 entry_time + 5/15/30/60분이 이미 지났는데
   해당 change_rate_*m 컬럼이 아직 NULL인 것을 찾아, 그 code의 현재
   change_rate를 ``routers.scalp._change_rate_lookup``(attention 우선,
   value-rank 폴백 — §5.4-1 관례 재사용)로 채운다.
3. **당일 마감(EOD) 샘플링** — NXT 마감(``market_hours.is_nxt_closed``, 개별
   종목 기준 — §5.6-6) 이후엔 오늘 행 중 ``change_rate_eod``가 아직 NULL인
   것을 마지막 캐시 값으로 채운다.

세 단계 모두 **새 외부 API 호출을 하지 않는다** — attention/value-rank 캐시가
이미 값을 갖고 있지 않은 code(예: 거래대금 상위권에서 이탈)는 이번 폴링에서는
채우지 못하고 다음 폴링에서 재시도한다(최선 노력 — 완벽한 보장은 아니다).

## 스케줄링 배선

``collectors/live_refresh.py``의 60초 잡(``_run_live_refresh``)에서 호출된다.
다만 그 잡의 최상단 NXT 게이트(``is_nxt_closed`` True면 조기 반환)가 **이
모듈까지 걸러버리면 안 된다** — 당일 마감 채우기(위 3번)는 정의상 NXT가
막 마감된 시점에 실행돼야 하는데, 이 모듈 자체는 (이미 설명했듯) 새 외부
호출이 전혀 없으므로 그 게이트 밖에서 매 폴링 호출돼도 비용이 없다. 자세한
배선은 ``live_refresh._run_live_refresh`` 참고.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..market_hours import KST, is_nxt_closed
from ..models import ScalpPick
from ..routers.scalp import _change_rate_lookup, _fetch_live_payloads, _scored_candidates

logger = logging.getLogger(__name__)

# 상위 몇 위까지 진입 기록 대상으로 삼을지 (PLAN.md §5.7 설계 1번 "예 10위").
TOP_N = 10

# (호라이즌 분, 채울 컬럼 이름) — models.ScalpPick의 change_rate_*m 컬럼과 1:1 대응.
HORIZONS_MINUTES: tuple[tuple[int, str], ...] = (
    (5, "change_rate_5m"),
    (15, "change_rate_15m"),
    (30, "change_rate_30m"),
    (60, "change_rate_60m"),
)


async def record_new_entries(session: AsyncSession, now_kst: dt.datetime) -> int:
    """오늘 스켈핑 후보 상위 ``TOP_N`` 중 아직 오늘 기록되지 않은 종목을 진입
    기록한다(1일 1종목 1회, PLAN.md §5.7 설계 1번). entry_rank는 스코어
    내림차순 순위(1이 최상위)다. 새 INSERT가 있으면 커밋하고, 삽입된 행 수를
    반환한다."""
    today = now_kst.date()
    scored, _value_payload = await _scored_candidates(session)
    top = scored[:TOP_N]
    if not top:
        return 0

    existing_codes = set(
        (
            await session.execute(select(ScalpPick.code).where(ScalpPick.date == today))
        ).scalars().all()
    )

    inserted = 0
    for rank, c in enumerate(top, start=1):
        code = c["code"]
        if code in existing_codes:
            continue
        session.add(
            ScalpPick(
                date=today,
                code=code,
                name=c.get("name") or code,
                market=c.get("market"),
                entry_time=now_kst,
                entry_rank=rank,
                entry_score=c.get("score"),
                entry_change_rate=c.get("change_rate"),
                entry_turnover=c.get("turnover"),
                in_attention_top_at_entry=bool(c.get("in_attention_top")),
            )
        )
        existing_codes.add(code)
        inserted += 1

    if inserted:
        await session.commit()
    return inserted


async def fill_horizons(session: AsyncSession, now_kst: dt.datetime) -> int:
    """오늘 행 중 도래한(entry_time + N분이 이미 지난) 호라이즌 컬럼이 아직
    NULL인 것을 현재 change_rate로 채운다(PLAN.md §5.7 설계 2번). 캐시에 그
    code의 값이 없으면(예: 거래대금 상위권 이탈) 이번엔 건너뛰고 다음 폴링에서
    재시도한다. 채운 컬럼 수를 반환한다."""
    today = now_kst.date()
    rows = (
        await session.execute(select(ScalpPick).where(ScalpPick.date == today))
    ).scalars().all()

    pending = [
        (row, column)
        for row in rows
        for minutes, column in HORIZONS_MINUTES
        if getattr(row, column) is None
        and now_kst >= row.entry_time + dt.timedelta(minutes=minutes)
    ]
    if not pending:
        return 0

    value_payload, attention_payload = await _fetch_live_payloads(session)
    rates = _change_rate_lookup(value_payload, attention_payload)

    filled = 0
    for row, column in pending:
        rate = rates.get(row.code)
        if rate is None:
            continue
        setattr(row, column, rate)
        filled += 1

    if filled:
        await session.commit()
    return filled


async def fill_eod(session: AsyncSession, now_kst: dt.datetime) -> int:
    """NXT 마감 이후(``is_nxt_closed``) 오늘 행 중 ``change_rate_eod``가 아직
    NULL인 것을 마지막 캐시 값으로 채운다(PLAN.md §5.7 설계 3번). 마감 전이면
    아무 것도 하지 않는다. 채운 행 수를 반환한다."""
    if not is_nxt_closed(now_kst):
        return 0

    today = now_kst.date()
    rows = (
        await session.execute(
            select(ScalpPick).where(
                ScalpPick.date == today, ScalpPick.change_rate_eod.is_(None)
            )
        )
    ).scalars().all()
    if not rows:
        return 0

    value_payload, attention_payload = await _fetch_live_payloads(session)
    rates = _change_rate_lookup(value_payload, attention_payload)

    filled = 0
    for row in rows:
        rate = rates.get(row.code)
        if rate is None:
            continue
        row.change_rate_eod = rate
        filled += 1

    if filled:
        await session.commit()
    return filled


async def track_scalp_picks(
    session: AsyncSession, now_kst: dt.datetime | None = None
) -> dict[str, int]:
    """PLAN.md §5.7 전체 흐름(신규 진입 기록 + 호라이즌 채우기 + EOD 채우기)을
    한 번에 실행한다. ``collectors/live_refresh.py``의 60초 잡 끝에서 호출된다.

    신규 진입 기록은 NXT 개장 중일 때만 시도한다 — 마감 중엔 스코어링 재료
    자체가 마지막(마감 전) 캐시 스냅샷의 재사용이라, 그걸 "새 진입"으로
    오기록하면 실제로는 마감 전에 이미 지나간 시점을 마감 후 시각으로 잘못
    타임스탬프 찍는 사고가 난다. 호라이즌/EOD 채우기는 마감 여부와 무관하게
    항상 시도한다 — 둘 다 새 외부 호출이 없어(이미 캐시된 값만 재사용) 마감
    중에 호출해도 비용이 없고, 마감 직후 EOD를 채우려면 오히려 마감 중에도
    호출돼야 한다."""
    now_kst = now_kst or dt.datetime.now(KST)
    result = {"entries": 0, "horizons": 0, "eod": 0}

    if not is_nxt_closed(now_kst):
        result["entries"] = await record_new_entries(session, now_kst)

    result["horizons"] = await fill_horizons(session, now_kst)
    result["eod"] = await fill_eod(session, now_kst)
    return result
