"""Thin wiring test for PLAN.md §5.4-2/§5.14: collectors/live_refresh.py's jobs
must pass the warm functions' already-fetched return value straight into the
matching collectors.intraday_snapshot recorder, with no extra transformation.

Not a full behavioral test of intraday_snapshot itself (see
test_intraday_snapshot.py for that) — just proof of the wiring, so a future
refactor that accidentally drops the recorder call or feeds it the wrong
object gets caught. Uses a real DB session via app.db.async_session_factory
for _run_live_refresh (same house pattern as test_basis_router.py etc.) since
markets._warm_breadth_live/_warm_flow_live are monkeypatched away entirely —
the session is opened/closed but never queried by those.

§5.14 changed the three record_* functions from sync in-memory appenders to
`async def record_x(session, payload)` DB writers — this file's assertions
now capture ``(session, payload)`` tuples instead of bare payloads, and the
fake recorders must be async (live_refresh awaits them).
"""

from __future__ import annotations

import pytest

from app.collectors import auto_trader, intraday_snapshot, live_refresh, positioning_snapshot, scalp_tracker
from app.routers import markets

FLOW_PAYLOAD = {"kospi": None, "kosdaq": None, "market_closed": False, "cached_at": "x"}
FUTURES_PAYLOAD = {"date": "2026-07-21", "investors": {}, "market_closed": False, "cached_at": "x"}
BREADTH_PAYLOAD = {"kospi": None, "kosdaq": None, "market_closed": False, "cached_at": "x"}


@pytest.fixture(autouse=True)
def _force_market_open(monkeypatch):
    monkeypatch.setattr(live_refresh, "is_nxt_closed", lambda now_kst: False)
    # §5.7 scalp-tracker는 이 파일이 다루는 "워밍 결과가 intraday_snapshot recorder로
    # 그대로 흘러가는지"와 무관하다 — 실제 scalp_tracker 동작은
    # test_scalp_tracker.py가 전담하므로, 여기서는 no-op으로 막아 이 파일의
    # 단언들이 scalp-tracker의 부수효과(추가 DB 쿼리 등)에 영향받지 않게 한다.
    monkeypatch.setattr(scalp_tracker, "track_scalp_picks", _async_return_dict)
    # PLAN.md §5.52 — positioning-snapshot도 §5.7 scalp-tracker와 완전히 같은
    # 이유로 no-op 처리한다: 실제 동작(스냅샷 기록/same_day/next_day 채우기)은
    # test_positioning_snapshot.py가 전담하고, 이 파일은 "워밍 결과가
    # intraday_snapshot recorder로 그대로 흘러가는지"만 본다 — 막지 않으면
    # positioning_snapshot이 markets._warm_regime 등을 몰래 실호출/실쿼리해
    # 이 파일의 단언과 무관한 부수효과가 섞인다.
    monkeypatch.setattr(
        positioning_snapshot, "track_positioning_snapshot", _async_return_positioning_dict
    )


async def _async_return_dict(*_args, **_kwargs):
    return {"entries": 0, "horizons": 0, "eod": 0}


async def _async_return_positioning_dict(*_args, **_kwargs):
    return {"created": False, "same_day_filled": False, "next_day_filled_count": 0}


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

    async def _fake_record_flow(session, payload):
        recorded.append(payload)

    monkeypatch.setattr(intraday_snapshot, "record_flow_snapshot", _fake_record_flow)

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

    async def _fake_record_futures(session, payload):
        recorded.append(payload)

    monkeypatch.setattr(intraday_snapshot, "record_futures_flow_snapshot", _fake_record_futures)

    await live_refresh._run_live_refresh()

    assert recorded == [FUTURES_PAYLOAD]


async def test_run_live_refresh_feeds_breadth_payload_into_recorder(monkeypatch):
    """PLAN.md §5.13 — breadth/live 워밍 직후 그 반환값이 그대로
    record_breadth_snapshot에 전달돼야 한다(등락비율 1D 누적 차트의 배선)."""
    recorded = []
    from app.routers import basis as basis_router
    from app.routers import groups as groups_router

    monkeypatch.setattr(markets, "_warm_breadth_live", lambda session: _async_return(BREADTH_PAYLOAD))
    monkeypatch.setattr(markets, "_warm_flow_live", lambda session: _async_return(None))
    monkeypatch.setattr(markets, "_warm_attention", lambda session: _async_return(None))
    monkeypatch.setattr(markets, "_warm_index_tiles_live", lambda session: _async_return(None))
    monkeypatch.setattr(markets, "_warm_fx_live", lambda session: _async_return(None))
    monkeypatch.setattr(basis_router, "_warm_basis_live", lambda: _async_return(None))
    monkeypatch.setattr(groups_router, "_warm_groups_live", lambda group_type: _async_return(None))
    monkeypatch.setattr(markets, "_warm_futures_flow_live", lambda: _async_return(FUTURES_PAYLOAD))

    async def _fake_record_breadth(session, payload):
        recorded.append(payload)

    monkeypatch.setattr(intraday_snapshot, "record_breadth_snapshot", _fake_record_breadth)

    await live_refresh._run_live_refresh()

    assert recorded == [BREADTH_PAYLOAD]


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


