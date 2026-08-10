"""Unit tests for GET /api/markets/flow/live (app.routers.markets, PLAN.md §6
Phase 3.7-3).

Uses httpx.AsyncClient + ASGITransport against the real FastAPI app, with
get_session overridden to a fake AsyncSession (no real DB) and
collectors.market_flow.fetch_live_flow (imported into the router module as
markets.fetch_live_flow) monkeypatched (no real Kiwoom network call) — same
no-DB/no-network philosophy as test_markets_breadth_router.py.

The fake session below is a plain FIFO queue of pre-scripted results rather
than a SQL-introspecting fake, because routers.markets._fetch_flow_confirmed_for_market
issues its two queries (max(date) then the matching rows) in a fixed,
well-known order per market — scripting the queue exactly matches that order
without needing to parse the compiled statement.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import get_session
from app.main import app
from app.models import MarketFlow
from app.routers import markets

KOSPI_FLOWS = [
    {"investor": "개인", "net_value": 50624, "net_volume": None},
    {"investor": "외국인", "net_value": -20698, "net_volume": None},
    {"investor": "기관계", "net_value": -31684, "net_volume": None},
]
KOSDAQ_FLOWS = [
    {"investor": "개인", "net_value": 4815, "net_volume": None},
    {"investor": "외국인", "net_value": -3609, "net_volume": None},
    {"investor": "기관계", "net_value": -1567, "net_volume": None},
]

# 각 테스트가 markets.fetch_live_flow를 monkeypatch 대신 직접 대입/복원하는 이유:
# 모듈 전역 함수 참조라 finally 블록에서 원복해야 테스트 실패로 조기 종료돼도 다음
# 테스트에 새지 않는다. 원본 참조를 임포트 시점에 저장해 둔다.
_real_fetch_live_flow = markets.fetch_live_flow


class _FakeScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeRowsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """execute() 호출 순서대로 미리 짜둔 결과를 하나씩 반환하는 FIFO 큐."""

    def __init__(self, results):
        self._results = list(results)

    async def execute(self, stmt):
        return self._results.pop(0)


@pytest.fixture(autouse=True)
def _reset_flow_live_cache():
    markets._flow_live_cache["data"] = None
    markets._flow_live_cache["ts"] = 0.0
    yield
    markets._flow_live_cache["data"] = None
    markets._flow_live_cache["ts"] = 0.0


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _session_with(results):
    async def fake_get_session():
        yield _FakeSession(results)

    return fake_get_session


async def test_flow_live_returns_both_markets_from_kiwoom(monkeypatch):
    # 이 테스트는 "장중" 경로(키움 라이브 우선 호출)를 검증하므로, 실제 wall-clock이
    # 언제든 그렇게 동작하도록 장중을 강제한다(2026-07-20 버그 수정으로
    # _warm_flow_live가 실제 market_closed를 확인해 게이트하기 시작했다 — 장 마감
    # 케이스는 아래 별도 테스트가 다룬다).
    monkeypatch.setattr(markets, "_market_closed_kst", lambda now_kst: False)

    async def fake_fetch_live_flow(client, market, target_date):
        return KOSPI_FLOWS if market == "kospi" else KOSDAQ_FLOWS

    markets.fetch_live_flow = fake_fetch_live_flow
    try:
        # DB는 라이브가 둘 다 성공하면 전혀 조회되지 않아야 한다 — 빈 큐로 검증.
        app.dependency_overrides[get_session] = _session_with([])

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/markets/flow/live")
    finally:
        markets.fetch_live_flow = _real_fetch_live_flow

    assert resp.status_code == 200
    body = resp.json()
    assert body["kospi"]["provisional"] is True
    assert body["kospi"]["source"] == "kiwoom_live"
    assert body["kospi"]["investors"]["외국인"]["net_value"] == -20698
    assert body["kosdaq"]["investors"]["개인"]["net_value"] == 4815
    assert body["market_closed"] is False
    assert "cached_at" in body


async def test_flow_live_falls_back_to_db_when_kiwoom_fails(monkeypatch):
    monkeypatch.setattr(markets, "_market_closed_kst", lambda now_kst: False)

    async def fake_fetch_live_flow(client, market, target_date):
        raise RuntimeError("kiwoom auth failed")

    markets.fetch_live_flow = fake_fetch_live_flow
    try:
        kospi_rows = [
            MarketFlow(market="kospi", date=dt.date(2026, 7, 17), investor="개인", net_value=111, net_volume=None),
            MarketFlow(
                market="kospi", date=dt.date(2026, 7, 17), investor="외국인", net_value=-222, net_volume=None
            ),
        ]
        # 순서: kospi max -> kospi rows -> kosdaq max(없음, rows 조회 생략).
        results = [
            _FakeScalarResult(dt.date(2026, 7, 17)),
            _FakeRowsResult(kospi_rows),
            _FakeScalarResult(None),
        ]
        app.dependency_overrides[get_session] = _session_with(results)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/markets/flow/live")
    finally:
        markets.fetch_live_flow = _real_fetch_live_flow

    assert resp.status_code == 200
    body = resp.json()
    assert body["kospi"]["provisional"] is False
    assert body["kospi"]["source"] == "market_flow_db"
    assert body["kospi"]["date"] == "2026-07-17"
    assert body["kospi"]["investors"]["개인"]["net_value"] == 111
    assert body["kosdaq"] is None


async def test_flow_live_502_when_both_kiwoom_and_db_fail(monkeypatch):
    monkeypatch.setattr(markets, "_market_closed_kst", lambda now_kst: False)

    async def fake_fetch_live_flow(client, market, target_date):
        raise RuntimeError("kiwoom auth failed")

    markets.fetch_live_flow = fake_fetch_live_flow
    try:
        # 순서: kospi max(없음) -> kosdaq max(없음). 둘 다 None이라 rows 조회는 없다.
        results = [_FakeScalarResult(None), _FakeScalarResult(None)]
        app.dependency_overrides[get_session] = _session_with(results)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/markets/flow/live")
    finally:
        markets.fetch_live_flow = _real_fetch_live_flow

    assert resp.status_code == 502


async def test_flow_live_caches_within_ttl(monkeypatch):
    monkeypatch.setattr(markets, "_market_closed_kst", lambda now_kst: False)
    calls = []

    async def fake_fetch_live_flow(client, market, target_date):
        calls.append(market)
        return KOSPI_FLOWS if market == "kospi" else KOSDAQ_FLOWS

    markets.fetch_live_flow = fake_fetch_live_flow
    try:
        app.dependency_overrides[get_session] = _session_with([])

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.get("/api/markets/flow/live")
            r2 = await client.get("/api/markets/flow/live")
    finally:
        markets.fetch_live_flow = _real_fetch_live_flow

    assert r1.json()["cached_at"] == r2.json()["cached_at"]
    assert calls == ["kospi", "kosdaq"]


def test_market_closed_flag_matches_kst_clock():
    before_close = dt.datetime(2026, 7, 20, 10, 0, tzinfo=markets.KST)
    after_close = dt.datetime(2026, 7, 20, 16, 0, tzinfo=markets.KST)
    assert markets._market_closed_kst(before_close) is False
    assert markets._market_closed_kst(after_close) is True


# ---------------------------------------------------------------------------
# 장 마감 게이트 (2026-07-20 버그 수정, 2026-08-10 재수정) — market_closed면
# 먼저 market_flow DB 확정치를 시장별로 확인한다. 확정치 date가 이미 오늘이면
# (그날 18:00 배치가 이미 돌았음) 그대로 쓰고 키움을 다시 부르지 않는다.
# 확정치 date가 아직 오늘이 아니면(배치 미실행, 사용자가 겪은 버그 — "장마감
# 했는데 지수가 7일 전 정보") 그 시장만 키움 라이브를 한 번 더 시도하고, 그마저
# 실패하면 옛 확정치(또는 None)로 최종 폴백한다.
# ---------------------------------------------------------------------------


def _today_kst_date() -> dt.date:
    return dt.datetime.now(markets.KST).date()


async def test_flow_live_market_closed_skips_kiwoom_when_confirmed_is_today(monkeypatch):
    """장 마감 + DB 확정치 date가 이미 오늘이면(그날 배치가 이미 돌았음) 두
    시장 모두 키움 라이브를 다시 부르지 않는다."""
    monkeypatch.setattr(markets, "_market_closed_kst", lambda now_kst: True)

    async def fake_fetch_live_flow(client, market, target_date):  # pragma: no cover - 불리면 안 됨
        raise AssertionError(f"fetch_live_flow should not be called for {market} when confirmed is already today's")

    markets.fetch_live_flow = fake_fetch_live_flow
    try:
        today = _today_kst_date()
        kospi_rows = [MarketFlow(market="kospi", date=today, investor="개인", net_value=111, net_volume=None)]
        kosdaq_rows = [MarketFlow(market="kosdaq", date=today, investor="개인", net_value=222, net_volume=None)]
        # 순서: kospi max -> kospi rows -> kosdaq max -> kosdaq rows.
        results = [
            _FakeScalarResult(today),
            _FakeRowsResult(kospi_rows),
            _FakeScalarResult(today),
            _FakeRowsResult(kosdaq_rows),
        ]
        app.dependency_overrides[get_session] = _session_with(results)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/markets/flow/live")
    finally:
        markets.fetch_live_flow = _real_fetch_live_flow

    assert resp.status_code == 200
    body = resp.json()
    assert body["market_closed"] is True
    assert body["kospi"]["provisional"] is False
    assert body["kospi"]["source"] == "market_flow_db"
    assert body["kospi"]["date"] == today.isoformat()
    assert body["kospi"]["investors"]["개인"]["net_value"] == 111
    assert body["kosdaq"]["date"] == today.isoformat()
    assert body["kosdaq"]["investors"]["개인"]["net_value"] == 222


async def test_flow_live_market_closed_retries_kiwoom_when_confirmed_is_stale(monkeypatch):
    """2026-08-10 버그 수정 — 사용자 지적("장마감 했는데 대시보드 지수가 7일
    전 정보다"). 장 마감 + 확정치가 오늘 날짜가 아니면(18:00 배치 미실행) 곧바로
    옛 확정치로 폴백하지 않고 키움 라이브를 한 번 더 시도해야 한다 — 라이브가
    성공하면 옛 확정치가 아니라 라이브 값을 응답에 반영한다."""
    monkeypatch.setattr(markets, "_market_closed_kst", lambda now_kst: True)

    async def fake_fetch_live_flow(client, market, target_date):
        return KOSPI_FLOWS if market == "kospi" else KOSDAQ_FLOWS

    markets.fetch_live_flow = fake_fetch_live_flow
    try:
        stale_date = dt.date(2026, 7, 17)
        kospi_stale_rows = [
            MarketFlow(market="kospi", date=stale_date, investor="개인", net_value=1, net_volume=None)
        ]
        kosdaq_stale_rows = [
            MarketFlow(market="kosdaq", date=stale_date, investor="개인", net_value=2, net_volume=None)
        ]
        # 순서: kospi max -> kospi rows(확정치 신선도 판단용, 오늘 아님) -> kosdaq
        # max -> kosdaq rows. 둘 다 라이브 재시도로 채워지므로 그 이상 DB 조회는 없다.
        results = [
            _FakeScalarResult(stale_date),
            _FakeRowsResult(kospi_stale_rows),
            _FakeScalarResult(stale_date),
            _FakeRowsResult(kosdaq_stale_rows),
        ]
        app.dependency_overrides[get_session] = _session_with(results)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/markets/flow/live")
    finally:
        markets.fetch_live_flow = _real_fetch_live_flow

    assert resp.status_code == 200
    body = resp.json()
    assert body["market_closed"] is True
    # 라이브 경로로 채워졌어야 한다 — 옛 확정치(net_value=1/2)가 아니라 라이브
    # 값(provisional=True, kiwoom_live)이어야 한다.
    assert body["kospi"]["provisional"] is True
    assert body["kospi"]["source"] == "kiwoom_live"
    assert body["kospi"]["investors"]["외국인"]["net_value"] == -20698
    assert body["kosdaq"]["provisional"] is True
    assert body["kosdaq"]["source"] == "kiwoom_live"
    assert body["kosdaq"]["investors"]["개인"]["net_value"] == 4815


async def test_flow_live_market_closed_stale_confirmed_falls_back_when_kiwoom_also_fails(monkeypatch):
    """확정치도 옛날 날짜고 키움 라이브 재시도도 실패하면 마지막 안전망으로
    옛 확정치를 그대로 보여준다 — 502가 아니라 최선의 근사치. `_warm_flow_live`는
    라이브가 채우지 못한 시장에 대해 최종 루프에서 확정치를 다시 조회하므로
    (index-tiles/live와 달리 재사용하지 않는 기존 관례), 시장당 확정치 조회가
    두 번(재시도 판단용 + 최종 폴백용) 일어난다."""
    monkeypatch.setattr(markets, "_market_closed_kst", lambda now_kst: True)

    async def fake_fetch_live_flow(client, market, target_date):
        raise RuntimeError("kiwoom boom")

    markets.fetch_live_flow = fake_fetch_live_flow
    try:
        stale_date = dt.date(2026, 7, 17)
        kospi_rows = [
            MarketFlow(market="kospi", date=stale_date, investor="개인", net_value=111, net_volume=None)
        ]
        # 순서: (신선도 판단) kospi max -> kospi rows -> kosdaq max(없음) ->
        # (라이브 재시도 실패 뒤 최종 폴백 루프) kospi max -> kospi rows -> kosdaq max(없음).
        results = [
            _FakeScalarResult(stale_date),
            _FakeRowsResult(kospi_rows),
            _FakeScalarResult(None),
            _FakeScalarResult(stale_date),
            _FakeRowsResult(kospi_rows),
            _FakeScalarResult(None),
        ]
        app.dependency_overrides[get_session] = _session_with(results)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/markets/flow/live")
    finally:
        markets.fetch_live_flow = _real_fetch_live_flow

    assert resp.status_code == 200
    body = resp.json()
    assert body["market_closed"] is True
    assert body["kospi"]["provisional"] is False
    assert body["kospi"]["source"] == "market_flow_db"
    assert body["kospi"]["date"] == "2026-07-17"
    assert body["kospi"]["investors"]["개인"]["net_value"] == 111
    assert body["kosdaq"] is None


async def test_flow_live_market_closed_no_db_returns_empty_not_502(monkeypatch):
    monkeypatch.setattr(markets, "_market_closed_kst", lambda now_kst: True)

    async def fake_fetch_live_flow(client, market, target_date):
        raise RuntimeError("kiwoom boom")

    markets.fetch_live_flow = fake_fetch_live_flow
    try:
        # 배치가 한 번도 안 돌았다고 가정 — 두 시장 다 max(date)가 None(신선도
        # 판단 및 최종 폴백 루프에서 각각 한 번씩, 총 두 번씩 조회).
        results = [
            _FakeScalarResult(None),
            _FakeScalarResult(None),
            _FakeScalarResult(None),
            _FakeScalarResult(None),
        ]
        app.dependency_overrides[get_session] = _session_with(results)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/markets/flow/live")
    finally:
        markets.fetch_live_flow = _real_fetch_live_flow

    # 장 마감 + DB도 없음은 "소스 장애"가 아니라 "아직 없음"이므로 502가 아니다
    # (breadth/live의 동일 정책과 일관, test_markets_breadth_router.py 참고).
    assert resp.status_code == 200
    body = resp.json()
    assert body["market_closed"] is True
    assert body["kospi"] is None
    assert body["kosdaq"] is None


async def test_flow_live_market_open_unaffected_by_closed_gate(monkeypatch):
    """장중(``market_closed=False``)이면 이번 수정과 무관하게 기존 동작 그대로
    (회귀 없음) — 확정치 신선도 조회 자체를 건너뛰고 바로 키움을 부른다."""
    monkeypatch.setattr(markets, "_market_closed_kst", lambda now_kst: False)

    async def fake_fetch_live_flow(client, market, target_date):
        return KOSPI_FLOWS if market == "kospi" else KOSDAQ_FLOWS

    markets.fetch_live_flow = fake_fetch_live_flow
    try:
        app.dependency_overrides[get_session] = _session_with([])

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/markets/flow/live")
    finally:
        markets.fetch_live_flow = _real_fetch_live_flow

    assert resp.status_code == 200
    body = resp.json()
    assert body["market_closed"] is False
    assert body["kospi"]["provisional"] is True
    assert body["kospi"]["source"] == "kiwoom_live"
