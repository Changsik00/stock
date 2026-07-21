"""Thin wiring test for PLAN.md §5.4-2: collectors/live_refresh.py's two jobs
must pass the warm functions' already-fetched return value straight into the
matching collectors.intraday_snapshot recorder, with no extra transformation.

Not a full behavioral test of intraday_snapshot itself (see
test_intraday_snapshot.py for that) — just proof of the wiring, so a future
refactor that accidentally drops the recorder call or feeds it the wrong
object gets caught. Uses a real DB session via app.db.async_session_factory
for _run_live_refresh (same house pattern as test_basis_router.py etc.) since
markets._warm_breadth_live/_warm_flow_live are monkeypatched away entirely —
the session is opened/closed but never queried.
"""

from __future__ import annotations

import pytest

from app.collectors import intraday_snapshot, live_refresh
from app.routers import markets

FLOW_PAYLOAD = {"kospi": None, "kosdaq": None, "market_closed": False, "cached_at": "x"}
FUTURES_PAYLOAD = {"date": "2026-07-21", "investors": {}, "market_closed": False, "cached_at": "x"}


@pytest.fixture(autouse=True)
def _force_market_open(monkeypatch):
    monkeypatch.setattr(live_refresh, "is_nxt_closed", lambda now_kst: False)


async def test_run_live_refresh_feeds_flow_payload_into_recorder(monkeypatch):
    recorded = []
    from app.routers import basis as basis_router
    from app.routers import groups as groups_router

    monkeypatch.setattr(markets, "_warm_breadth_live", lambda session: _async_return(None))
    monkeypatch.setattr(markets, "_warm_flow_live", lambda session: _async_return(FLOW_PAYLOAD))
    monkeypatch.setattr(markets, "_warm_attention", lambda session: _async_return(None))
    monkeypatch.setattr(markets, "_warm_index_tiles_live", lambda session: _async_return(None))
    monkeypatch.setattr(markets, "_warm_fx_live", lambda session: _async_return(None))
    monkeypatch.setattr(basis_router, "_warm_basis_live", lambda: _async_return(None))
    monkeypatch.setattr(groups_router, "_warm_groups_live", lambda group_type: _async_return(None))
    monkeypatch.setattr(markets, "_warm_futures_flow_live", lambda: _async_return(FUTURES_PAYLOAD))
    monkeypatch.setattr(intraday_snapshot, "record_flow_snapshot", lambda payload: recorded.append(payload))

    await live_refresh._run_live_refresh()

    assert recorded == [FLOW_PAYLOAD]


async def test_run_live_refresh_feeds_futures_flow_payload_into_recorder(monkeypatch):
    """§5.6 회귀 수정: futures-flow/live가 60초 잡(_run_live_refresh)으로 옮겨왔다 —
    예전엔 이 배선이 7분 잡(_run_live_refresh_extra)에 있었다."""
    recorded = []
    from app.routers import basis as basis_router
    from app.routers import groups as groups_router

    monkeypatch.setattr(markets, "_warm_breadth_live", lambda session: _async_return(None))
    monkeypatch.setattr(markets, "_warm_flow_live", lambda session: _async_return(None))
    monkeypatch.setattr(markets, "_warm_attention", lambda session: _async_return(None))
    monkeypatch.setattr(markets, "_warm_index_tiles_live", lambda session: _async_return(None))
    monkeypatch.setattr(markets, "_warm_fx_live", lambda session: _async_return(None))
    monkeypatch.setattr(basis_router, "_warm_basis_live", lambda: _async_return(None))
    monkeypatch.setattr(groups_router, "_warm_groups_live", lambda group_type: _async_return(None))
    monkeypatch.setattr(markets, "_warm_futures_flow_live", lambda: _async_return(FUTURES_PAYLOAD))
    monkeypatch.setattr(
        intraday_snapshot, "record_futures_flow_snapshot", lambda payload: recorded.append(payload)
    )

    await live_refresh._run_live_refresh()

    assert recorded == [FUTURES_PAYLOAD]


async def test_run_live_refresh_extra_only_warms_value_rank(monkeypatch):
    """§5.6 회귀 수정: 7분 잡은 이제 value-rank/live 하나만 채운다 — basis/groups/
    futures-flow는 위 60초 잡으로 옮겼으므로 이 잡에서 호출되면 안 된다."""
    from app.routers import basis as basis_router
    from app.routers import flow_rank as flow_rank_router
    from app.routers import groups as groups_router

    called = {"value_rank": False}

    def _mark_and_return(value):
        called["value_rank"] = True
        return _async_return(value)

    def _fail(*_args, **_kwargs):
        raise AssertionError("7분 잡에서 호출되면 안 되는 warm 함수가 호출됐다")

    monkeypatch.setattr(flow_rank_router, "_warm_value_rank_live", lambda: _mark_and_return(None))
    monkeypatch.setattr(basis_router, "_warm_basis_live", _fail)
    monkeypatch.setattr(groups_router, "_warm_groups_live", _fail)
    monkeypatch.setattr(markets, "_warm_futures_flow_live", _fail)

    await live_refresh._run_live_refresh_extra()

    assert called["value_rank"] is True


async def _async_return(value):
    return value
