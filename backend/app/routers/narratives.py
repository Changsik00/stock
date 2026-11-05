"""시장 내러티브 메모 CRUD + 섹터 수급 일치 evidence (PLAN.md §5.90-1) +
뉴스 기반 LLM 제안 큐/승인 API (§5.90-2).

사용자가 직접 기록한 서사(수혜/피해 섹터)를 최근 거래일의 sector_flow(외국인/기관/개인
순매수, 백만원)·group_snapshot(등락률)과 이름 부분일치로 대조해 보여준다.
**참고용이며 매매 신호가 아니다** — 자동매매 로직은 이 테이블을 읽지 않는다."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import narrative_llm
from ..collectors import narrative_news
from ..collectors.base import _upsert_log
from ..db import get_session
from ..market_hours import KST
from ..models import CollectLog, GroupSnapshot, MarketNarrative, NarrativeProposal, NewsItem, SectorFlow

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


async def _with_evidence(session: AsyncSession, rows: list[MarketNarrative]) -> list[dict]:
    all_terms = sorted({t for r in rows for t in (r.beneficiaries or []) + (r.victims or [])})
    flow_rows, group_rows = await _load_latest(session, all_terms)
    items = []
    for r in rows:
        ev = build_evidence("beneficiary", r.beneficiaries or [], flow_rows, group_rows) + build_evidence(
            "victim", r.victims or [], flow_rows, group_rows
        )
        items.append(_item(r, {"evidence": ev, "alignment": summarize(ev), "disclaimer": DISCLAIMER}))
    return items


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
