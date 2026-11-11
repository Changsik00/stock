"""시장 내러티브 메모 CRUD + 섹터 수급 일치 evidence (PLAN.md §5.90-1) +
뉴스 기반 LLM 제안 큐/승인 API (§5.90-2).

사용자가 직접 기록한 서사(수혜/피해 섹터)를 최근 거래일의 sector_flow(외국인/기관/개인
순매수, 백만원)·group_snapshot(등락률)과 이름 부분일치로 대조해 보여준다.
**참고용이며 매매 신호가 아니다** — 자동매매 로직은 이 테이블을 읽지 않는다."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import narrative_llm
from ..collectors import narrative_news
from ..collectors.base import _upsert_log
from ..db import get_session
from ..market_hours import KST
from ..models import CollectLog, GroupSnapshot, MarketNarrative, NarrativeProposal, NewsIssue, NewsItem, SectorFlow

router = APIRouter(prefix="/api/narratives", tags=["narratives"])

DISCLAIMER = "참고용, 매매 신호 아님"
MIN_TERM_LEN = 2
DEFAULT_EXPIRY_DAYS = 30

Direction = Literal["bullish", "bearish", "neutral"]
Confidence = Literal["low", "mid", "high"]


def _clean_list(v: list[str] | None) -> list[str]:
    out: list[str] = []
    for x in v or []:
        x = (x or "").strip()
        if x and x not in out:
            out.append(x[:50])
    return out


class NarrativeIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str | None = None
    direction: Direction = "neutral"
    beneficiaries: list[str] = []
    victims: list[str] = []
    confidence: Confidence = "mid"
    expires_on: dt.date | None = None
    active: bool = True

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("제목은 필수입니다")
        return v


class NarrativePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = None
    direction: Direction | None = None
    beneficiaries: list[str] | None = None
    victims: list[str] | None = None
    confidence: Confidence | None = None
    expires_on: dt.date | None = None
    active: bool | None = None


# ---------------------------------------------------------------- evidence 순수 로직


def name_matches(term: str, name: str | None) -> bool:
    """이름 부분일치(양방향, 공백 무시). 너무 짧은 용어는 오탐 방지를 위해 불일치 처리."""
    t = term.replace(" ", "")
    n = (name or "").replace(" ", "")
    if len(t) < MIN_TERM_LEN or not n:
        return False
    return t in n or (len(n) >= MIN_TERM_LEN and n in t)


def judge(role: str, flow_smart: int | None, change_rate: float | None) -> str:
    """단순 규칙. role: 'beneficiary'|'victim'.

    수급(외국인+기관 순매수 합)이 있으면 수급 기준, 없으면 등락률 기준.
    수혜인데 순매수<0(또는 하락) → divergent, 피해인데 순매수>0(또는 상승) → divergent.
    0이거나 데이터 없음 → unknown."""
    x: float | None = flow_smart if flow_smart is not None else change_rate
    if x is None or x == 0:
        return "unknown"
    positive = x > 0
    return "aligned" if positive == (role == "beneficiary") else "divergent"


def build_evidence(
    role: str,
    terms: list[str],
    flow_rows: list[dict],
    group_rows: list[dict],
) -> list[dict]:
    """terms 각각에 대해 매칭된 sector_flow/group_snapshot 행을 evidence로 만든다.
    매칭 실패한 term은 항목을 만들지 않는다(빈 배열 = 근거 없음, 정직하게)."""
    out: list[dict] = []
    for term in terms:
        for r in flow_rows:
            if not name_matches(term, r["name"]):
                continue
            f, o, i = r.get("foreign"), r.get("inst"), r.get("indiv")
            smart = (f or 0) + (o or 0) if (f is not None or o is not None) else None
            out.append(
                {
                    "term": term,
                    "role": role,
                    "source": "sector_flow",
                    "market": r.get("market"),
                    "name": r["name"],
                    "date": r["date"],
                    "foreign_net": f,
                    "institution_net": o,
                    "individual_net": i,
                    "unit": "백만원",
                    "verdict": judge(role, smart, None),
                }
            )
        for r in group_rows:
            if not name_matches(term, r["name"]):
                continue
            out.append(
                {
                    "term": term,
                    "role": role,
                    "source": "group_snapshot",
                    "group_type": r.get("group_type"),
                    "name": r["name"],
                    "date": r["date"],
                    "change_rate": r.get("change_rate"),
                    "verdict": judge(role, None, r.get("change_rate")),
                }
            )
    return out


def summarize(evidence: list[dict]) -> str:
    """aligned|divergent|mixed|unknown|none — 판정 가능한 evidence만 집계."""
    v = {e["verdict"] for e in evidence if e["verdict"] != "unknown"}
    if not evidence:
        return "none"
    if not v:
        return "unknown"
    if v == {"aligned"}:
        return "aligned"
    if v == {"divergent"}:
        return "divergent"
    return "mixed"


async def _load_latest(session: AsyncSession, terms: list[str]) -> tuple[list[dict], list[dict]]:
    """terms와 이름이 겹칠 수 있는 행만 각 테이블의 최근 거래일에서 읽는다."""
    terms = [t for t in terms if len(t.replace(" ", "")) >= MIN_TERM_LEN]
    if not terms:
        return [], []
    flow_rows: list[dict] = []
    group_rows: list[dict] = []

    fdate = (await session.execute(select(func.max(SectorFlow.date)))).scalar()
    if fdate:
        # 역방향(용어가 업종명을 포함)은 업종 수가 적어 전부 가져와 파이썬에서 거른다.
        rows = (
            await session.execute(select(SectorFlow).where(SectorFlow.date == fdate))
        ).scalars()
        flow_rows = [
            {
                "market": r.market,
                "name": r.sector_name,
                "date": r.date.isoformat(),
                "foreign": r.frgnr_net_value,
                "inst": r.orgn_net_value,
                "indiv": r.ind_net_value,
            }
            for r in rows
            if any(name_matches(t, r.sector_name) for t in terms)
        ]

    gdate = (await session.execute(select(func.max(GroupSnapshot.date)))).scalar()
    if gdate:
        rows = (
            await session.execute(
                select(GroupSnapshot).where(
                    GroupSnapshot.date == gdate,
                    or_(*[GroupSnapshot.name.ilike(f"%{t}%") for t in terms]),
                )
            )
        ).scalars()
        group_rows = [
            {
                "group_type": r.group_type,
                "name": r.name,
                "date": r.date.isoformat(),
                "change_rate": float(r.change_rate) if r.change_rate is not None else None,
            }
            for r in rows
        ]
    return flow_rows, group_rows


def _item(r: MarketNarrative, evidence: dict | None = None) -> dict:
    out = {
        "id": r.id,
        "title": r.title,
        "summary": r.summary,
        "direction": r.direction,
        "beneficiaries": r.beneficiaries or [],
        "victims": r.victims or [],
        "confidence": r.confidence,
        "expires_on": r.expires_on.isoformat() if r.expires_on else None,
        "active": r.active,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }
    if evidence is not None:
        out.update(evidence)
    return out


async def _evidence_for(session: AsyncSession, rows: list[Any]) -> list[tuple[list[dict], str]]:
    """beneficiaries/victims 속성을 가진 객체(MarketNarrative, NewsIssue)별 (evidence, alignment).
    sector_flow/group_snapshot 조회는 전체 용어를 모아 한 번만 한다."""
    all_terms = sorted({t for r in rows for t in (r.beneficiaries or []) + (r.victims or [])})
    flow_rows, group_rows = await _load_latest(session, all_terms)
    out = []
    for r in rows:
        ev = build_evidence("beneficiary", r.beneficiaries or [], flow_rows, group_rows) + build_evidence(
            "victim", r.victims or [], flow_rows, group_rows
        )
        out.append((ev, summarize(ev)))
    return out


async def _with_evidence(session: AsyncSession, rows: list[MarketNarrative]) -> list[dict]:
    pairs = await _evidence_for(session, rows)
    return [
        _item(r, {"evidence": ev, "alignment": al, "disclaimer": DISCLAIMER}) for r, (ev, al) in zip(rows, pairs)
    ]


# ---------------------------------------------------------------- 엔드포인트


@router.get("/")
async def list_narratives(
    include_inactive: bool = Query(False, description="비활성/만료 항목 포함"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    today = dt.datetime.now(KST).date()
    stmt = select(MarketNarrative).order_by(MarketNarrative.id.desc())
    if not include_inactive:
        stmt = stmt.where(
            MarketNarrative.active.is_(True),
            or_(MarketNarrative.expires_on.is_(None), MarketNarrative.expires_on >= today),
        )
    rows = list((await session.execute(stmt)).scalars())
    return {"disclaimer": DISCLAIMER, "items": await _with_evidence(session, rows)}


@router.post("/")
async def create_narrative(body: NarrativeIn, session: AsyncSession = Depends(get_session)) -> dict:
    row = MarketNarrative(
        title=body.title,
        summary=(body.summary or "").strip() or None,
        direction=body.direction,
        beneficiaries=_clean_list(body.beneficiaries),
        victims=_clean_list(body.victims),
        confidence=body.confidence,
        expires_on=body.expires_on,
        active=body.active,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return (await _with_evidence(session, [row]))[0]


async def _update(narrative_id: int, body: NarrativePatch, session: AsyncSession) -> dict:
    row = await session.get(MarketNarrative, narrative_id)
    if row is None:
        raise HTTPException(404, "내러티브를 찾을 수 없습니다")
    data = body.model_dump(exclude_unset=True)
    if "title" in data:
        data["title"] = (data["title"] or "").strip()
        if not data["title"]:
            raise HTTPException(422, "제목은 필수입니다")
    for k in ("direction", "confidence", "active"):
        if k in data and data[k] is None:
            raise HTTPException(422, f"{k}는 null일 수 없습니다")
    for k in ("beneficiaries", "victims"):
        if k in data:
            data[k] = _clean_list(data[k])
    if "summary" in data:
        data["summary"] = (data["summary"] or "").strip() or None
    for k, v in data.items():
        setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    return (await _with_evidence(session, [row]))[0]


# ---------------------------------------------------------------- 5.106 오늘의 이슈 (뉴스 묶음)
# 라우트 순서: 아래 /issues* 는 /{narrative_id} 동적 경로보다 먼저 등록한다.

ISSUES_JOB = "narrative_issues_ingest"  # collect_log 기록 job명
ISSUE_WINDOW_HOURS = 24
ISSUE_CONTEXT_MAX = 150
ISSUE_MAX_ITEMS = 8
ISSUE_KEEP_DAYS = 30
ISSUE_BATCH_LIST_DAYS = 7
ISSUE_MAX_FACTS = 3
ISSUE_TRACK_MAX_SOURCES = 5
IssueStatus = Literal["new", "tracked", "hidden"]


def _issue_item(r: NewsIssue, ev: list[dict], alignment: str) -> dict:
    return {
        "id": r.id,
        "rank": r.rank,
        "title": r.title,
        "direction": r.direction,
        "confidence": r.confidence,
        "continuing": r.continuing,
        "facts": r.facts or [],
        "interpretation": r.interpretation or "",
        "counter": r.counter or "",
        "beneficiaries": r.beneficiaries or [],
        "victims": r.victims or [],
        "sources": r.sources or [],
        "article_count": len(r.sources or []),
        "status": r.status,
        "narrative_id": r.narrative_id,
        "evidence": ev,
        "alignment": alignment,
    }


async def _issue_items(session: AsyncSession, rows: list[NewsIssue]) -> list[dict]:
    pairs = await _evidence_for(session, rows)
    return [_issue_item(r, ev, al) for r, (ev, al) in zip(rows, pairs)]


async def _latest_batch_at(session: AsyncSession) -> dt.datetime | None:
    return (await session.execute(select(func.max(NewsIssue.batch_at)))).scalar()


@router.get("/issues/context")
async def issues_context(
    limit: int = Query(ISSUE_CONTEXT_MAX, ge=1, le=300),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """호스트 스크립트용 입력. 최근 24h 뉴스 최신순 상한 limit.
    ingest-context와 달리 '이미 근거로 쓴 기사 제외'를 하지 않는다(이어지는 이슈는 매번 갱신)."""
    now = dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(hours=ISSUE_WINDOW_HOURS)
    ts = func.coalesce(NewsItem.published_at, NewsItem.fetched_at)
    rows = list(
        (await session.execute(select(NewsItem).where(ts >= since).order_by(ts.desc(), NewsItem.id.desc()).limit(limit)))
        .scalars()
    )
    news = [
        {
            "n": i,
            "id": r.id,
            "source": r.source,
            "title": r.title,
            "url": r.url,  # 스크립트가 기사 번호 -> URL 변환에 쓴다(프롬프트에는 넣지 않음)
            "snippet": (r.summary or "")[:120],
            "published_at": r.published_at.isoformat() if r.published_at else None,
        }
        for i, r in enumerate(rows, 1)
    ]
    latest = await _latest_batch_at(session)
    prev_titles: list[str] = []
    if latest is not None:
        prev_titles = list(
            (
                await session.execute(
                    select(NewsIssue.title).where(NewsIssue.batch_at == latest).order_by(NewsIssue.rank)
                )
            ).scalars()
        )
    today = now.astimezone(KST).date()
    narr = (
        await session.execute(
            select(MarketNarrative).where(
                MarketNarrative.active.is_(True),
                or_(MarketNarrative.expires_on.is_(None), MarketNarrative.expires_on >= today),
            )
        )
    ).scalars()
    return {
        "news": news,
        "window": {"from": since.isoformat(), "to": now.isoformat()},
        "previous_issue_titles": prev_titles,
        "active_narratives": [{"title": r.title, "direction": r.direction} for r in narr],
        "vocabulary": await narrative_news._vocab(session),
    }


def _trunc(v: Any, n: int) -> str:
    return v.strip()[:n].strip() if isinstance(v, str) else ""


class IssueRaw(BaseModel):
    """LLM이 만든 이슈 한 건(느슨한 타입 검증). 길이/enum 보정은 _clean_issue에서 한다."""

    title: str
    direction: Any = "neutral"
    confidence: Any = "low"
    continuing: bool = False
    facts: list[Any] = []
    interpretation: Any = ""
    counter: Any = ""
    beneficiaries: list[Any] = []
    victims: list[Any] = []
    article_urls: list[Any] = []


def _clean_issue(raw: IssueRaw) -> dict | None:
    """길이 제한·enum 보정만 한다(텍스트 안의 지시는 해석하지 않음). 제목이 비면 None."""
    title = _trunc(raw.title, 200)
    if not title:
        return None
    facts = [f for f in (_trunc(x, 200) for x in raw.facts) if f][:ISSUE_MAX_FACTS]
    ben = _clean_list([x for x in raw.beneficiaries if isinstance(x, str)])[:6]
    vic = _clean_list([x for x in raw.victims if isinstance(x, str)])[:6]
    urls: list[str] = []
    for u in raw.article_urls:
        if isinstance(u, str) and u.strip() and u.strip() not in urls:
            urls.append(u.strip())
    return {
        "title": title,
        "direction": raw.direction if raw.direction in ("bullish", "bearish", "neutral") else "neutral",
        "confidence": raw.confidence if raw.confidence in ("low", "mid", "high") else "low",
        "continuing": raw.continuing,
        "facts": facts,
        "interpretation": _trunc(raw.interpretation, 300),
        "counter": _trunc(raw.counter, 300),
        "beneficiaries": ben,
        "victims": vic,
        "urls": urls,
    }


class IssuesIngestBody(BaseModel):
    """이슈는 느슨하게(dict) 받는다 — 불량 항목 하나가 전체를 막지 않게."""

    model: str | None = Field(default=None, max_length=60)
    window: dict | None = None
    news_total: int | None = Field(default=None, ge=0)
    issues: list[dict | str | int | None] = Field(max_length=50)


@router.post("/issues/ingest")
async def ingest_issues(body: IssuesIngestBody, session: AsyncSession = Depends(get_session)) -> dict:
    """호스트(Claude Code) 생성 이슈 수신. 환각 URL 제거·기사 중복 제거·길이/enum 보정 후 한 배치로 저장."""
    reasons: list[str] = []
    cleaned: list[dict] = []
    for idx, raw in enumerate(body.issues, 1):
        if not isinstance(raw, dict):
            reasons.append(f"#{idx} 객체가 아님")
            continue
        try:
            c = _clean_issue(IssueRaw.model_validate(raw))
        except Exception as e:  # noqa: BLE001 - 불량 항목은 버리고 계속
            reasons.append(f"#{idx} 검증 실패: {str(e).splitlines()[0][:80]}")
            continue
        if c is None:
            reasons.append(f"#{idx} 제목 없음")
            continue
        cleaned.append(c)

    # news_item에 실제 있는 URL만 유효(없는 URL = 환각)
    all_urls = {u for c in cleaned for u in c["urls"]}
    news_by_url: dict[str, NewsItem] = {}
    if all_urls:
        found = (await session.execute(select(NewsItem).where(NewsItem.url.in_(all_urls)))).scalars()
        news_by_url = {n.url: n for n in found}

    kept: list[tuple[dict, list[NewsItem]]] = []
    used: set[str] = set()
    seen_titles: set[str] = set()
    for c in cleaned:
        if len(kept) >= ISSUE_MAX_ITEMS:
            reasons.append(f"'{c['title'][:30]}' 이슈 {ISSUE_MAX_ITEMS}개 초과")
            continue
        key = narrative_llm._norm_title(c["title"])
        if key in seen_titles:
            reasons.append(f"'{c['title'][:30]}' 배치 내 제목 중복")
            continue
        valid = [u for u in c["urls"] if u in news_by_url and u not in used]  # 한 기사는 앞선 이슈에만
        if not valid:
            reasons.append(f"'{c['title'][:30]}' 유효 기사 0건")
            continue
        seen_titles.add(key)
        used.update(valid)
        items = [news_by_url[u] for u in valid]
        # 최신순 정렬하되 첫 유효 URL(대표 기사)을 맨 앞에
        lead, rest = items[0], sorted(
            items[1:], key=lambda n: n.published_at or n.fetched_at, reverse=True
        )
        kept.append((c, [lead, *rest]))

    now = dt.datetime.now(dt.timezone.utc)
    batch_at: dt.datetime | None = None
    if kept:
        batch_at = now
        model = body.model
        for rank, (c, items) in enumerate(kept, 1):
            session.add(
                NewsIssue(
                    batch_at=batch_at,
                    rank=rank,
                    title=c["title"],
                    direction=c["direction"],
                    confidence=c["confidence"],
                    continuing=c["continuing"],
                    facts=c["facts"],
                    interpretation=c["interpretation"],
                    counter=c["counter"],
                    beneficiaries=c["beneficiaries"],
                    victims=c["victims"],
                    sources=[
                        {
                            "title": n.title,
                            "url": n.url,
                            "source": n.source,
                            "published_at": n.published_at.isoformat() if n.published_at else None,
                        }
                        for n in items
                    ],
                    status="new",
                    generated_by=model[:60] if model else None,
                    news_total=body.news_total,
                )
            )
    # 30일 지난 이슈 정리(narrative는 별도 테이블이라 영향 없음)
    await session.execute(delete(NewsIssue).where(NewsIssue.batch_at < now - dt.timedelta(days=ISSUE_KEEP_DAYS)))
    dropped = len(body.issues) - len(kept)
    msg = f"이슈 수신 {len(body.issues)}건 -> 저장 {len(kept)}건, 버림 {dropped}건"
    if body.model:
        msg += f" ({body.model})"
    if body.news_total is not None:
        msg += f" / 창 내 기사 {body.news_total}건"
    await _upsert_log(session, ISSUES_JOB, dt.datetime.now(KST).date(), "ok", len(kept), msg[:500])
    await session.commit()
    return {
        "saved": len(kept),
        "dropped": dropped,
        "reasons": reasons,
        "batch_at": batch_at.isoformat() if batch_at else None,
    }


@router.get("/issues")
async def list_issues(
    batch_at: dt.datetime | None = Query(None, description="특정 배치(ISO). 없으면 최신 배치"),
    include_hidden: bool = Query(False),
    session: AsyncSession = Depends(get_session),
) -> dict:
    target = batch_at if batch_at is not None else await _latest_batch_at(session)
    rows: list[NewsIssue] = []
    if target is not None:
        rows = list(
            (
                await session.execute(
                    select(NewsIssue).where(NewsIssue.batch_at == target).order_by(NewsIssue.rank, NewsIssue.id)
                )
            ).scalars()
        )
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=ISSUE_BATCH_LIST_DAYS)
    batches = (
        await session.execute(
            select(NewsIssue.batch_at, func.count())
            .where(NewsIssue.batch_at >= since)
            .group_by(NewsIssue.batch_at)
            .order_by(NewsIssue.batch_at.desc())
        )
    ).all()
    visible = rows if include_hidden else [r for r in rows if r.status != "hidden"]
    log = await _last_log(session, ISSUES_JOB)
    return {
        "disclaimer": DISCLAIMER,
        "batch_at": rows[0].batch_at.isoformat() if rows else None,
        "generated_by": rows[0].generated_by if rows else None,
        "stats": {
            "articles_in_window": rows[0].news_total if rows else None,
            "grouped": sum(len(r.sources or []) for r in rows),
        },
        "items": await _issue_items(session, visible),
        "batches": [{"batch_at": b.isoformat(), "count": n} for b, n in batches],
        "last_ingest": log,
    }


async def _last_log(session: AsyncSession, job: str) -> dict | None:
    row = (
        await session.execute(
            select(CollectLog).where(CollectLog.job == job).order_by(CollectLog.ran_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return {"ran_at": row.ran_at.isoformat() if row.ran_at else None, "status": row.status, "message": row.message}


async def _get_issue(session: AsyncSession, issue_id: int) -> NewsIssue:
    r = (await session.execute(select(NewsIssue).where(NewsIssue.id == issue_id).with_for_update())).scalar_one_or_none()
    if r is None:
        raise HTTPException(404, "이슈를 찾을 수 없습니다")
    return r


def _track_summary(r: NewsIssue) -> str:
    parts = []
    if r.facts:
        parts.append("사실: " + " / ".join(r.facts))
    if r.interpretation:
        parts.append("해석: " + r.interpretation)
    if r.counter:
        parts.append("반대 해석: " + r.counter)
    summary = "\n".join(parts)
    srcs = (r.sources or [])[:ISSUE_TRACK_MAX_SOURCES]
    if srcs:  # approve_proposal과 같은 형식으로 근거 URL을 요지 끝에 보존
        summary += "\n\n출처:\n" + "\n".join(f"- {s.get('title', '')} {s.get('url', '')}".strip() for s in srcs)
    return summary.strip()


@router.post("/issues/{issue_id}/track")
async def track_issue(issue_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """이슈를 내러티브로 고정한다(사용자 명시 동작). 이미 추적 중이면 기존 narrative_id를 그대로 반환(멱등)."""
    r = await _get_issue(session, issue_id)
    if r.status == "tracked" and r.narrative_id is not None:
        nid = r.narrative_id
    else:
        today = dt.datetime.now(KST).date()
        row = MarketNarrative(
            title=r.title[:200],
            summary=_track_summary(r) or None,
            direction=r.direction,
            beneficiaries=_clean_list(r.beneficiaries),
            victims=_clean_list(r.victims),
            confidence=r.confidence,
            expires_on=today + dt.timedelta(days=DEFAULT_EXPIRY_DAYS),
            active=True,
        )
        session.add(row)
        await session.flush()
        r.status = "tracked"
        r.narrative_id = row.id
        nid = row.id
        await session.commit()
        await session.refresh(r)
    return {"narrative_id": nid, "issue": (await _issue_items(session, [r]))[0]}


@router.post("/issues/{issue_id}/hide")
async def hide_issue(issue_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    r = await _get_issue(session, issue_id)
    if r.status == "tracked":
        raise HTTPException(409, "추적 중인 이슈는 숨길 수 없습니다")
    r.status = "hidden"
    await session.commit()
    await session.refresh(r)
    return {"issue": (await _issue_items(session, [r]))[0]}


@router.post("/issues/{issue_id}/unhide")
async def unhide_issue(issue_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    r = await _get_issue(session, issue_id)
    if r.status == "hidden":
        r.status = "new"
        await session.commit()
        await session.refresh(r)
    return {"issue": (await _issue_items(session, [r]))[0]}


@router.put("/{narrative_id}")
async def put_narrative(
    narrative_id: int, body: NarrativeIn, session: AsyncSession = Depends(get_session)
) -> dict:
    return await _update(narrative_id, NarrativePatch(**body.model_dump()), session)


@router.patch("/{narrative_id}")
async def patch_narrative(
    narrative_id: int, body: NarrativePatch, session: AsyncSession = Depends(get_session)
) -> dict:
    return await _update(narrative_id, body, session)


@router.delete("/{narrative_id}")
async def delete_narrative(narrative_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    row = await session.get(MarketNarrative, narrative_id)
    if row is None:
        raise HTTPException(404, "내러티브를 찾을 수 없습니다")
    await session.delete(row)
    await session.commit()
    return {"deleted": narrative_id}


# ---------------------------------------------------------------- 5.90-2 뉴스 기반 제안


def _proposal_item(p: NarrativeProposal) -> dict:
    return {
        "id": p.id,
        "title": p.title,
        "summary": p.summary,
        "direction": p.direction,
        "beneficiaries": p.beneficiaries or [],
        "victims": p.victims or [],
        "confidence": p.confidence,
        "sources": p.sources or [],
        "status": p.status,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "decided_at": p.decided_at.isoformat() if p.decided_at else None,
        "approved_narrative_id": p.approved_narrative_id,
    }


class ProposalApprove(BaseModel):
    """승인 시 덮어쓸 필드(모두 선택). 지정하지 않은 필드는 제안 값을 그대로 쓴다."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = None
    direction: Direction | None = None
    beneficiaries: list[str] | None = None
    victims: list[str] | None = None
    confidence: Confidence | None = None
    expires_on: dt.date | None = None


