"""시장 내러티브 메모 (PLAN.md §5.90-1) — evidence 매칭 순수 로직 + 라우터 CRUD.

CRUD는 실 dev Postgres를 쓰며 제목 접두사 'ZT'로 만든 행만 정리한다(자동매매 라우터를
import하지 않아 킬스위치 가드를 타지 않는다). 시드/실데이터는 건드리지 않는다."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db import async_session_factory, engine
from app.market_hours import KST
from app.models import MarketNarrative
from app.routers import narratives as mod

# ---------------------------------------------------------------- 순수 로직

FLOW = [
    {"market": "kospi", "name": "전기/전자", "date": "2026-11-04", "foreign": -500, "inst": -200, "indiv": 700},
    {"market": "kospi", "name": "화학", "date": "2026-11-04", "foreign": 300, "inst": 100, "indiv": -400},
]
GROUP = [
    {"group_type": "upjong", "name": "반도체와반도체장비", "date": "2026-11-04", "change_rate": -2.5},
    {"group_type": "theme", "name": "2차전지", "date": "2026-11-04", "change_rate": 1.2},
]


def test_name_matches():
    assert mod.name_matches("반도체", "반도체와반도체장비")
    assert mod.name_matches("전기전자 업종", "전기/전자") is False  # 슬래시 차이는 정직하게 불일치
    assert mod.name_matches("전기/전자", "전기/전자")
    assert not mod.name_matches("전", "전기/전자")  # 너무 짧은 용어
    assert not mod.name_matches("반도체", None)


def test_judge_rules():
    assert mod.judge("beneficiary", -700, None) == "divergent"
    assert mod.judge("beneficiary", 400, None) == "aligned"
    assert mod.judge("victim", 400, None) == "divergent"
    assert mod.judge("victim", -400, None) == "aligned"
    assert mod.judge("beneficiary", None, -2.5) == "divergent"  # 수급 없으면 등락률
    assert mod.judge("beneficiary", 0, None) == "unknown"
    assert mod.judge("beneficiary", None, None) == "unknown"


def test_build_evidence_and_summary():
    ev = mod.build_evidence("beneficiary", ["전기/전자", "반도체"], FLOW, GROUP)
    assert {(e["source"], e["verdict"]) for e in ev} == {
        ("sector_flow", "divergent"),
        ("group_snapshot", "divergent"),
    }
    assert mod.summarize(ev) == "divergent"
    flow_ev = next(e for e in ev if e["source"] == "sector_flow")
    assert flow_ev["foreign_net"] == -500 and flow_ev["unit"] == "백만원"

    ev2 = mod.build_evidence("victim", ["화학"], FLOW, GROUP)
    assert mod.summarize(ev2) == "divergent"  # 피해인데 순매수 +
    mixed = mod.build_evidence("beneficiary", ["화학", "반도체"], FLOW, GROUP)
    assert mod.summarize(mixed) == "mixed"


def test_no_match_is_empty():
    assert mod.build_evidence("beneficiary", ["우주항공"], FLOW, GROUP) == []
    assert mod.summarize([]) == "none"


# ---------------------------------------------------------------- 라우터 CRUD


async def _clear() -> None:
    async with async_session_factory() as session:
        await session.execute(MarketNarrative.__table__.delete().where(MarketNarrative.title.like("ZT%")))
        await session.commit()


@pytest.fixture
async def clean():
    await _clear()
    yield
    await _clear()
    await engine.dispose()


def _client() -> AsyncClient:
    app = FastAPI()
    app.include_router(mod.router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_validation(clean):
    async with _client() as c:
        assert (await c.post("/api/narratives/", json={"title": ""})).status_code == 422
        assert (await c.post("/api/narratives/", json={"title": "   "})).status_code == 422
        assert (await c.post("/api/narratives/", json={"title": "ZTx", "direction": "up"})).status_code == 422
        assert (await c.post("/api/narratives/", json={"title": "ZTx", "confidence": "max"})).status_code == 422


async def test_crud_roundtrip(clean):
    async with _client() as c:
        r = await c.post(
            "/api/narratives/",
            json={
                "title": "ZT 반도체 약세",
                "summary": "요지",
                "direction": "bearish",
                "beneficiaries": ["우주항공우주항공", " "],
                "victims": ["반도체", "반도체"],
                "confidence": "high",
            },
        )
        assert r.status_code == 200
        body = r.json()
        nid = body["id"]
        assert body["victims"] == ["반도체"] and body["beneficiaries"] == ["우주항공우주항공"]
        assert body["disclaimer"] == "참고용, 매매 신호 아님"
        assert isinstance(body["evidence"], list)

        lst = (await c.get("/api/narratives/")).json()["items"]
        assert any(i["id"] == nid for i in lst)

        p = await c.patch(f"/api/narratives/{nid}", json={"confidence": "low", "active": False})
        assert p.status_code == 200 and p.json()["confidence"] == "low"
        assert not any(i["id"] == nid for i in (await c.get("/api/narratives/")).json()["items"])
        inc = (await c.get("/api/narratives/?include_inactive=true")).json()["items"]
        assert any(i["id"] == nid for i in inc)

        put = await c.put(f"/api/narratives/{nid}", json={"title": "ZT 수정", "direction": "neutral"})
        assert put.status_code == 200 and put.json()["title"] == "ZT 수정" and put.json()["active"] is True

        assert (await c.delete(f"/api/narratives/{nid}")).status_code == 200
        assert (await c.delete(f"/api/narratives/{nid}")).status_code == 404
        assert (await c.patch(f"/api/narratives/{nid}", json={"active": True})).status_code == 404


async def test_expired_hidden_by_default(clean):
    today = dt.datetime.now(KST).date()
    async with _client() as c:
        old = (await c.post("/api/narratives/", json={"title": "ZT 만료", "expires_on": (today - dt.timedelta(days=1)).isoformat()})).json()
        live = (await c.post("/api/narratives/", json={"title": "ZT 오늘까지", "expires_on": today.isoformat()})).json()
        ids = [i["id"] for i in (await c.get("/api/narratives/")).json()["items"]]
        assert live["id"] in ids and old["id"] not in ids
        ids = [i["id"] for i in (await c.get("/api/narratives/?include_inactive=true")).json()["items"]]
        assert old["id"] in ids
