"""오늘의 이슈(뉴스 묶음) ingest / 조회 / 추적·숨김 / context (PLAN.md §5.106).

LLM은 호출하지 않는다. DB 테스트는 실 dev Postgres를 쓰되 제목 접두사 'ZT' / URL 접두사
'https://zt.test/'로 만든 행만 정리하고, collect_log job명은 테스트용으로 바꿔 실 기록을 건드리지 않는다."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.db import async_session_factory, engine
from app.models import CollectLog, MarketNarrative, NewsIssue, NewsItem
from app.routers import narratives as mod

JOB = "zt_narrative_issues_ingest"
NOW = dt.datetime.now(dt.timezone.utc)


async def _clear() -> None:
    async with async_session_factory() as s:
        await s.execute(delete(NewsIssue).where(NewsIssue.title.ilike("ZT%")))
        await s.execute(delete(MarketNarrative).where(MarketNarrative.title.like("ZT%")))
        await s.execute(delete(NewsItem).where(NewsItem.url.like("https://zt.test/%")))
        await s.execute(delete(CollectLog).where(CollectLog.job == JOB))
        await s.commit()


@pytest.fixture
async def env(monkeypatch):
    monkeypatch.setattr(mod, "ISSUES_JOB", JOB)
    await _clear()
    async with async_session_factory() as s:
        for i in range(1, 7):
            s.add(
                NewsItem(
                    source="네이버증권/테스트",
                    title=f"ZT 기사 {i}",
                    url=f"https://zt.test/{i}",
                    published_at=NOW - dt.timedelta(minutes=i),  # 1이 가장 최신
                    summary="요약" * 100 if i == 1 else None,
                )
            )
        # 24h 창 밖
        s.add(NewsItem(source="x", title="ZT 오래된 기사", url="https://zt.test/old",
                       published_at=NOW - dt.timedelta(hours=30)))
        await s.commit()
    yield
    await _clear()
    await engine.dispose()


def _client() -> AsyncClient:
    app = FastAPI()
    app.include_router(mod.router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _u(*ns: int) -> list[str]:
    return [f"https://zt.test/{n}" for n in ns]


def _issue(title="ZT 이슈", urls=None, **kw):
    d = {"title": title, "direction": "bullish", "confidence": "mid", "continuing": False,
         "facts": ["사실1"], "interpretation": "해석", "counter": "반대", "beneficiaries": ["반도체"],
         "victims": [], "article_urls": urls if urls is not None else _u(1)}
    d.update(kw)
    return d


async def _ingest(c, issues, **kw):
    r = await c.post("/api/narratives/issues/ingest", json={"model": "claude-code:sonnet", "news_total": 77, "issues": issues, **kw})
    assert r.status_code == 200, r.text
    return r.json()


async def test_ingest_validation(env):
    long = "가" * 500
    issues = [
        _issue("ZT 긴 제목 " + long, _u(1, 2, "evil"), direction="up", confidence="zzz",
               facts=["a", "", "b", long, "d"], interpretation=long, counter=long),
        _issue("ZT 환각만", ["https://evil.example/x"]),           # 유효 기사 0건 -> 버림
        _issue("ZT 중복 기사", _u(2, 3)),                          # 2는 앞 이슈가 가져감 -> 3만
        _issue("ZT 기사 겹침만", _u(1, 2)),                         # 전부 겹침 -> 버림
        _issue("zt  중복-제목!", _u(4)),                            # 앞선 'ZT 중복 기사'와 정규화 동일하지 않음 -> 저장
        _issue("ZT 중복 기사", _u(5)),                              # 배치 내 제목 중복 -> 버림
        {"title": "   ", "article_urls": _u(6)},                  # 제목 없음
        "garbage",
        _issue("ZT 일곱", _u(6)),
    ]
    async with _client() as c:
        r = await _ingest(c, issues)
        assert r["saved"] == 4 and r["dropped"] == 5 and r["batch_at"]
        assert len(r["reasons"]) == 5
        got = (await c.get("/api/narratives/issues")).json()
    items = got["items"]
    assert [i["rank"] for i in items] == [1, 2, 3, 4]
    first = items[0]
    assert len(first["title"]) == 200
    assert first["direction"] == "neutral" and first["confidence"] == "low"  # enum 보정
    assert first["facts"] == ["a", "b", "가" * 200]  # 빈 문자열 제거, 3개, 200자
    assert len(first["interpretation"]) == 300 and len(first["counter"]) == 300
    assert [s["url"] for s in first["sources"]] == _u(1, 2)  # 환각 URL 제거, 대표 기사 맨 앞
    assert [s["url"] for s in items[1]["sources"]] == _u(3)  # 중복 기사는 첫 이슈에만
    assert first["sources"][0]["title"] == "ZT 기사 1" and first["sources"][0]["source"] == "네이버증권/테스트"
    assert got["stats"] == {"articles_in_window": 77, "grouped": 5}
    assert got["generated_by"] == "claude-code:sonnet"


async def test_ingest_lead_article_first_then_newest(env):
    async with _client() as c:
        await _ingest(c, [_issue("ZT 대표", _u(4, 1, 3))])  # 4가 대표(가장 오래됨), 나머지는 최신순
        items = (await c.get("/api/narratives/issues")).json()["items"]
    assert [s["url"] for s in items[0]["sources"]] == _u(4, 1, 3)


async def test_ingest_max_eight(env):
    async with _client() as c:
        async with async_session_factory() as s:
            for i in range(10, 22):
                s.add(NewsItem(source="x", title=f"ZT 기사 {i}", url=f"https://zt.test/{i}", published_at=NOW))
            await s.commit()
        r = await _ingest(c, [_issue(f"ZT 이슈 {i}", _u(i)) for i in range(10, 20)])
    assert r["saved"] == 8 and r["dropped"] == 2


async def test_ingest_zero_saved_keeps_previous_batch(env):
    async with _client() as c:
        first = await _ingest(c, [_issue("ZT 첫 배치", _u(1))])
        r = await _ingest(c, [_issue("ZT 환각", ["https://evil.example/y"])])
        assert r["saved"] == 0 and r["batch_at"] is None
        got = (await c.get("/api/narratives/issues")).json()
    assert got["batch_at"] == first["batch_at"] and got["items"][0]["title"] == "ZT 첫 배치"
    async with async_session_factory() as s:
        log = (await s.execute(select(CollectLog).where(CollectLog.job == JOB))).scalar_one()
        assert log.status == "ok" and "저장 0건" in log.message


async def test_get_latest_batch_hidden_and_batches(env):
    async with _client() as c:
        await _ingest(c, [_issue("ZT 옛 배치", _u(1))])
        await _ingest(c, [_issue("ZT 신 A", _u(1)), _issue("ZT 신 B", _u(2), beneficiaries=["반도체"], victims=["은행"])])
        got = (await c.get("/api/narratives/issues")).json()
        assert [i["title"] for i in got["items"]] == ["ZT 신 A", "ZT 신 B"]  # 최신 배치만
        assert got["disclaimer"] == "참고용, 매매 신호 아님"
        assert len(got["batches"]) >= 2 and got["batches"][0]["batch_at"] == got["batch_at"] and got["batches"][0]["count"] == 2
        assert got["last_ingest"]["status"] == "ok" and "저장 2건" in got["last_ingest"]["message"]
        old = (await c.get("/api/narratives/issues", params={"batch_at": got["batches"][1]["batch_at"]})).json()
        assert [i["title"] for i in old["items"]] == ["ZT 옛 배치"]
        # evidence: 내러티브와 같은 모양(list of term/role/source/verdict...) + alignment
        b = got["items"][1]
        assert isinstance(b["evidence"], list) and b["alignment"] in {"aligned", "divergent", "mixed", "unknown", "none"}
        for e in b["evidence"]:
            assert {"term", "role", "source", "name", "date", "verdict"} <= set(e)
        # hidden
        hid = got["items"][0]["id"]
        assert (await c.post(f"/api/narratives/issues/{hid}/hide")).json()["issue"]["status"] == "hidden"
        assert [i["title"] for i in (await c.get("/api/narratives/issues")).json()["items"]] == ["ZT 신 B"]
        inc = (await c.get("/api/narratives/issues?include_hidden=true")).json()
        assert len(inc["items"]) == 2 and inc["stats"]["grouped"] == 2
        assert (await c.post(f"/api/narratives/issues/{hid}/unhide")).json()["issue"]["status"] == "new"


async def test_evidence_same_shape_as_narrative(env):
    """같은 용어 -> 이슈와 내러티브의 evidence가 동일(공용 로직 재사용 검증)."""
    async with _client() as c:
        await _ingest(c, [_issue("ZT 증거", _u(1), beneficiaries=["반도체", "전기"], victims=["은행"])])
        issue = (await c.get("/api/narratives/issues")).json()["items"][0]
        n = (await c.post("/api/narratives/", json={"title": "ZT 내러티브", "beneficiaries": ["반도체", "전기"], "victims": ["은행"]})).json()
    assert issue["evidence"] == n["evidence"] and issue["alignment"] == n["alignment"]


async def test_track_idempotent_and_summary(env):
    async with _client() as c:
        await _ingest(c, [_issue("ZT 추적", _u(1, 2, 3, 4, 5, 6), facts=["f1", "f2"], interpretation="해석문", counter="반대문")])
        iid = (await c.get("/api/narratives/issues")).json()["items"][0]["id"]
        r1 = (await c.post(f"/api/narratives/issues/{iid}/track")).json()
        r2 = (await c.post(f"/api/narratives/issues/{iid}/track")).json()
        assert r1["narrative_id"] == r2["narrative_id"] and r1["issue"]["status"] == "tracked"
        assert r1["issue"]["narrative_id"] == r1["narrative_id"] and "evidence" in r1["issue"]
        lst = (await c.get("/api/narratives/")).json()["items"]
        n = [x for x in lst if x["id"] == r1["narrative_id"]]
        assert len(n) == 1 and len([x for x in lst if x["title"] == "ZT 추적"]) == 1
        n = n[0]
        assert n["direction"] == "bullish" and n["confidence"] == "mid" and n["beneficiaries"] == ["반도체"]
        assert n["expires_on"] == (dt.datetime.now(mod.KST).date() + dt.timedelta(days=30)).isoformat()
        s = n["summary"]
        assert s.startswith("사실: f1 / f2\n해석: 해석문\n반대 해석: 반대문\n\n출처:\n- ZT 기사 1 https://zt.test/1")
        assert s.count("https://zt.test/") == 5  # 근거 URL 최대 5개
        # 추적 중인 이슈는 숨길 수 없음
        assert (await c.post(f"/api/narratives/issues/{iid}/hide")).status_code == 409


async def test_hide_unhide_404(env):
    async with _client() as c:
        for act in ("track", "hide", "unhide"):
            assert (await c.post(f"/api/narratives/issues/99999999/{act}")).status_code == 404


async def test_context_window_and_previous_titles(env):
    async with _client() as c:
        await _ingest(c, [_issue("ZT 직전 이슈", _u(1)), _issue("ZT 직전 둘째", _u(2))])
        ctx = (await c.get("/api/narratives/issues/context?limit=300")).json()
    mine = [n for n in ctx["news"] if n["url"].startswith("https://zt.test/")]
    assert {n["url"] for n in mine} == set(_u(1, 2, 3, 4, 5, 6))  # 30h 전 기사는 창 밖
    assert "https://zt.test/old" not in {n["url"] for n in mine}
    # 이미 이슈 근거로 쓴 기사(1, 2)도 제외하지 않는다
    assert [n["n"] for n in ctx["news"]] == list(range(1, len(ctx["news"]) + 1))
    n1 = next(n for n in mine if n["url"].endswith("/1"))
    assert len(n1["snippet"]) == 120 and set(n1) >= {"n", "id", "source", "title", "snippet", "published_at"}
    assert ctx["previous_issue_titles"] == ["ZT 직전 이슈", "ZT 직전 둘째"]
    assert set(ctx["window"]) == {"from", "to"} and isinstance(ctx["vocabulary"], list)
    assert all("title" in a and "direction" in a for a in ctx["active_narratives"])
    # limit 상한
    async with _client() as c:
        assert len((await c.get("/api/narratives/issues/context?limit=2")).json()["news"]) == 2


async def test_old_batches_purged_on_ingest(env):
    async with async_session_factory() as s:
        s.add(NewsIssue(batch_at=NOW - dt.timedelta(days=31), rank=1, title="ZT 만료", direction="neutral", confidence="low"))
        await s.commit()
    async with _client() as c:
        await _ingest(c, [_issue("ZT 새것", _u(1))])
    async with async_session_factory() as s:
        titles = set((await s.execute(select(NewsIssue.title).where(NewsIssue.title.ilike("ZT%")))).scalars())
    assert titles == {"ZT 새것"}
