"""Unit tests for GET /api/markets/regime (app.routers.markets, PLAN.md §5.15
"검증 기반 시장 우세 판정").

Uses httpx.AsyncClient + ASGITransport against the real FastAPI app. The DB
session is never actually queried by the router itself (all reads happen
inside app.quant.regime_backtest, which this file monkeypatches with canned
per-(market, investor) results) — same no-DB/no-network philosophy as
test_markets_flow_live_router.py, but one level higher: we fake the
regime_backtest functions instead of faking SQL results, since the router's
own logic (judgement + live overlay) is what's under test here, not the
backtest math (covered separately by tests/test_regime_backtest.py).

The central thing every test in this file protects: **the verdict is decided
solely by the kosdaq/외국인 combo** — kospi's own streak, however bullish,
must never produce "코스피우세" (PLAN.md §5.15 explicit principle — "코스피는
신호가 약하다는 걸 감추지 않는다, 억지로 판정하지 않는다").
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import get_session
from app.main import app
from app.quant import flow_acceleration, regime_backtest
from app.routers import markets


class _FakeSession:
    """regime router는 session을 직접 쿼리하지 않고 전부 regime_backtest쪽
    monkeypatch로 가로채므로, 세션 자체는 어떤 메서드도 실제로 호출되지 않는
    더미 객체면 충분하다."""


@pytest.fixture(autouse=True)
def _reset_regime_cache():
    markets._regime_cache["data"] = None
    markets._regime_cache["ts"] = 0.0
    yield
    markets._regime_cache["data"] = None
    markets._regime_cache["ts"] = 0.0


@pytest.fixture(autouse=True)
def _clear_overrides():
    async def fake_get_session():
        yield _FakeSession()

    app.dependency_overrides[get_session] = fake_get_session
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _default_flow_acceleration_none(monkeypatch):
    """PLAN.md §5.17 — _compute_regime_combo가 세션으로 flow_acceleration을
    직접 조회한다. 이 파일의 세션은 진짜 쿼리를 실행할 수 없는 ``_FakeSession``
    더미이므로(다른 라우터 로직은 전부 regime_backtest monkeypatch로 가로챔),
    기본값으로 None(데이터 부족)을 돌려주는 가짜 함수로 항상 교체해 둔다 —
    가속도 값 자체를 검증하는 테스트는 이 fixture 이후 다시 monkeypatch해서
    override한다."""

    async def fake_compute_flow_acceleration(session, series_key, now, window_minutes=30):
        return None

    monkeypatch.setattr(flow_acceleration, "compute_flow_acceleration", fake_compute_flow_acceleration)


def _bucket(label, n, avg, pos):
    return {"bucket": label, "n": n, "avg_return_pct": avg, "positive_rate_pct": pos}


def _all_buckets(filled: dict[str, dict]) -> list[dict]:
    """BUCKET_ORDER 순서로 채운다 — 지정 안 된 라벨은 표본 없음(n=0)."""
    empty = {"n": 0, "avg_return_pct": None, "positive_rate_pct": None}
    return [{"bucket": label, **filled.get(label, empty)} for label in regime_backtest.BUCKET_ORDER]


def _patch_backtest(monkeypatch, *, streaks: dict[tuple[str, str], int], buckets: dict[tuple[str, str], list[dict]]):
    """(market, investor) -> confirmed_streak / bucket 리스트를 그대로 돌려주는
    가짜 regime_backtest 함수로 교체한다. compute_baseline은 이 라우터 테스트의
    관심사가 아니라(판정에 안 쓰임, 응답에 참고용으로만 실림) 고정값 하나로
    충분하다. 호출 인자를 calls 리스트에 기록해 캐싱 테스트에서 재사용한다."""
    calls: list[tuple[str, str, str]] = []

    async def fake_current_streak(session, market, investor):
        calls.append(("current_streak", market, investor))
        return streaks.get((market, investor), 0)

    async def fake_streak_buckets(session, market, investor):
        calls.append(("streak_buckets", market, investor))
        return buckets.get((market, investor), _all_buckets({}))

    async def fake_baseline(session, market):
        calls.append(("baseline", market, ""))
        return {"n": 100, "avg_return_pct": 0.01, "positive_rate_pct": 50.0}

    monkeypatch.setattr(regime_backtest, "compute_current_streak", fake_current_streak)
    monkeypatch.setattr(regime_backtest, "compute_streak_buckets", fake_streak_buckets)
    monkeypatch.setattr(regime_backtest, "compute_baseline", fake_baseline)
    return calls


def _patch_flow_live(monkeypatch, payload):
    async def fake_warm_flow_live(session):
        return payload

    monkeypatch.setattr(markets, "_warm_flow_live", fake_warm_flow_live)


CLOSED_FLOW_LIVE = {"kospi": None, "kosdaq": None, "market_closed": True}


async def _get_regime():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get("/api/markets/regime")


# ---------------------------------------------------------------------------
# 종합 판정 — 코스닥·외국인만 근거
# ---------------------------------------------------------------------------


async def test_kosdaq_foreign_buy_streak_yields_kosdaq_advantage(monkeypatch):
    _patch_backtest(
        monkeypatch,
        streaks={("kosdaq", "외국인"): 3, ("kosdaq", "기관계"): 1, ("kospi", "외국인"): 1, ("kospi", "기관계"): 1},
        buckets={("kosdaq", "외국인"): _all_buckets({"3+매수": _bucket("3+매수", 69, 0.522, 65.2)})},
    )
    _patch_flow_live(monkeypatch, CLOSED_FLOW_LIVE)

    resp = await _get_regime()
    assert resp.status_code == 200
    body = resp.json()

    assert body["regime"] == "코스닥우세"
    assert "65.2%" in body["reason"]
    assert "69일" in body["reason"]
    assert body["reliable_signal"] == "kosdaq_foreign"
    assert body["kosdaq"]["외국인"]["reliable"] is True
    assert body["kosdaq"]["기관계"]["reliable"] is False
    assert body["kospi"]["외국인"]["reliable"] is False
    assert body["kospi"]["기관계"]["reliable"] is False

    # PLAN.md §5.17 — 4개 조합 전부에 acceleration 필드가 존재해야 한다(기본
    # fixture는 데이터 부족 None을 돌려주지만, 필드 자체는 항상 있어야 함).
    for market in ("kospi", "kosdaq"):
        for investor in ("외국인", "기관계"):
            assert "acceleration" in body[market][investor]
            assert body[market][investor]["acceleration"] is None


async def test_kospi_streak_never_yields_kospi_advantage_even_when_bullish(monkeypatch):
    """kospi 두 조합 모두 극단적으로 강세인 버킷 통계를 줘도(가짜 데이터),
    코스닥·외국인이 약한/매도 스트릭이면 절대 "코스피우세"가 나오면 안 된다."""
    _patch_backtest(
        monkeypatch,
        streaks={
            ("kosdaq", "외국인"): -3,  # 코스닥 외국인 3일+ 연속 매도 -> 중립(코스닥 불리, 코스피 유리 아님)
            ("kosdaq", "기관계"): 5,
            ("kospi", "외국인"): 10,
            ("kospi", "기관계"): 10,
        },
        buckets={
            ("kosdaq", "외국인"): _all_buckets({"3+매도": _bucket("3+매도", 98, -0.9, 20.0)}),
            ("kosdaq", "기관계"): _all_buckets({"3+매수": _bucket("3+매수", 50, 5.0, 95.0)}),
            ("kospi", "외국인"): _all_buckets({"3+매수": _bucket("3+매수", 50, 5.0, 99.0)}),
            ("kospi", "기관계"): _all_buckets({"3+매수": _bucket("3+매수", 50, 5.0, 99.0)}),
        },
    )
    _patch_flow_live(monkeypatch, CLOSED_FLOW_LIVE)

    resp = await _get_regime()
    body = resp.json()

    assert body["regime"] != "코스피우세"
    assert body["regime"] == "중립"
    assert "코스닥" in body["reason"]
    assert "코스피" not in body["reason"] or "코스피가 유리하다는 뜻은 아님" in body["reason"]


async def test_kosdaq_foreign_short_streak_is_neutral(monkeypatch):
    _patch_backtest(
        monkeypatch,
        streaks={("kosdaq", "외국인"): 1, ("kosdaq", "기관계"): 0, ("kospi", "외국인"): 0, ("kospi", "기관계"): 0},
        buckets={("kosdaq", "외국인"): _all_buckets({"1매수": _bucket("1매수", 182, 0.225, 59.3)})},
    )
    _patch_flow_live(monkeypatch, CLOSED_FLOW_LIVE)

    resp = await _get_regime()
    body = resp.json()
    assert body["regime"] == "중립"
    assert "표본이 짧아" in body["reason"]


async def test_kosdaq_foreign_no_streak_is_neutral(monkeypatch):
    _patch_backtest(
        monkeypatch,
        streaks={("kosdaq", "외국인"): 0, ("kosdaq", "기관계"): 0, ("kospi", "외국인"): 0, ("kospi", "기관계"): 0},
        buckets={},
    )
    _patch_flow_live(monkeypatch, CLOSED_FLOW_LIVE)

    resp = await _get_regime()
    body = resp.json()
    assert body["regime"] == "중립"
    assert body["kosdaq"]["외국인"]["bucket"] is None


# ---------------------------------------------------------------------------
# 오늘의 라이브 반영
# ---------------------------------------------------------------------------


async def test_live_overlay_applied_when_direction_matches(monkeypatch):
    _patch_backtest(
        monkeypatch,
        streaks={("kosdaq", "외국인"): 2, ("kosdaq", "기관계"): 0, ("kospi", "외국인"): 0, ("kospi", "기관계"): 0},
        buckets={("kosdaq", "외국인"): _all_buckets({"3+매수": _bucket("3+매수", 69, 0.522, 65.2)})},
    )
    _patch_flow_live(
        monkeypatch,
        {
            "kospi": None,
            "kosdaq": {"date": "2026-07-23", "investors": {"외국인": {"net_value": 500}}, "provisional": True},
            "market_closed": False,
        },
    )

    resp = await _get_regime()
    body = resp.json()
    kosdaq_foreign = body["kosdaq"]["외국인"]
    assert kosdaq_foreign["confirmed_streak"] == 2
    assert kosdaq_foreign["streak"] == 3  # 같은 방향(+) -> +1
    assert kosdaq_foreign["live_applied"] is True
    assert kosdaq_foreign["bucket"] == "3+매수"
    assert body["regime"] == "코스닥우세"


async def test_live_overlay_skipped_when_direction_opposite(monkeypatch):
    _patch_backtest(
        monkeypatch,
        streaks={("kosdaq", "외국인"): 2, ("kosdaq", "기관계"): 0, ("kospi", "외국인"): 0, ("kospi", "기관계"): 0},
        buckets={("kosdaq", "외국인"): _all_buckets({"2매수": _bucket("2매수", 82, -0.135, 50.0)})},
    )
    _patch_flow_live(
        monkeypatch,
        {
            "kospi": None,
            "kosdaq": {"date": "2026-07-23", "investors": {"외국인": {"net_value": -500}}, "provisional": True},
            "market_closed": False,
        },
    )

    resp = await _get_regime()
    kosdaq_foreign = resp.json()["kosdaq"]["외국인"]
    assert kosdaq_foreign["confirmed_streak"] == 2
    assert kosdaq_foreign["streak"] == 2  # 반대 방향 -> 확정 스트릭 그대로
    assert kosdaq_foreign["live_applied"] is False


async def test_live_overlay_skipped_when_market_closed_even_if_payload_present(monkeypatch):
    """provisional=False(마감 후 DB 확정치 폴백)이면 confirmed_streak 계산에
    이미 그 데이터가 포함돼 있으므로 다시 반영하면 이중 계산이 된다 — 라우터가
    provisional 플래그를 확인해 건너뛰는지 검증."""
    _patch_backtest(
        monkeypatch,
        streaks={("kosdaq", "외국인"): 2, ("kosdaq", "기관계"): 0, ("kospi", "외국인"): 0, ("kospi", "기관계"): 0},
        buckets={("kosdaq", "외국인"): _all_buckets({"2매수": _bucket("2매수", 82, -0.135, 50.0)})},
    )
    _patch_flow_live(
        monkeypatch,
        {
            "kospi": None,
            "kosdaq": {
                "date": "2026-07-22",
                "investors": {"외국인": {"net_value": 500}},
                "provisional": False,
                "source": "market_flow_db",
            },
            "market_closed": True,
        },
    )

    resp = await _get_regime()
    body = resp.json()
    assert body["market_closed"] is True
    kosdaq_foreign = body["kosdaq"]["외국인"]
    assert kosdaq_foreign["streak"] == 2
    assert kosdaq_foreign["live_applied"] is False


# ---------------------------------------------------------------------------
# 캐싱
# ---------------------------------------------------------------------------


async def test_regime_caches_within_ttl(monkeypatch):
    calls = _patch_backtest(
        monkeypatch,
        streaks={("kosdaq", "외국인"): 3, ("kosdaq", "기관계"): 0, ("kospi", "외국인"): 0, ("kospi", "기관계"): 0},
        buckets={("kosdaq", "외국인"): _all_buckets({"3+매수": _bucket("3+매수", 69, 0.522, 65.2)})},
    )
    _patch_flow_live(monkeypatch, CLOSED_FLOW_LIVE)

    r1 = await _get_regime()
    calls_after_first = len(calls)
    r2 = await _get_regime()

    assert r1.json()["cached_at"] == r2.json()["cached_at"]
    assert len(calls) == calls_after_first  # 두 번째 호출은 캐시만 읽고 재계산 없음


async def test_regime_falls_back_when_flow_live_raises(monkeypatch):
    """flow/live 조회가 실패해도(예: 키움 인증 오류) 확정 스트릭만으로 판정은
    계속 응답해야 한다(502로 죽지 않음) — 다른 라이브 엔드포인트와 달리 이
    엔드포인트에게 라이브 오버레이는 "있으면 좋은" 보강 정보일 뿐이다."""
    _patch_backtest(
        monkeypatch,
        streaks={("kosdaq", "외국인"): 3, ("kosdaq", "기관계"): 0, ("kospi", "외국인"): 0, ("kospi", "기관계"): 0},
        buckets={("kosdaq", "외국인"): _all_buckets({"3+매수": _bucket("3+매수", 69, 0.522, 65.2)})},
    )

    async def fake_warm_flow_live_raises(session):
        raise RuntimeError("kiwoom auth failed")

    monkeypatch.setattr(markets, "_warm_flow_live", fake_warm_flow_live_raises)

    resp = await _get_regime()
    assert resp.status_code == 200
    body = resp.json()
    assert body["regime"] == "코스닥우세"
    assert body["kosdaq"]["외국인"]["live_applied"] is False


# ---------------------------------------------------------------------------
# 가속도 (PLAN.md §5.17) — 스트릭과 별도 필드, _judge_regime에 섞이지 않음
# ---------------------------------------------------------------------------


async def test_acceleration_is_wired_per_combo_with_correct_series_key(monkeypatch):
    """각 콤보가 자신의 series_key(``flow_{market}_{investor}``)로 조회한
    가속도 결과를 그대로 노출하는지 확인 — 4개 조합이 서로 다른 값을 받아도
    섞이지 않아야 한다."""
    _patch_backtest(
        monkeypatch,
        streaks={("kosdaq", "외국인"): 3, ("kosdaq", "기관계"): 0, ("kospi", "외국인"): 0, ("kospi", "기관계"): 0},
        buckets={("kosdaq", "외국인"): _all_buckets({"3+매수": _bucket("3+매수", 69, 0.522, 65.2)})},
    )
    _patch_flow_live(monkeypatch, CLOSED_FLOW_LIVE)

    results = {
        ("kosdaq", "외국인"): {
            "window_minutes": 30,
            "recent_velocity": 1200.0,
            "prior_velocity": 300.0,
            "acceleration": 900.0,
        },
        ("kosdaq", "기관계"): {
            "window_minutes": 30,
            "recent_velocity": -100.0,
            "prior_velocity": 200.0,
            "acceleration": -300.0,
        },
        # kospi 두 조합은 patch하지 않음 -> None(데이터 부족) 그대로 유지되는지 확인.
    }

    async def fake_compute_flow_acceleration(session, series_key, now, window_minutes=30):
        prefix = "flow_"
        market, investor = series_key[len(prefix) :].split("_", 1)
        return results.get((market, investor))

    monkeypatch.setattr(flow_acceleration, "compute_flow_acceleration", fake_compute_flow_acceleration)

    resp = await _get_regime()
    body = resp.json()

    assert body["kosdaq"]["외국인"]["acceleration"] == results[("kosdaq", "외국인")]
    assert body["kosdaq"]["기관계"]["acceleration"] == results[("kosdaq", "기관계")]
    assert body["kospi"]["외국인"]["acceleration"] is None
    assert body["kospi"]["기관계"]["acceleration"] is None

    # 종합 판정(regime/reason)에는 가속도가 섞이지 않는다 — 여전히 스트릭 기반.
    assert body["regime"] == "코스닥우세"
    assert "가속" not in body["reason"] and "감속" not in body["reason"]