async def test_run_live_refresh_skips_auto_trader_when_nxt_closed(monkeypatch):
    """PLAN.md §5.59(2026-08-07) — `run_auto_trade`는 `_warm_stock_intraday`로
    매번 실제 키움 ka10080을 호출한다(scalp-tracker/positioning-snapshot과
    달리 "새 외부 호출 없음"이 아니다). 킬스위치가 켜져 있으면 NXT 마감
    (20:00 KST)~다음날 개장(08:00 KST) 사이에도 60초마다 키움을 계속
    두드리고 있던 버그를 고쳤다 — 이제 `watch_stop_loss`(30초 잡)와 동일하게
    `is_nxt_closed`로 게이트된다."""
    monkeypatch.setattr(live_refresh, "is_nxt_closed", lambda now_kst: True)
    monkeypatch.setattr(markets, "_warm_breadth_live", lambda session: _async_return(None))
    monkeypatch.setattr(markets, "_warm_flow_live", lambda session: _async_return(None))
    monkeypatch.setattr(markets, "_warm_attention", lambda session: _async_return(None))
    monkeypatch.setattr(markets, "_warm_index_tiles_live", lambda session: _async_return(None))
    monkeypatch.setattr(markets, "_warm_fx_live", lambda session: _async_return(None))

    called = {"run_auto_trade": False}

    async def _fake_run_auto_trade(session, now_kst=None):  # pragma: no cover - 호출되면 안 됨
        called["run_auto_trade"] = True
        return {"enabled": False, "action": "none"}

    monkeypatch.setattr(auto_trader, "run_auto_trade", _fake_run_auto_trade)

    await live_refresh._run_live_refresh()

    assert called["run_auto_trade"] is False


async def test_run_live_refresh_calls_auto_trader_when_nxt_open(monkeypatch):
    """위 테스트의 대조군 — NXT 개장 중(이 파일의 기본 `_force_market_open`
    상태)에는 여전히 매 폴링 호출돼야 한다(킬스위치 자체 게이트는
    test_auto_trader_collector.py가 전담)."""
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

    called = {"run_auto_trade": False}

    async def _fake_run_auto_trade(session, now_kst=None):
        called["run_auto_trade"] = True
        return {"enabled": False, "action": "none"}

    monkeypatch.setattr(auto_trader, "run_auto_trade", _fake_run_auto_trade)

    await live_refresh._run_live_refresh()

    assert called["run_auto_trade"] is True


async def test_nasdaq_futures_morning_job_calls_fetch_and_cache_not_warm(monkeypatch):
    """PLAN.md §5.56(2026-08-06) — 07:50 KST 아침 크론은 실제로 조회하는
    `_fetch_and_cache_nasdaq_futures_live`를 불러야 한다. 캐시만 읽는
    `_warm_nasdaq_futures_live`를 잘못 불렀다면(회귀) 그날 나스닥 캐시가
    영영 채워지지 않는다 — 이 잡이 하루 중 실제 조회를 하는 유일한 경로이기
    때문이다."""
    called = {"fetch": False}

    async def _fake_fetch():
        called["fetch"] = True
        return {"symbol": "NQ=F", "bars": [], "latest_change_pct": None, "cached_at": "x"}

    def _warm_should_not_be_called():  # pragma: no cover - 호출되면 안 됨
        raise AssertionError("아침 크론이 캐시-읽기 전용 _warm_nasdaq_futures_live를 호출했다")

    monkeypatch.setattr(markets, "_fetch_and_cache_nasdaq_futures_live", _fake_fetch)
    monkeypatch.setattr(markets, "_warm_nasdaq_futures_live", _warm_should_not_be_called)

    await live_refresh._run_nasdaq_futures_morning_job()

    assert called["fetch"] is True


async def test_nasdaq_futures_morning_job_swallows_fetch_failure(monkeypatch):
    """실패해도 스케줄러 전체를 죽이지 않는다(2026-08-06부터 온디맨드 폴백이
    없어졌으므로 — 이 잡 실패 시 그날은 그냥 나스닥 데이터 없이 넘어간다,
    모듈 docstring 참고)."""

    async def _raise():
        raise RuntimeError("yfinance boom")

    monkeypatch.setattr(markets, "_fetch_and_cache_nasdaq_futures_live", _raise)

    await live_refresh._run_nasdaq_futures_morning_job()  # 예외가 새 나오지 않아야 함
