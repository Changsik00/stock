"""Unit tests for GET /api/markets/attention (app.routers.markets) — "실시간
관심 종목 TOP20" 카드, 키움 ka00198 소스.

httpx.AsyncClient + ASGITransport against the real FastAPI app, with
get_session overridden to a fake AsyncSession (no real DB) and
app.routers.markets.KiwoomClient monkeypatched to a fake async-context-manager
class (no real Kiwoom network call) — same no-DB/no-network philosophy as
test_markets_flow_live_router.py. Since KiwoomClient is used here as
``async with KiwoomClient() as client:``, the fake substituted for
``markets.KiwoomClient`` must itself be an async context manager class (not
just a fake function like flow/live's `fetch_live_flow`).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import get_session
from app.main import app
from app.routers import markets

_real_kiwoom_client = markets.KiwoomClient


class _FakeRowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """execute() 호출 순서대로 미리 짜둔 결과를 하나씩 반환하는 FIFO 큐."""

    def __init__(self, results):
        self._results = list(results)

    async def execute(self, stmt):
        return self._results.pop(0)


def _session_with(results):
    async def fake_get_session():
        yield _FakeSession(results)

    return fake_get_session


def _make_fake_kiwoom_client(item_inq_rank=None, raise_exc=None):
    """markets.KiwoomClient 대체용 fake — `async with KiwoomClient() as client:`
    로 쓰이므로 클래스 자체가 async context manager여야 한다."""

    class _FakeClient:
        call_count = 0

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def realtime_inquiry_rank(self, qry_tp="4"):
            _FakeClient.call_count += 1
            if raise_exc is not None:
                raise raise_exc
            data = {
                "return_code": 0,
                "return_msg": "",
                "item_inq_rank": item_inq_rank or [],
            }
            headers = {"cont-yn": "N", "next-key": "", "api-id": "ka00198"}
            return data, headers

    return _FakeClient


@pytest.fixture(autouse=True)
def _reset_attention_cache():
    markets._attention_cache["data"] = None
    markets._attention_cache["ts"] = 0.0
    yield
    markets._attention_cache["data"] = None
    markets._attention_cache["ts"] = 0.0


@pytest.fixture(autouse=True)
def _restore_kiwoom_client():
    yield
    markets.KiwoomClient = _real_kiwoom_client


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


async def test_attention_live_happy_path_with_stock_join():
    rows = [
        {"stk_nm": "SK하이닉스", "bigd_rank": "1", "stk_cd": "000660", "base_comp_chgr": "-12.10"},
        {"stk_nm": "삼성전자", "bigd_rank": "2", "stk_cd": "005930", "base_comp_chgr": "-9.30"},
        {"stk_nm": "기아", "bigd_rank": "3", "stk_cd": "000270", "base_comp_chgr": "+1.72"},
    ]
    markets.KiwoomClient = _make_fake_kiwoom_client(item_inq_rank=rows)

    stock_rows = [
        ("000660", "SK하이닉스", "KOSPI", False),
        ("005930", "삼성전자", "KOSPI", False),
        ("000270", "기아", "KOSPI", False),
    ]
    app.dependency_overrides[get_session] = _session_with([_FakeRowsResult(stock_rows)])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/attention")

    assert resp.status_code == 200
    body = resp.json()
    assert body["qry_tp"] == "4"
    assert "queried_at" in body
    assert len(body["rows"]) == 3
    first = body["rows"][0]
    assert first == {
        "rank": 1,
        "code": "000660",
        "name": "SK하이닉스",
        "change_rate": -12.10,
        "is_etf": False,
        "market": "kospi",
    }
    assert body["rows"][2]["change_rate"] == 1.72


async def test_attention_live_row_without_stock_match_still_included():
    rows = [{"stk_nm": "미등록종목", "bigd_rank": "1", "stk_cd": "999999", "base_comp_chgr": "+0.50"}]
    markets.KiwoomClient = _make_fake_kiwoom_client(item_inq_rank=rows)

    # stocks 테이블에 매칭되는 행이 없음.
    app.dependency_overrides[get_session] = _session_with([_FakeRowsResult([])])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/attention")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["code"] == "999999"
    assert row["name"] == "미등록종목"  # TR stk_nm 폴백
    assert row["market"] is None
    assert row["is_etf"] is False


async def test_attention_live_502_when_kiwoom_client_raises():
    markets.KiwoomClient = _make_fake_kiwoom_client(raise_exc=RuntimeError("auth failed"))
    app.dependency_overrides[get_session] = _session_with([])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/markets/attention")

    assert resp.status_code == 502


async def test_attention_live_caches_within_ttl():
    rows = [{"stk_nm": "삼성전자", "bigd_rank": "1", "stk_cd": "005930", "base_comp_chgr": "-9.30"}]
    fake_cls = _make_fake_kiwoom_client(item_inq_rank=rows)
    markets.KiwoomClient = fake_cls

    stock_rows = [("005930", "삼성전자", "KOSPI", False)]
    # 두 번째 요청이 캐시를 맞으면 세션 큐를 두 번째로 소비하지 않는다 — 첫 요청 분만 준비.
    app.dependency_overrides[get_session] = _session_with([_FakeRowsResult(stock_rows)])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/api/markets/attention")
        r2 = await client.get("/api/markets/attention")

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["queried_at"] == r2.json()["queried_at"]
    assert fake_cls.call_count == 1
