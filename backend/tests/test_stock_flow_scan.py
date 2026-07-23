"""Tests for collectors.live_refresh._run_stock_flow_scan (PLAN.md §5.20-1).

The scan sweeps the value-rank/live candidate universe through the existing
per-stock ka10059 TR (routers.stocks._parse_ka10059_rows/_upsert_flow_rows,
reused as-is) and upserts stock_flow rows. Real dev Postgres via
app.db.async_session_factory (same house pattern as test_scalp_tracker.py) —
test codes ("999701"/"999702") don't collide with real KRX codes, seeded/torn
down per test like test_stocks_router.py's seeded_stock fixture.

The two external collaborators are monkeypatched, never real network:
- routers.flow_rank._warm_value_rank_live -> fixed candidate payload.
- clients.kiwoom.KiwoomClient -> a fake that (a) returns canned ka10059
  bodies and (b) counts how many times it's constructed, since the whole
  point of PLAN.md §5.20-1's design is "exactly one KiwoomClient instance for
  the entire sweep" (the rate limiter's token bucket is a per-instance
  attribute — a fresh client per code would reset it and could burst past
  1 req/s against the real Kiwoom server).
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import delete, select

from app.clients import kiwoom as kiwoom_module
from app.collectors import live_refresh
from app.db import async_session_factory, engine
from app.market_hours import KST
from app.models import Stock, StockFlow
from app.routers import flow_rank as flow_rank_router

TEST_CODE_A = "999701"
TEST_CODE_B = "999702"
TEST_CODE_ETF = "999703"  # value-rank 후보에 있지만 is_etf=True -> 스윕 대상에서 제외돼야 함
TEST_CODES = [TEST_CODE_A, TEST_CODE_B]


@pytest.fixture(autouse=True)
async def _dispose_engine_per_test():
    """app.db.engine이 이벤트 루프에 바인딩된 모듈 전역 싱글턴이라(pytest-asyncio가
    테스트마다 새 루프를 준다) 매 테스트 뒤 dispose — test_stocks_router.py와 동일한
    안전장치."""
    yield
    await engine.dispose()


async def _clear_test_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(delete(StockFlow).where(StockFlow.code.in_(TEST_CODES)))
        await session.execute(delete(Stock).where(Stock.code.in_(TEST_CODES)))
        await session.commit()


@pytest.fixture
async def seeded_stocks():
    await _clear_test_rows()
    async with async_session_factory() as session:
        session.add(Stock(code=TEST_CODE_A, name="테스트수급A", market="KOSPI", is_etf=False))
        session.add(Stock(code=TEST_CODE_B, name="테스트수급B", market="KOSDAQ", is_etf=False))
        await session.commit()
    yield
    await _clear_test_rows()


def _fake_value_rank_payload() -> dict:
    return {
        "date": "2026-07-23",
        "market_closed": False,
        "cached_at": "2026-07-23T01:00:00+00:00",
        "rows": [
            {"code": TEST_CODE_A, "name": "테스트수급A", "market": "kospi", "is_etf": False, "rank": 1},
            {"code": TEST_CODE_B, "name": "테스트수급B", "market": "kosdaq", "is_etf": False, "rank": 2},
            # ETF는 §5.2 "ETF는 제외" 원칙대로 스윕 대상에서 빠져야 한다 — Stock 테이블에
            # 이 코드를 일부러 안 심어 둬서, 혹시 걸러지지 않고 upsert 시도가 되면 FK
            # 위반으로 즉시 드러나게 해 둔다(이중 안전장치).
            {"code": TEST_CODE_ETF, "name": "테스트ETF", "market": "kospi", "is_etf": True, "rank": 3},
        ],
    }


async def _fake_warm_value_rank_live() -> dict:
    return _fake_value_rank_payload()


def _fake_ka10059_response(net_frgnr: int, net_orgn: int) -> dict:
    today = dt.datetime.now(KST).date()
    return {
        "return_code": 0,
        "return_msg": "",
        "stk_invsr_orgn": [
            {
                "dt": today.strftime("%Y%m%d"),
                "ind_invsr": "0",
                "frgnr_invsr": str(net_frgnr),
                "orgn": str(net_orgn),
                "fnnc_invt": "0",
                "insrnc": "0",
                "invtrt": "0",
                "etc_fnnc": "0",
                "bank": "0",
                "penfnd_etc": "0",
                "samo_fund": "0",
                "natn": "0",
                "etc_corp": "0",
                "natfor": "0",
            }
        ],
    }


class _FakeKiwoomClient:
    """__init__ 호출 횟수를 클래스 변수에 기록 — "스윕 전체에 인스턴스가 정확히
    하나만 생성됐는지"를 검증하는 게 이 테스트 파일의 핵심 단언이다."""

    instance_count = 0
    calls: list[str] = []

    def __init__(self, *args, **kwargs) -> None:
        type(self).instance_count += 1

    async def __aenter__(self) -> "_FakeKiwoomClient":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def stock_investor_daily(self, code: str):
        type(self).calls.append(code)
        # 코드별로 다른 net_value를 줘서 나중에 stock_flow 행이 code마다 제대로
        # 갈렸는지(서로 뒤섞이지 않았는지) 구분할 수 있게 한다.
        net_frgnr = 1000 if code == TEST_CODE_A else 500
        net_orgn = 2000 if code == TEST_CODE_A else -300
        return (
            _fake_ka10059_response(net_frgnr, net_orgn),
            {"cont-yn": "N", "next-key": "", "api-id": "ka10059"},
        )