async def _last_refresh(session: AsyncSession, job: str = narrative_news.JOB_NAME) -> dict | None:
    row = (
        await session.execute(
            select(CollectLog).where(CollectLog.job == job).order_by(CollectLog.ran_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return {
        "ran_at": row.ran_at.isoformat() if row.ran_at else None,
        "status": row.status,
        "proposals": row.rows,
        "message": row.message,
    }


@router.get("/proposals")
async def list_proposals(
    status: str = Query("pending", pattern="^(pending|approved|dismissed|all)$"),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(NarrativeProposal).order_by(NarrativeProposal.id.desc()).limit(limit)
    if status != "all":
        stmt = stmt.where(NarrativeProposal.status == status)
    rows = list((await session.execute(stmt)).scalars())
    return {
        "disclaimer": DISCLAIMER,
        "llm_configured": narrative_llm.make_client() is not None,
        "running": narrative_news.is_running(),
        "last_refresh": await _last_refresh(session),
        "last_external": await _last_refresh(session, narrative_news.EXTERNAL_JOB_NAME),
        "items": [_proposal_item(r) for r in rows],
    }


# ---------------------------------------------------------------- 방식 B: 호스트 Claude Code 연동

CONTEXT_MAX_NEWS = 80
CONTEXT_DEFAULT_HOURS = 36
USED_NEWS_DAYS = 14
INGEST_MAX_ITEMS = 20
ORIGIN_NOTE = "(생성: Claude Code 호스트 스크립트 — 사람이 검토 후 승인)"


async def _used_source_urls(session: AsyncSession) -> set[str]:
    """최근 제안의 근거로 이미 쓰인 기사 URL(재요약 낭비/중복 제안 방지)."""
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=USED_NEWS_DAYS)
    rows = (
        await session.execute(select(NarrativeProposal.sources).where(NarrativeProposal.created_at >= since))
    ).scalars()
    return {s.get("url") for srcs in rows for s in (srcs or []) if isinstance(s, dict) and s.get("url")}


@router.get("/ingest-context")
async def ingest_context(
    hours: int = Query(CONTEXT_DEFAULT_HOURS, ge=1, le=168),
    limit: int = Query(CONTEXT_MAX_NEWS, ge=1, le=200),
    exclude_used: bool = Query(True, description="최근 제안 근거로 이미 쓴 기사 제외"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """호스트 스크립트(scripts/narrative_claude.py)가 프롬프트를 만들 입력을 한 번에 제공한다."""
    now = dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(hours=hours)
    rows = list(
        (
            await session.execute(
                select(NewsItem).where(func.coalesce(NewsItem.published_at, NewsItem.fetched_at) >= since)
            )
        ).scalars()
    )
    used = await _used_source_urls(session) if exclude_used else set()
    rows = [r for r in rows if r.url not in used]
    news = narrative_news.pick_news(rows, limit)

    today = now.astimezone(KST).date()
    narr = (
        await session.execute(
            select(MarketNarrative).where(
                MarketNarrative.active.is_(True),
                or_(MarketNarrative.expires_on.is_(None), MarketNarrative.expires_on >= today),
            )
        )
    ).scalars()
    rec_since = now - dt.timedelta(days=narrative_news.RECENT_DECIDED_DAYS)
    props = (
        await session.execute(
            select(NarrativeProposal.title, NarrativeProposal.status)
            .where(
                or_(
                    NarrativeProposal.status == "pending",
                    NarrativeProposal.created_at >= rec_since,
                )
            )
            .order_by(NarrativeProposal.id.desc())
        )
    ).all()
    return {
        "generated_at": now.isoformat(),
        "window_hours": hours,
        "news": news,
        "active_narratives": [
            {"title": r.title, "direction": r.direction, "beneficiaries": r.beneficiaries or [], "victims": r.victims or []}
            for r in narr
        ],
        "vocabulary": await narrative_news._vocab(session),
        "recent_proposal_titles": [{"title": t, "status": st} for t, st in props],
        "max_proposals": narrative_llm.MAX_PROPOSALS,
    }


class IngestBody(BaseModel):
    """항목은 느슨하게(dict) 받고 narrative_llm.ProposalOut으로 검증한다 — 불량 항목 하나가 전체를 막지 않게."""

    proposals: list[dict | str | int | None] = Field(max_length=INGEST_MAX_ITEMS)
    model: str | None = Field(default=None, max_length=60)


@router.post("/proposals/ingest")
async def ingest_proposals(body: IngestBody, session: AsyncSession = Depends(get_session)) -> dict:
    """외부(Claude Code) 생성 제안 수신. 검증/출처 URL 매핑/중복 제거는 API 경로와 같은 함수를 쓴다.
    자동 승인 없음 — 모두 pending으로 저장."""
    raw = list(body.proposals)
    urls: set[str] = set()
    for it in raw:
        if isinstance(it, dict) and isinstance(it.get("source_urls"), list):
            urls.update(u.strip() for u in it["source_urls"] if isinstance(u, str) and u.strip())
    news_by_url: dict[str, dict] = {}
    if urls:
        found = (await session.execute(select(NewsItem).where(NewsItem.url.in_(urls)))).scalars()
        news_by_url = {n.url: {"title": n.title, "url": n.url} for n in found}

    drops: list[str] = []
    known = await narrative_news._known_titles(session)
    items = narrative_llm.process_raw_proposals(raw, news_by_url, known, drops)
    for p in items:
        p["summary"] = f"{p['summary']}\n\n{ORIGIN_NOTE}"
        session.add(NarrativeProposal(**p, status="pending"))
    await session.flush()
    msg = f"Claude Code 제안 수신 {len(raw)}건 -> 저장 {len(items)}건, 버림 {len(raw) - len(items)}건"
    if body.model:
        msg += f" ({body.model})"
    await _upsert_log(session, narrative_news.EXTERNAL_JOB_NAME, dt.datetime.now(KST).date(), "ok", len(items), msg[:500])
    await session.commit()
    return {
        "received": len(raw),
        "saved": len(items),
        "dropped": len(raw) - len(items),
        "reasons": drops,
        "saved_titles": [p["title"] for p in items],
        "disclaimer": DISCLAIMER,
    }


@router.get("/refresh-status")
async def refresh_status(session: AsyncSession = Depends(get_session)) -> dict:
    """뉴스 수집(worker) 마지막 상태 + 외부(Claude Code) 마지막 요약 시각."""
    return {
        "llm_configured": narrative_llm.make_client() is not None,
        "running": narrative_news.is_running(),
        "last_refresh": await _last_refresh(session),
        "last_external": await _last_refresh(session, narrative_news.EXTERNAL_JOB_NAME),
    }


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(
    proposal_id: int,
    body: ProposalApprove | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    p = (
        await session.execute(select(NarrativeProposal).where(NarrativeProposal.id == proposal_id).with_for_update())
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, "제안을 찾을 수 없습니다")
    if p.status != "pending":
        raise HTTPException(409, f"이미 처리된 제안입니다 ({p.status})")
    o = body.model_dump(exclude_unset=True) if body else {}
    title = (o.get("title") or p.title).strip()
    if not title:
        raise HTTPException(422, "제목은 필수입니다")
    summary = o["summary"] if "summary" in o else p.summary
    summary = (summary or "").strip()
    # 근거 기사 URL은 MarketNarrative에 별도 컬럼이 없어 요지 끝에 붙여 보존한다.
    if p.sources:
        summary += "\n\n출처:\n" + "\n".join(f"- {s.get('title', '')} {s.get('url', '')}".strip() for s in p.sources)
    today = dt.datetime.now(KST).date()
    row = MarketNarrative(
        title=title[:200],
        summary=summary.strip() or None,
        direction=o.get("direction") or p.direction,
        beneficiaries=_clean_list(o["beneficiaries"] if o.get("beneficiaries") is not None else p.beneficiaries),
        victims=_clean_list(o["victims"] if o.get("victims") is not None else p.victims),
        confidence=o.get("confidence") or p.confidence,
        expires_on=o.get("expires_on") or today + dt.timedelta(days=DEFAULT_EXPIRY_DAYS),
        active=True,
    )
    session.add(row)
    await session.flush()
    p.status = "approved"
    p.decided_at = dt.datetime.now(dt.timezone.utc)
    p.approved_narrative_id = row.id
    await session.commit()
    await session.refresh(row)
    await session.refresh(p)
    return {"proposal": _proposal_item(p), "narrative": (await _with_evidence(session, [row]))[0]}


@router.post("/proposals/{proposal_id}/dismiss")
async def dismiss_proposal(proposal_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    p = (
        await session.execute(select(NarrativeProposal).where(NarrativeProposal.id == proposal_id).with_for_update())
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, "제안을 찾을 수 없습니다")
    if p.status != "pending":
        raise HTTPException(409, f"이미 처리된 제안입니다 ({p.status})")
    p.status = "dismissed"
    p.decided_at = dt.datetime.now(dt.timezone.utc)
    await session.commit()
    await session.refresh(p)
    return {"proposal": _proposal_item(p)}


@router.get("/news")
async def list_news(
    limit: int = Query(50, ge=1, le=200), session: AsyncSession = Depends(get_session)
) -> dict:
    rows = (
        await session.execute(
            select(NewsItem)
            .order_by(func.coalesce(NewsItem.published_at, NewsItem.fetched_at).desc())
            .limit(limit)
        )
    ).scalars()
    return {
        "items": [
            {
                "id": r.id,
                "source": r.source,
                "title": r.title,
                "url": r.url,
                "published_at": r.published_at.isoformat() if r.published_at else None,
                "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None,
                "summary": r.summary,
            }
            for r in rows
        ]
    }


@router.post("/refresh")
async def refresh(session: AsyncSession = Depends(get_session)) -> dict:
    """뉴스 수집 + (LLM 키가 있으면) 제안 생성을 즉시 실행(동기). 이미 실행 중이면 409."""
    result = await narrative_news.run_refresh_locked(session)
    if result is None:
        raise HTTPException(409, "이미 갱신 중입니다")
    await _upsert_log(
        session, narrative_news.JOB_NAME, dt.datetime.now(KST).date(), "ok",
        result["proposals_created"], narrative_news.summarize(result),
    )
    await session.commit()
    return {
        "collected": result["collected"],
        "new_items": result["new_items"],
        "proposals_created": result["proposals_created"],
        "llm_configured": result["llm_configured"],
        "no_key": not result["llm_configured"],
        "external_pending": not result["llm_configured"],
        "source_errors": result["source_errors"],
        "llm_error": result["llm_error"],
        "message": result["message"],
        "disclaimer": DISCLAIMER,
    }
