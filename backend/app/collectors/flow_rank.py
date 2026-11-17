"""투자자별(외인/기관) 순매수·순매도 상위 종목 스냅샷 수집 -> flow_rank
(PLAN.md §4.5/§6 3.5-2b, 소스 교체: 2026-11-15).

## 소스 교체 배경
기존 소스(네이버 ``sise_deal_rank_iframe.naver``)가 2026-09-17부터 HTTP 410(페이지
삭제)을 돌려줘 flow_rank가 09-15 이후 비어 있었다. 새 소스는 이미 DB에 있는 값과
키움 ka10059다.

- **개별주**: ``stock_flow``(date=D, investor '외국인'->foreign, '기관계'->institution)
  의 net_value(백만 원). ``stock_flow``는 전 종목분이 ``accumulation_screener``(평일
  20:00 KST 시작, 약 65분 소요 -> 21:05 전후 완료)에서 채워진다. 18:00 본배치와
  ``live_refresh._run_stock_flow_scan``은 거래대금 상위 ~200종목만 채운다.
- **ETF**: stock_flow에는 ETF가 없어 키움 ka10059(``stock_investor_daily``)를 ETF당
  1콜 호출한다. 응답 한 번에 최근 100거래일치(일자 내림차순)가 와서 과거 날짜 백필도
  추가 호출이 없다. 금액 단위는 백만 원으로 stock_flow와 같은 스케일이다.
  ETF 유니버스 = (네이버 ETF 목록 거래대금 상위 ``ETF_TOP_N``) ∪ (최근
  ``ETF_HISTORY_DAYS``일 flow_rank에 is_etf로 등장한 코드).
- 키움 ka90009(외국인기관매매상위)는 금액이 stock_flow와 5~10배 어긋나 쓰지 않는다.

## 랭킹 규칙
개별주+ETF를 합쳐 investor별로 net_value>0 내림차순 상위 ``TOP_N``(40) = side 'buy',
net_value<0 절대값 내림차순 상위 40 = 'sell'. 저장은 기존과 같다(net_value는 양수
크기, 방향은 side). 재실행 시 더 큰 rank의 잔여 행이 남지 않도록 (date, investor,
side) 기존 행을 지우고 넣는다.

- ``market``: stocks.market을 'kospi'/'kosdaq'으로 소문자 변환. ETF는 'kospi'(기존
  적재분의 ETF 값과 동일 — 상장 시장 구분 소스가 없다).
- ``quantity``: 소스가 금액만 줘서(수량은 별도 콜이 필요) NULL이다.
  routers/flow_rank.py는 그대로 내려보내고 프론트(FlowRankTable.qtyLabel)가 '-'로
  표시한다.
- ``turnover``(%): 개별주는 value_rank(date, code)의 value/market_value_million, 없고
  D가 오늘이면 naver_rank.fetch_stock_market_value로 보충. ETF는 ka10059의
  acc_trde_prica(거래대금)/AUM — AUM은 D가 오늘이면 naver ETF 목록 aum_million,
  과거면 etf_stats.aum(D 이하 가장 최근 값). 못 구하면 NULL.

## 부분 적재 방지 / 자가 복구
D의 stock_flow 종목 수가 직전 거래일(stock_flow 기준)의 ``COMPLETE_RATIO``(80%)
미만이면 그 날짜는 쓰지 않고 message로 남긴다(18:00 시점 ~200종목 상태에서 쓰지
않기 위함). 일별 실행은 target_date와 함께 stock_flow 기준 최근
``RECOVER_DAYS``(5)거래일 중 flow_rank가 비어 있는 날도 채운다(Mac이 잠들어 하루
건너뛰어도 복구). 그래서 전 종목 수급이 준비된 뒤(21:30)에 도는 실행이 반드시 하나
있어야 한다(collectors/scheduler.py ``flow_rank_catchup``; 18:00 본배치에서는 제외).

REGISTRY["flow_rank"]로 등록된다.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import naver_etf, naver_rank
from ..models import EtfStat, FlowRank, Stock, StockFlow, ValueRank
from .base import REGISTRY

logger = logging.getLogger(__name__)

TOP_N = 40
ETF_TOP_N = 200
ETF_HISTORY_DAYS = 120
RECOVER_DAYS = 5
COMPLETE_RATIO = 0.8

# stock_flow.investor(한글) -> flow_rank.investor
STOCK_FLOW_INVESTORS = {"외국인": "foreign", "기관계": "institution"}
# ka10059 응답 필드 -> flow_rank.investor
KA10059_FIELDS = {"frgnr_invsr": "foreign", "orgn": "institution"}

# 네이버 개별주 회전율 조회 간격(서버 부담 방지)
NAVER_REQUEST_DELAY_SECONDS = 0.5


# ---------------------------------------------------------------------------
# 순수 함수
# ---------------------------------------------------------------------------


def build_rankings(items: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """items = [{"code","name","market","is_etf","foreign","institution"}, ...]
    (값은 부호 있는 net_value, 없으면 None) -> {(investor, side): [상위 TOP_N rows]}.

    각 row는 {"code","name","market","is_etf","net_value"(양수 크기)}.
    동률은 코드 오름차순으로 안정 정렬한다(재실행해도 순서가 같도록).
    """
    out: dict[tuple[str, str], list[dict]] = {}
    for investor in ("foreign", "institution"):
        for side in ("buy", "sell"):
            picked = []
            for it in items:
                v = it.get(investor)
                if v is None:
                    continue
                if (side == "buy" and v > 0) or (side == "sell" and v < 0):
                    picked.append((abs(v), it))
            picked.sort(key=lambda p: (-p[0], p[1]["code"]))
            out[(investor, side)] = [
                {
                    "code": it["code"],
                    "name": it.get("name"),
                    "market": it.get("market"),
                    "is_etf": it.get("is_etf", False),
                    "net_value": mag,
                }
                for mag, it in picked[:TOP_N]
            ]
    return out


def parse_etf_history(data: dict) -> dict[dt.date, dict]:
    """ka10059 응답 body -> {date: {"foreign": int|None, "institution": int|None,
    "value": 거래대금 백만원|None}}. 부호는 소스 그대로(순매도=음수)."""
    from ..routers.stocks import _parse_signed_int  # 지연 import(순환 방지)

    out: dict[dt.date, dict] = {}
    for row in data.get("stk_invsr_orgn") or []:
        raw = row.get("dt")
        if not raw:
            continue
        try:
            d = dt.datetime.strptime(raw, "%Y%m%d").date()
        except ValueError:
            continue
        entry = {name: _parse_signed_int(row.get(f)) for f, name in KA10059_FIELDS.items()}
        value = _parse_signed_int(row.get("acc_trde_prica"))
        entry["value"] = None if value is None else abs(value)
        out[d] = entry
    return out


# ---------------------------------------------------------------------------
# 외부 호출(테스트에서 mock)
# ---------------------------------------------------------------------------


def _fetch_etf_list_blocking() -> list[dict]:
    return naver_etf.fetch_etf_list()


def _fetch_stock_market_value_blocking(code: str) -> dict:
    time.sleep(NAVER_REQUEST_DELAY_SECONDS)
    return naver_rank.fetch_stock_market_value(code)


async def _fetch_etf_histories(codes: list[str], end_date: dt.date) -> dict[str, dict]:
    """ETF 코드별 ka10059 일별 이력 {code: {date: {...}}}. 한 종목 실패는 경고만 남기고
    건너뛴다. KiwoomClient는 TR별 초당 1회 제한이 인스턴스 속성이라 하나만 연다."""
    from ..clients.kiwoom import KiwoomClient

    result: dict[str, dict] = {}
    async with KiwoomClient() as client:
        for code in codes:
            try:
                data, _ = await client.stock_investor_daily(code, end_date)
                hist = parse_etf_history(data)
            except Exception:
                logger.warning("flow_rank: ETF %s ka10059 조회 실패", code, exc_info=True)
                continue
            if hist:
                result[code] = hist
    return result


# ---------------------------------------------------------------------------
# DB 조회
# ---------------------------------------------------------------------------


async def stock_flow_count(session: AsyncSession, date: dt.date) -> int:
    return (
        await session.execute(
            select(func.count(func.distinct(StockFlow.code))).where(StockFlow.date == date)
        )
    ).scalar_one()


async def _prev_flow_date(session: AsyncSession, date: dt.date) -> dt.date | None:
    return (
        await session.execute(select(func.max(StockFlow.date)).where(StockFlow.date < date))
    ).scalar_one_or_none()


async def check_complete(session: AsyncSession, date: dt.date) -> tuple[bool, str]:
    """D의 stock_flow가 직전 거래일의 COMPLETE_RATIO 이상 채워졌는지."""
    cnt = await stock_flow_count(session, date)
    if cnt == 0:
        return False, f"{date} stock_flow 없음"
    prev = await _prev_flow_date(session, date)
    if prev is None:
        return True, ""
    prev_cnt = await stock_flow_count(session, prev)
    if prev_cnt and cnt < prev_cnt * COMPLETE_RATIO:
        return False, f"{date} stock_flow {cnt}종목 < 직전 거래일({prev}) {prev_cnt}종목의 80%"
    return True, ""


async def _load_stock_items(session: AsyncSession, date: dt.date) -> list[dict]:
    stmt = (
        select(StockFlow.code, StockFlow.investor, StockFlow.net_value, Stock.name, Stock.market)
        .join(Stock, Stock.code == StockFlow.code)
        .where(StockFlow.date == date, StockFlow.investor.in_(list(STOCK_FLOW_INVESTORS)))
    )
    by_code: dict[str, dict] = {}
    for code, investor, net_value, name, market in (await session.execute(stmt)).all():
        it = by_code.setdefault(
            code,
            {
                "code": code,
                "name": name,
                "market": (market or "").lower() or None,
                "is_etf": False,
                "foreign": None,
                "institution": None,
            },
        )
        it[STOCK_FLOW_INVESTORS[investor]] = net_value
    return list(by_code.values())


async def _load_etf_history_codes(session: AsyncSession, today: dt.date) -> dict[str, str | None]:
    """최근 ETF_HISTORY_DAYS일 flow_rank에 is_etf로 등장한 {code: name}."""
    since = today - dt.timedelta(days=ETF_HISTORY_DAYS)
    rows = (
        await session.execute(
            select(FlowRank.code, FlowRank.name)
            .where(FlowRank.is_etf.is_(True), FlowRank.date >= since)
            .distinct()
        )
    ).all()
    return {code: name for code, name in rows}


async def _stock_turnover_map(
    session: AsyncSession, date: dt.date, codes: set[str], fetch_missing: bool
) -> dict[str, float]:
    """개별주 회전율(%). value_rank 우선, 오늘이면 없는 종목만 네이버로 보충."""
    if not codes:
        return {}
    rows = (
        await session.execute(
            select(ValueRank.code, ValueRank.value, ValueRank.market_value_million).where(
                ValueRank.date == date, ValueRank.code.in_(codes)
            )
        )
    ).all()
    out = {
        code: round(value / mv * 100, 4)
        for code, value, mv in rows
        if value is not None and mv
    }
    if fetch_missing:
        for code in sorted(codes - set(out)):
            try:
                mv = await asyncio.to_thread(_fetch_stock_market_value_blocking, code)
            except Exception:
                logger.warning("flow_rank: 종목 %s 회전율 조회 실패", code, exc_info=True)
                continue
            amount = mv.get("accumulated_trading_value_million")
            market_value = mv.get("market_value_million")
            if amount is not None and market_value:
                out[code] = round(amount / market_value * 100, 4)
    return out


async def _etf_aum_map(session: AsyncSession, date: dt.date, codes: set[str]) -> dict[str, int]:
    """etf_stats에서 date 이하 가장 최근 aum(백만 원)."""
    if not codes:
        return {}
    rows = (
        await session.execute(
            select(EtfStat.code, EtfStat.aum)
            .where(EtfStat.code.in_(codes), EtfStat.date <= date, EtfStat.aum.is_not(None))
            .order_by(EtfStat.code, EtfStat.date.desc())
            .distinct(EtfStat.code)
        )
    ).all()
    return {code: aum for code, aum in rows if aum}


# ---------------------------------------------------------------------------
# 핵심: 한 날짜의 랭킹 계산 / 저장
# ---------------------------------------------------------------------------


async def compute_rankings(
    session: AsyncSession,
    date: dt.date,
    etf_hist: dict[str, dict],
    etf_names: dict[str, str | None],
    etf_live_aum: dict[str, int],
    today: dt.date | None = None,
) -> dict[tuple[str, str], list[dict]]:
    """저장하지 않고 날짜 D의 랭킹(+turnover)을 계산한다(비교 모드와 저장이 공용)."""
    today = today or dt.date.today()
    items = await _load_stock_items(session, date)
    # stock_flow에는 ETF 코드도 들어 있다(스윕 잡이 채움). 같은 ETF가 아래 ETF
    # 유니버스로도 들어오면 한 랭킹에 두 번(연속 순위, 같은 금액) 올라가 상위 40개
    # 자리를 잡아먹었다(Phase 5.111) — ETF 쪽 행(is_etf·회전율이 정확)만 남긴다.
    etf_day_codes = {code for code, hist in etf_hist.items() if hist.get(date)}
    items = [it for it in items if it["code"] not in etf_day_codes]
    for code, hist in etf_hist.items():
        day = hist.get(date)
        if not day:
            continue
        items.append(
            {
                "code": code,
                "name": etf_names.get(code),
                "market": "kospi",
                "is_etf": True,
                "foreign": day.get("foreign"),
                "institution": day.get("institution"),
            }
        )
    ranks = build_rankings(items)

    picked_stocks = {r["code"] for rows in ranks.values() for r in rows if not r["is_etf"]}
    picked_etfs = {r["code"] for rows in ranks.values() for r in rows if r["is_etf"]}
    is_today = date == today
    stock_turn = await _stock_turnover_map(session, date, picked_stocks, fetch_missing=is_today)
    aum = await _etf_aum_map(session, date, picked_etfs)
    if is_today:
        aum.update({c: v for c, v in etf_live_aum.items() if c in picked_etfs and v})
    for rows in ranks.values():
        for r in rows:
            if r["is_etf"]:
                value = (etf_hist.get(r["code"], {}).get(date) or {}).get("value")
                a = aum.get(r["code"])
                r["turnover"] = round(value / a * 100, 4) if value is not None and a else None
            else:
                r["turnover"] = stock_turn.get(r["code"])
    return ranks


async def save_rankings(
    session: AsyncSession, date: dt.date, ranks: dict[tuple[str, str], list[dict]]
) -> int:
    """(date, investor, side) 기존 행을 지우고 새로 넣는다(재실행 시 잔여 rank 방지)."""
    total = 0
    for (investor, side), rows in ranks.items():
        await session.execute(
            delete(FlowRank).where(
                FlowRank.date == date, FlowRank.investor == investor, FlowRank.side == side
            )
        )
        if not rows:
            continue
        await session.execute(
            pg_insert(FlowRank).values(
                [
                    {
                        "date": date,
                        "investor": investor,
                        "side": side,
                        "rank": i,
                        "code": r["code"],
                        "name": r["name"],
                        "net_value": r["net_value"],
                        "quantity": None,  # 소스가 금액만 제공(모듈 docstring)
                        "turnover": r.get("turnover"),
                        "is_etf": r["is_etf"],
                        "market": r["market"],
                    }
                    for i, r in enumerate(rows, start=1)
                ]
            )
        )
        total += len(rows)
    return total


async def prepare_etf_data(
    session: AsyncSession, end_date: dt.date, today: dt.date | None = None
) -> tuple[dict[str, dict], dict[str, str | None], dict[str, int]]:
    """ETF 유니버스 구성 + ka10059 이력 수집 -> (etf_hist, etf_names, live_aum)."""
    today = today or dt.date.today()
    names = await _load_etf_history_codes(session, today)
    live_aum: dict[str, int] = {}
    universe = set(names)
    try:
        etf_list = await asyncio.to_thread(_fetch_etf_list_blocking)
    except Exception:
        logger.warning("flow_rank: 네이버 ETF 목록 조회 실패 — 과거 등장 ETF만 사용", exc_info=True)
        etf_list = []
    top = sorted(
        (it for it in etf_list if it.get("code")),
        key=lambda it: it.get("amount_million") or 0,
        reverse=True,
    )[:ETF_TOP_N]
    for it in top:
        universe.add(it["code"])
    for it in etf_list:
        if it.get("code") in universe:
            if it.get("name"):
                names[it["code"]] = it["name"]
            if it.get("aum_million"):
                live_aum[it["code"]] = it["aum_million"]
    hist = await _fetch_etf_histories(sorted(universe), end_date)
    return hist, names, live_aum


async def collect_flow_rank_dates(
    session: AsyncSession, dates: list[dt.date], today: dt.date | None = None
) -> tuple[int, list[str]]:
    """dates를 완전성 검사 후 저장한다. (적재 행 수, 상태 메모 목록)."""
    notes: list[str] = []
    ok_dates: list[dt.date] = []
    for d in sorted(dates):
        ok, why = await check_complete(session, d)
        if ok:
            ok_dates.append(d)
        else:
            notes.append(f"건너뜀: {why}")
    if not ok_dates:
        return 0, notes
    etf_hist, etf_names, live_aum = await prepare_etf_data(session, max(ok_dates), today)
    total = 0
    for d in ok_dates:
        ranks = await compute_rankings(session, d, etf_hist, etf_names, live_aum, today)
        n = await save_rankings(session, d, ranks)
        total += n
        notes.append(f"{d}: {n}행")
    return total, notes


async def _recoverable_dates(session: AsyncSession, target_date: dt.date) -> list[dt.date]:
    """target_date + (stock_flow 기준 최근 RECOVER_DAYS거래일 중 flow_rank가 빈 날)."""
    recent = (
        (
            await session.execute(
                select(StockFlow.date)
                .where(StockFlow.date <= target_date)
                .distinct()
                .order_by(StockFlow.date.desc())
                .limit(RECOVER_DAYS)
            )
        )
        .scalars()
        .all()
    )
    have = set(
        (await session.execute(select(FlowRank.date).where(FlowRank.date.in_(recent)).distinct()))
        .scalars()
        .all()
    )
    dates = {d for d in recent if d not in have}
    dates.add(target_date)
    return sorted(dates)


async def collect_flow_rank(session: AsyncSession, target_date: dt.date) -> tuple[int, str | None]:
    """target_date와 최근 5거래일 중 비어 있는 날의 flow_rank를 적재한다(모듈 docstring)."""
    dates = await _recoverable_dates(session, target_date)
    total, notes = await collect_flow_rank_dates(session, dates)
    return total, "; ".join(notes) if notes else None


REGISTRY["flow_rank"] = collect_flow_rank
