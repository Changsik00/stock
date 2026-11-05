"""뉴스 수집 + LLM 내러티브 후보 제안 잡 (PLAN.md §5.90-2).

``REGISTRY["narrative_proposals"]`` — 평일 08:30/12:00/16:30 KST에 scheduler가 실행하고,
``POST /api/narratives/refresh``가 같은 ``run_refresh``를 수동 호출한다.

1) 소스별 뉴스 수집(실패 격리) -> ``news_item`` upsert(url 유니크)
2) ANTHROPIC_API_KEY가 있으면 최근 뉴스 + 활성 내러티브 + 업종/테마 어휘로 LLM 호출 ->
   검증/중복 제거한 제안을 ``narrative_proposal(status=pending)``로 저장. 키가 없으면 수집만 하고
   제안 생성은 건너뛴다(예외 없음). **자동 승인은 없다.**

중복 실행 방지는 프로세스 내 asyncio.Lock이다(worker와 backend는 별개 프로세스라 동시 실행은
막지 못하지만, url 유니크 + 제목 중복 제거로 결과가 망가지지는 않는다)."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .. import narrative_llm
from ..clients import news_feeds
from ..models import GroupSnapshot, MarketNarrative, NarrativeProposal, NewsItem, SectorFlow
from .base import REGISTRY

logger = logging.getLogger(__name__)

JOB_NAME = "narrative_proposals"
NEWS_WINDOW_HOURS = 24
MAX_NEWS_FOR_LLM = 60
NEWS_RETENTION_DAYS = 30
RECENT_DECIDED_DAYS = 14
MAX_THEME_VOCAB = 80
NO_KEY_MESSAGE = "외부(Claude Code) 처리 대기 — 뉴스 수집만 수행(제안은 호스트 스크립트가 올림)"
EXTERNAL_JOB_NAME = "narrative_claude_ingest"  # 호스트 Claude Code 스크립트의 ingest 기록(collect_log)

_lock = asyncio.Lock()


def is_running() -> bool:
    return _lock.locked()


def _fetch_all_blocking() -> tuple[list[news_feeds.NewsRecord], dict[str, str]]:
    """news_feeds.fetch_all_news의 얇은 래퍼(테스트 monkeypatch 대상)."""
    return news_feeds.fetch_all_news()


async def store_news(session: AsyncSession, records: list[news_feeds.NewsRecord]) -> int:
    """url 유니크로 신규 기사만 INSERT. 신규 건수를 반환."""
    seen: set[str] = set()
    rows = []
    for r in records:
        if r.url in seen:
            continue
        seen.add(r.url)
        rows.append(
            {"source": r.source[:40], "title": r.title[:500], "url": r.url[:1000],
             "published_at": r.published_at, "summary": r.summary}
        )
    if not rows:
        return 0
    stmt = pg_insert(NewsItem).values(rows).on_conflict_do_nothing(index_elements=[NewsItem.url])
    res = await session.execute(stmt.returning(NewsItem.id))
    return len(res.fetchall())


def pick_news(rows: list[NewsItem], limit: int = MAX_NEWS_FOR_LLM) -> list[dict]:
    """소스별 최신순으로 라운드로빈해 특정 소스(예: 네이버 40건)가 입력을 독점하지 않게 한다."""
    by_src: dict[str, list[NewsItem]] = {}
    for r in sorted(rows, key=lambda x: x.published_at or x.fetched_at, reverse=True):
        by_src.setdefault(r.source.split("/")[0], []).append(r)
    picked: list[NewsItem] = []
    queues = list(by_src.values())
    while queues and len(picked) < limit:
        for q in list(queues):
            if len(picked) >= limit:
                break
            picked.append(q.pop(0))
            if not q:
                queues.remove(q)
    return [
        {"source": r.source, "title": r.title, "url": r.url, "summary": r.summary} for r in picked
    ]


async def _vocab(session: AsyncSession) -> list[str]:
    names: list[str] = []
    fdate = (await session.execute(select(func.max(SectorFlow.date)))).scalar()
    if fdate:
        names += list(
            (await session.execute(select(SectorFlow.sector_name).where(SectorFlow.date == fdate).distinct())).scalars()
        )
    gdate = (await session.execute(select(func.max(GroupSnapshot.date)))).scalar()
    if gdate:
        names += list(
            (await session.execute(
                select(GroupSnapshot.name).where(GroupSnapshot.date == gdate, GroupSnapshot.group_type == "upjong")
            )).scalars()
        )
        names += list(
            (await session.execute(
                select(GroupSnapshot.name)
                .where(GroupSnapshot.date == gdate, GroupSnapshot.group_type == "theme")
                .order_by(GroupSnapshot.value.desc().nulls_last())
                .limit(MAX_THEME_VOCAB)
            )).scalars()
        )
    out: list[str] = []
    for n in names:
        if n and n not in out:
            out.append(n)
    return out


async def _known_titles(session: AsyncSession) -> list[str]:
    now = dt.datetime.now(dt.timezone.utc)
    today = now.astimezone(news_feeds.KST).date()
    narr = (await session.execute(
        select(MarketNarrative.title).where(
            MarketNarrative.active.is_(True),
            or_(MarketNarrative.expires_on.is_(None), MarketNarrative.expires_on >= today),
        )
    )).scalars()
    since = now - dt.timedelta(days=RECENT_DECIDED_DAYS)
    props = (await session.execute(
        select(NarrativeProposal.title).where(
            or_(NarrativeProposal.status == "pending", NarrativeProposal.created_at >= since)
        )
    )).scalars()
    return list(narr) + list(props)


async def run_refresh(session: AsyncSession) -> dict:
    """수집 + (키가 있으면) 제안 생성. 호출부가 commit한다. 예외 대신 결과 dict에 상태를 담는다."""
    result: dict = {
        "collected": 0, "new_items": 0, "source_errors": {}, "llm_configured": False,
        "proposals_created": 0, "message": None, "llm_error": None,
    }
    records, errors = await asyncio.to_thread(_fetch_all_blocking)
    result["collected"] = len(records)
    result["source_errors"] = errors
    result["new_items"] = await store_news(session, records)
    await session.execute(
        delete(NewsItem).where(
            NewsItem.fetched_at < dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=NEWS_RETENTION_DAYS)
        )
    )
    await session.flush()

    client = narrative_llm.make_client()
    if client is None:
        result["message"] = NO_KEY_MESSAGE
        return result
    result["llm_configured"] = True

    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=NEWS_WINDOW_HOURS)
    rows = list((await session.execute(
        select(NewsItem).where(func.coalesce(NewsItem.published_at, NewsItem.fetched_at) >= since)
    )).scalars())
    news = pick_news(rows)
    if not news:
        result["message"] = "최근 뉴스 없음 — 제안 생성 건너뜀"
        return result

    known = await _known_titles(session)
    vocab = await _vocab(session)
    try:
        proposals = await narrative_llm.generate_proposals(client, news, known, vocab)
    except Exception as e:  # noqa: BLE001 - API/파싱 실패는 수집 결과를 막지 않는다
        logger.warning("narrative LLM 실패: %s", e)
        result["llm_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        result["message"] = "LLM 호출/파싱 실패 — 제안 생성 건너뜀"
        return result
    for p in proposals:
        session.add(NarrativeProposal(**p, status="pending"))
    await session.flush()
    result["proposals_created"] = len(proposals)
    result["message"] = f"제안 {len(proposals)}건 생성"
    return result


def summarize(result: dict) -> str:
    parts = [f"수집 {result['collected']}건(신규 {result['new_items']})"]
    if result["source_errors"]:
        parts.append("실패 소스: " + ", ".join(result["source_errors"]))
    parts.append(result["message"] or "")
    if result.get("llm_error"):
        parts.append(result["llm_error"])
    return " / ".join(p for p in parts if p)[:500]


async def run_refresh_locked(session: AsyncSession) -> dict | None:
    """락을 잡고 run_refresh. 이미 실행 중이면 None."""
    if _lock.locked():
        return None
    async with _lock:
        return await run_refresh(session)


async def collect_narrative_proposals(session: AsyncSession, target_date: dt.date) -> tuple[int, str | None]:
    """collector 계약: (제안 건수, message). 이미 실행 중이면 건너뛴다."""
    result = await run_refresh_locked(session)
    if result is None:
        return 0, "이미 실행 중 — 건너뜀"
    return result["proposals_created"], summarize(result)


REGISTRY[JOB_NAME] = collect_narrative_proposals