@pytest.fixture(autouse=True)
def _reset_fake_client_state():
    _FakeKiwoomClient.instance_count = 0
    _FakeKiwoomClient.calls = []
    yield


async def test_nxt_closed_gate_skips_everything(monkeypatch):
    monkeypatch.setattr(live_refresh, "is_nxt_closed", lambda now_kst: True)

    warm_calls = {"n": 0}

    async def _tracking_warm():
        warm_calls["n"] += 1
        return _fake_value_rank_payload()

    monkeypatch.setattr(flow_rank_router, "_warm_value_rank_live", _tracking_warm)
    monkeypatch.setattr(kiwoom_module, "KiwoomClient", _FakeKiwoomClient)

    await live_refresh._run_stock_flow_scan()

    assert warm_calls["n"] == 0
    assert _FakeKiwoomClient.instance_count == 0
    assert _FakeKiwoomClient.calls == []


async def test_scan_upserts_flow_rows_using_single_kiwoom_client(monkeypatch, seeded_stocks):
    monkeypatch.setattr(live_refresh, "is_nxt_closed", lambda now_kst: False)
    monkeypatch.setattr(flow_rank_router, "_warm_value_rank_live", _fake_warm_value_rank_live)
    monkeypatch.setattr(kiwoom_module, "KiwoomClient", _FakeKiwoomClient)

    await live_refresh._run_stock_flow_scan()

    # 핵심 단언: 후보가 2개(ETF 제외)인데도 KiwoomClient는 정확히 1번만 생성됐다.
    assert _FakeKiwoomClient.instance_count == 1
    # ETF는 애초에 조회 대상에서 빠져야 한다.
    assert set(_FakeKiwoomClient.calls) == {TEST_CODE_A, TEST_CODE_B}

    async with async_session_factory() as session:
        rows = (
            await session.execute(select(StockFlow).where(StockFlow.code.in_(TEST_CODES)))
        ).scalars().all()

    by_code_investor = {(r.code, r.investor): r.net_value for r in rows}
    assert by_code_investor[(TEST_CODE_A, "외국인")] == 1000
    assert by_code_investor[(TEST_CODE_A, "기관계")] == 2000
    assert by_code_investor[(TEST_CODE_B, "외국인")] == 500
    assert by_code_investor[(TEST_CODE_B, "기관계")] == -300


async def test_scan_continues_after_one_code_fails(monkeypatch, seeded_stocks):
    """PLAN.md §5.20-1 지시: 한 종목 실패가 나머지 스윕을 막으면 안 된다."""
    monkeypatch.setattr(live_refresh, "is_nxt_closed", lambda now_kst: False)
    monkeypatch.setattr(flow_rank_router, "_warm_value_rank_live", _fake_warm_value_rank_live)

    class _PartialFailKiwoomClient(_FakeKiwoomClient):
        async def stock_investor_daily(self, code: str):
            if code == TEST_CODE_A:
                type(self).calls.append(code)
                raise RuntimeError("일시적 조회 실패(테스트)")
            return await super().stock_investor_daily(code)

    monkeypatch.setattr(kiwoom_module, "KiwoomClient", _PartialFailKiwoomClient)

    await live_refresh._run_stock_flow_scan()

    assert _PartialFailKiwoomClient.instance_count == 1
    async with async_session_factory() as session:
        rows = (
            await session.execute(select(StockFlow).where(StockFlow.code == TEST_CODE_B))
        ).scalars().all()
    assert len(rows) > 0

    async with async_session_factory() as session:
        rows_a = (
            await session.execute(select(StockFlow).where(StockFlow.code == TEST_CODE_A))
        ).scalars().all()
    assert rows_a == []
