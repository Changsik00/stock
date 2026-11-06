"""지수·대표 ETF 1분봉 장중 적재기 (PLAN.md §5.93) — 연구용 데이터 축적, 매매 입력 아님.

**목적**: §5.92 고변동일 연구에서 지수 분봉이 DB에 없어 시초 N분 변동성을 직접 못 쟀다.
키움 분봉 TR은 과거를 무한정 주지 않으므로 지금부터 쌓는다.

**소스**(clients/kiwoom.py 모듈 docstring "ka10080/ka20005 실측 확정" 절):
- 지수 코스피 "001"/코스닥 "101": ``ka20005`` -> ``index_intraday_bar`` (가격은 파서에서 ÷100 보정 완료)
- ETF 069500(KODEX 200)/229200(KODEX 코스닥150): ``ka10080`` -> ``stock_intraday_bar``(stocks FK 충족)
- 0167A0은 기존 ``stock_intraday_history``(18:00 배치)가 그대로 담당 — 여기서 건드리지 않는다.

**페이지네이션**: 한 콜은 900행(약 2.4거래일)이지만 ``cont-yn``/``next-key`` 연속조회로 더
과거를 받을 수 있다(2026-11-06 실측: 4페이지에 10거래일). 그래서
- 평소(DB에 오늘까지 있음): 1페이지만 upsert(진행 중 봉 덮어쓰기 + 누락 보정),
- 최초/공백 발생: DB 최신 일자에 닿을 때까지(없으면 ``BACKFILL_MAX_PAGES``까지) 거슬러 올라간다.

**스케줄**: scheduler.py 전용 크론(평일 09:00~15:40 5분 간격, 15:40이 마감 후 보정). 18:00 본배치
제외, REGISTRY("market_minute_bars")로 수동 실행 가능. 대상 하나의 실패는 로그만 남기고 다음으로.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients.kiwoom import MINUTE_CHART_ROWS_KEY, KiwoomClient, parse_minute_chart_rows
from ..models import IndexIntradayBar, StockIntradayBar
from .base import REGISTRY

logger = logging.getLogger(__name__)

INDEX_CODES = ("001", "101")  # 코스피 / 코스닥
ETF_CODES = ("069500", "229200")  # KODEX 200 / KODEX 코스닥150
BACKFILL_MAX_PAGES = 40  # 최초 백필 상한(페이지당 약 2.4거래일)
_CHUNK = 2000


def parse_all_days(data: dict[str, Any], api_id: str) -> list[dict[str, Any]]:
    """응답 한 페이지(여러 거래일 혼재) -> 전 일자 분봉 리스트(`parse_minute_chart_rows`는
    최신 하루만 남기므로 일자별로 쪼개 재사용). 중복 (date,time)은 마지막 값만 남긴다."""
    key = MINUTE_CHART_ROWS_KEY[api_id]
    raw = [r for r in (data.get(key) or []) if r.get("cntr_tm")]
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for d in sorted({r["cntr_tm"][:8] for r in raw}):
        sub = {key: [r for r in raw if r["cntr_tm"].startswith(d)]}
        for b in parse_minute_chart_rows(sub, api_id):
            out[(b["date"], b["time"])] = b
    return list(out.values())


def to_rows(code: str, bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """파싱된 봉 -> DB 행(date 변환, 날짜/시각 없는 봉 제외)."""
    return [
        {
            "code": code,
            "date": dt.datetime.strptime(b["date"], "%Y%m%d").date(),
            "time": b["time"],
            "open": b.get("open"),
            "high": b.get("high"),
            "low": b.get("low"),
            "close": b.get("close"),
            "volume": b.get("volume"),
        }
        for b in bars
        if b.get("date") and b.get("time")
    ]


async def _upsert(session: AsyncSession, model, rows: list[dict[str, Any]]) -> None:
    for i in range(0, len(rows), _CHUNK):
        stmt = pg_insert(model).values(rows[i : i + _CHUNK])
        stmt = stmt.on_conflict_do_update(
            index_elements=[model.code, model.date, model.time],
            set_={c: getattr(stmt.excluded, c) for c in ("open", "high", "low", "close", "volume")},
        )
        await session.execute(stmt)


async def _collect_one(
    session: AsyncSession, client: KiwoomClient, code: str, is_index: bool, max_pages: int
) -> int:
    model = IndexIntradayBar if is_index else StockIntradayBar
    api_id = "ka20005" if is_index else "ka10080"
    latest = (
        await session.execute(select(func.max(model.date)).where(model.code == code))
    ).scalar_one()

    total, cont_yn, next_key = 0, None, None
    for _page in range(max_pages):
        if is_index:
            data, headers = await client.sector_minute_chart(code, "1", cont_yn=cont_yn, next_key=next_key)
        else:
            data, headers = await client.stock_minute_chart(code, "1", cont_yn=cont_yn, next_key=next_key)
        bars = parse_all_days(data, api_id)
        if not bars:
            break
        rows = to_rows(code, bars)
        await _upsert(session, model, rows)
        total += len(rows)
        oldest = min(r["date"] for r in rows)
        # 이미 DB에 있는 일자까지 거슬러 올라왔으면 끝(평소엔 1페이지로 종료).
        if latest is not None and oldest <= latest:
            break
        if headers.get("cont-yn") != "Y" or not headers.get("next-key"):
            break
        cont_yn, next_key = "Y", headers["next-key"]
    await session.commit()
    return total


async def collect_market_minute_bars(session: AsyncSession, target_date: dt.date | None = None) -> int:
    """지수 2 + ETF 2의 1분봉 upsert. 반환=쓴 행 수 합. 대상별 실패는 격리(로그만)."""
    targets = [(c, True) for c in INDEX_CODES] + [(c, False) for c in ETF_CODES]
    total = 0
    try:
        async with KiwoomClient() as client:
            for code, is_index in targets:
                try:
                    n = await _collect_one(session, client, code, is_index, BACKFILL_MAX_PAGES)
                    total += n
                    logger.info("market_minute_bars: %s %d행 upsert", code, n)
                except Exception as e:  # noqa: BLE001 - 대상 하나의 실패가 나머지에 번지지 않게
                    logger.warning("market_minute_bars: %s 실패(무시): %s", code, e)
                    try:
                        await session.rollback()
                    except Exception:  # noqa: BLE001
                        pass
    except Exception as e:  # noqa: BLE001 - 클라이언트 생성/토큰 실패 등
        logger.warning("market_minute_bars 실패(무시): %s", e)
    return total


REGISTRY["market_minute_bars"] = collect_market_minute_bars
