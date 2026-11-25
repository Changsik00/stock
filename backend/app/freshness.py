"""데이터 신선도 레지스트리 + 감시 (PLAN.md Phase 5.120 C2/C4).

"장 마감 후 낡은 값" 버그가 반복돼서(Phase 5.4~5.119 12회 이상) 시간대별 기대값을 한 표로 선언하고,
감시 잡(scheduler ``_run_freshness_check_job``)이 그 시간대에 DB 실제값과 대조한다.

- ``ready_after``: 이 시각(KST) 이후엔 오늘 거래일 행이 있어야 한다.
- ``final_after``: 이 시각 이후 수집분이어야 "확정"(예: NXT 마감 20:00). 이 시각이 지났는데
  수집 시각이 그 전이면 ``early``(확정 전 값이 굳은 상태).
- ``lag_trading_days``: T+1 소스면 1.
- 수집 시각: value_rank/stock_ohlcv는 ``collected_at`` 컬럼(NULL=과거 행), 없으면 collect_log.ran_at.

새 데이터셋은 ``DATASETS``에 등록한다(CLAUDE.md "테스트·검증" 절).
판정은 DB 조회(``_fetch_*``)와 분리된 순수 함수(``expected_date``, ``judge``)로 테스트한다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .krx_calendar import is_krx_closed

KST = dt.timezone(dt.timedelta(hours=9))

READY_BATCH = dt.time(18, 10)  # 18:00 본배치(약 5~10분 소요) 직후
FINAL_NXT = dt.time(20, 0)  # NXT 애프터마켓 마감


@dataclass(frozen=True)
class Dataset:
    key: str
    name: str  # 한국어 표시명
    table: str
    date_col: str = "date"
    where: str = ""  # 고정 조건(정적 문자열만) — 예: "market IN ('kospi','kosdaq')"
    job: str | None = None  # collect_log.job
    ready_after: dt.time = READY_BATCH
    final_after: dt.time | None = None
    lag_trading_days: int = 0
    has_collected_at: bool = False  # 테이블에 collected_at 컬럼이 있는지


DATASETS: list[Dataset] = [
    Dataset("index_ohlcv", "지수 일봉", "index_ohlcv", where="market IN ('kospi','kosdaq')", job="ohlcv"),
    Dataset("market_flow", "투자자별 수급", "market_flow", where="market IN ('kospi','kosdaq')", job="market_flow"),
    Dataset("market_breadth", "등락 종목수", "market_breadth", job="breadth"),
    # program_flow 잡은 program_trade(종목별)가 아니라 macro_series(prog_*)에 쓴다
    Dataset("program_flow", "프로그램매매", "macro_series", where="series = 'prog_arb_kospi'", job="program_flow"),
    Dataset("group_snapshot", "업종·테마", "group_snapshot", job="group_snapshot"),
    Dataset(
        "value_rank", "거래대금 상위", "value_rank", job="value_rank",
        final_after=FINAL_NXT, has_collected_at=True,
    ),
    Dataset(
        "stock_ohlcv", "관심종목 일봉", "stock_ohlcv",
        where="code IN (SELECT code FROM watchlist)", job="stock_ohlcv_watchlist",
        final_after=FINAL_NXT, has_collected_at=True,
    ),
    Dataset("volume_profile_daily", "매물대", "volume_profile_daily", job="volume_profile_daily"),
    Dataset("flow_rank", "수급 상위 종목", "flow_rank", job="flow_rank", ready_after=dt.time(21, 35)),
    Dataset("etf_stats", "ETF 순유입", "etf_stats", job="etf_master", lag_trading_days=1),
]


# ---------------------------------------------------------------- 날짜 계산 (순수)

def _aware(now: dt.datetime) -> dt.datetime:
    """naive면 KST로 간주."""
    return now if now.tzinfo else now.replace(tzinfo=KST)


def is_trading_day(day: dt.date) -> bool:
    return not is_krx_closed(day)


def prev_trading_day(day: dt.date) -> dt.date:
    """day보다 엄격히 앞선 직전 거래일."""
    d = day - dt.timedelta(days=1)
    while not is_trading_day(d):
        d -= dt.timedelta(days=1)
    return d


def expected_date(now_kst: dt.datetime, ready_after: dt.time, lag: int = 0) -> dt.date:
    """now 시점에 DB에 있어야 할 최신 날짜.

    오늘이 거래일이고 now가 ready_after 이후면 오늘, 아니면 직전 거래일. lag(T+1 등)만큼 거래일을 더 뺀다.
    """
    now = _aware(now_kst).astimezone(KST)
    today = now.date()
    if is_trading_day(today) and now.time() >= ready_after:
        base = today
    else:
        base = prev_trading_day(today)
    for _ in range(lag):
        base = prev_trading_day(base)
    return base


def judge(
    ds: Dataset,
    now_kst: dt.datetime,
    expected: dt.date,
    actual: dt.date | None,
    collected_at: dt.datetime | None,
) -> tuple[str, str]:
    """(status, detail) 판정 — ok / stale / early. DB 조회와 분리된 순수 함수."""
    now = _aware(now_kst).astimezone(KST)
    if actual is None:
        return "stale", f"데이터 없음(기대 {expected})"
    if actual < expected:
        return "stale", f"최신 {actual} < 기대 {expected}"
    if ds.final_after is not None and ds.lag_trading_days == 0:
        threshold = dt.datetime.combine(expected, ds.final_after, tzinfo=KST)
        if now >= threshold and actual == expected:
            if collected_at is None:
                return "ok", "수집 시각 미상"
            if _aware(collected_at) < threshold:
                local = _aware(collected_at).astimezone(KST).strftime("%H:%M")
                return "early", f"확정 시각 {ds.final_after:%H:%M} 전 수집분({local})"
    return "ok", ""


# ---------------------------------------------------------------- DB 조회

def _where(ds: Dataset) -> str:
    return f" AND {ds.where}" if ds.where else ""


async def _fetch_actual(session: AsyncSession, ds: Dataset, as_of: dt.date):
    """(as_of 이하 최신 날짜, 그 날짜의 수집 시각 또는 None) — 수집 시각은 컬럼 → collect_log 순."""
    row = await session.execute(
        text(f"SELECT max({ds.date_col}) FROM {ds.table} WHERE {ds.date_col} <= :as_of{_where(ds)}"),
        {"as_of": as_of},
    )
    actual = row.scalar()
    if actual is None:
        return None, None
    collected = None
    if ds.has_collected_at:
        row = await session.execute(
            text(f"SELECT max(collected_at) FROM {ds.table} WHERE {ds.date_col} = :d{_where(ds)}"),
            {"d": actual},
        )
        collected = row.scalar()
    if collected is None and ds.job:
        row = await session.execute(
            text("SELECT max(ran_at) FROM collect_log WHERE job = :j AND target_date = :d AND status = 'ok'"),
            {"j": ds.job, "d": actual},
        )
        collected = row.scalar()
    return actual, collected


async def check_freshness(session: AsyncSession, now_kst: dt.datetime) -> list[dict]:
    """모든 데이터셋을 now 기준으로 대조한다. 읽기 전용. as_of=now의 날짜(과거 시점 재현용 상한)."""
    now = _aware(now_kst).astimezone(KST)
    items: list[dict] = []
    for ds in DATASETS:
        exp = expected_date(now, ds.ready_after, ds.lag_trading_days)
        actual, collected = await _fetch_actual(session, ds, now.date())
        status, detail = judge(ds, now, exp, actual, collected)
        items.append(
            {
                "key": ds.key,
                "name": ds.name,
                "expected_date": exp.isoformat(),
                "actual_date": actual.isoformat() if actual else None,
                "collected_at": _aware(collected).astimezone(KST).isoformat() if collected else None,
                "status": status,
                "detail": detail,
            }
        )
    return items


def summarize_problems(items: list[dict], limit: int = 480) -> str:
    """collect_log.message(500자)용 문제 요약."""
    parts = [f"{i['name']}({i['status']}: {i['detail']})" for i in items if i["status"] != "ok"]
    return "; ".join(parts)[:limit]
