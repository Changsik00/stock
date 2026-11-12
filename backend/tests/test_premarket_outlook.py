"""프리마켓 예상 시작점(PLAN.md §5.107) — 순수 모듈 + API + 표본 외 로그 잡 단위테스트.

외부 호출(키움·yfinance)은 전부 mock. DB를 쓰는 테스트는 코드 `TST*`(실제 종목이 아님) 행을 만들고 바깥
트랜잭션을 **항상 롤백**해 실제 DB에 행이 남지 않게 한다(정리 조건에 `like`를 쓰지 않는다)."""

from __future__ import annotations

import datetime as dt
import math

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors import premarket_outlook_log as pol
from app.db import engine, get_session
from app.krx_calendar import is_krx_closed
from app.main import app
from app.models import KrxRegularClose, NxtIntradayBar, PremarketOutlookLog
from app.quant import premarket_outlook as po
from app.routers import premarket as router_mod

KST = po.KST


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_per_test():
    """테스트마다 이벤트 루프가 달라 커넥션 풀을 setup/teardown 양쪽에서 비운다(기존 테스트 관례)."""
    await engine.dispose()
    yield
    await engine.dispose()


def _k(y, mo, d, h=0, mi=0):
    return dt.datetime(y, mo, d, h, mi, tzinfo=KST)


# -- 합성 데이터 --------------------------------------------------------------------------

BETA, ALPHA = 0.5, 0.002  # 합성 갭 = ALPHA + BETA * x


def _open_days(start: dt.date, n: int) -> list[dt.date]:
    out, d = [], start
    while len(out) < n:
        if not is_krx_closed(d):
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def _x(i: int) -> float:
    return 0.01 * math.sin(i * 1.3) + 0.004 * math.cos(i * 0.7)


def _synthetic(n_days: int = 40, start: dt.date = dt.date(2026, 9, 1)):
    """NQ(날짜별 15:25 봉=100, 다음 개장 07:55 봉=100*exp(x)), 종가 상수, 시가 = 종가*exp(갭)."""
    days = _open_days(start, n_days)
    bars, closes, opens, xs = [], {}, {}, {}
    for i, d in enumerate(days):
        x = _x(i)
        xs[d] = x
        bars.append((_k(d.year, d.month, d.day, 15, 25), 100.0))
        bars.append((_k(d.year, d.month, d.day, 7, 55), 100.0 * math.exp(x)))
        closes[d] = 1_000_000
        opens[d] = 1_000_000 * math.exp(ALPHA + BETA * x)
    return days, po.NqSeries(bars), closes, opens, xs


# -- OLS / 예측 / weak / insufficient ---------------------------------------------------------


def test_fit_ols_exact_line():
    xs = [0.01 * i for i in range(-5, 6)]
    ys = [0.001 + 0.8 * x for x in xs]
    f = po.fit_ols(xs, ys)
    assert f.n == 11 and f.beta == pytest.approx(0.8) and f.alpha == pytest.approx(0.001)
    assert f.resid_std == pytest.approx(0, abs=1e-12) and f.resid_mad == pytest.approx(0, abs=1e-12)


def test_fit_ols_residual_stats_and_degenerate():
    f = po.fit_ols([0, 1, 2, 3, 4], [0, 1.1, 1.9, 3.2, 3.8])
    assert f.resid_std > 0 and f.resid_mad > 0
    assert po.fit_ols([1, 1, 1, 1], [1, 2, 3, 4]) is None  # x 분산 0
    assert po.fit_ols([1, 2], [1, 2]) is None  # 표본 2개


def test_predict_values():
    f = po.Fit(n=30, alpha=0.001, beta=1.0, resid_std=0.01, resid_mad=0.008)
    p = po.predict(f, 0.02, 100_000)
    g = 0.021
    assert p["expected_price"] == round(100_000 * math.exp(g))
    assert p["expected_gap_pct"] == pytest.approx((math.exp(g) - 1) * 100)
    assert p["expected_low_pct"] == pytest.approx((math.exp(g - 0.01) - 1) * 100)
    assert p["expected_high_pct"] == pytest.approx((math.exp(g + 0.01) - 1) * 100)
    assert isinstance(p["expected_price"], int)


def test_is_weak_threshold():
    assert po.is_weak(math.log(1.0049)) is True
    assert po.is_weak(math.log(0.9951)) is True
    assert po.is_weak(math.log(1.0051)) is False
    assert po.is_weak(math.log(0.99)) is False


def test_outlook_insufficient_and_ok_and_no_data():
    days, nq, closes, opens, xs = _synthetic(40)
    samples = po.build_samples(nq, closes, opens)
    target = days[-1]
    ok = po.outlook_for_stock(samples, target, 0.02, 1_000_000)
    assert ok["status"] == "ok" and ok["beta"] == pytest.approx(BETA, abs=0.01)
    # 대상일 이전 표본이 19개뿐이면 예측하지 않는다.
    few = po.outlook_for_stock(samples, samples[19].date, 0.02, 1_000_000)
    assert few["status"] == "insufficient" and few["n"] == 19
    at20 = po.outlook_for_stock(samples, samples[20].date, 0.02, 1_000_000)
    assert at20["status"] == "ok" and at20["n"] == 20
    assert po.outlook_for_stock(samples, target, None, 1_000_000)["status"] == "no_data"
    assert po.outlook_for_stock(samples, target, 0.01, None)["status"] == "no_data"


def test_window_uses_latest_60_only():
    days, nq, closes, opens, xs = _synthetic(80)
    samples = po.build_samples(nq, closes, opens)
    res = po.outlook_for_stock(samples, days[-1], 0.01, 1_000_000)
    assert res["n"] == po.WINDOW_DAYS


def test_no_future_leak_target_day_excluded_from_fit():
    days, nq, closes, opens, xs = _synthetic(40)
    samples = po.build_samples(nq, closes, opens)
    target = samples[30].date
    base = po.fit_for_target(samples, target)
    # 대상일(과 그 이후) 시가를 터무니없게 바꿔도 적합은 그대로여야 한다.
    tampered = [s if s.date < target else po.Sample(s.date, s.x, s.y + 0.5) for s in samples]
    again = po.fit_for_target(tampered, target)
    assert again == base
    assert base.n == 30  # 대상일 이전 30개만


def test_build_samples_skips_collection_gap():
    days, nq, closes, opens, xs = _synthetic(30)
    full = po.build_samples(nq, closes, opens)
    del closes[days[10]]  # 직전 거래일 종가가 빠지면 d=days[11]은 표본에서 빠진다
    gapped = po.build_samples(nq, closes, opens)
    dates = {s.date for s in gapped}
    assert days[11] not in dates and days[10] in dates  # days[10]은 days[9] 종가로 계산 가능
    assert len(gapped) == len(full) - 1


# -- NQ 이전 최근 봉 선택(정비 시간·주말) ----------------------------------------------------


def test_nq_picks_last_bar_at_or_before_cutoff_and_ignores_after():
    s = po.NqSeries([(_k(2026, 11, 10, 15, 20), 100.0), (_k(2026, 11, 10, 15, 25), 101.0), (_k(2026, 11, 10, 15, 30), 150.0)])
    t, v = s.last_at_or_before(po.nq_base_cutoff(dt.date(2026, 11, 10)))
    assert (t, v) == (_k(2026, 11, 10, 15, 25), 101.0)  # 15:30 봉은 쓰지 않는다


def test_nq_maintenance_window_uses_earlier_bar():
    # 겨울 정비 시간: KST 07:00~08:00 봉 없음 -> 07:55 이전 마지막은 06:55 봉
    s = po.NqSeries([(_k(2026, 11, 12, 6, 50), 1.0), (_k(2026, 11, 12, 6, 55), 2.0), (_k(2026, 11, 12, 8, 0), 3.0)])
    t, v = s.last_at_or_before(po.nq_now_cutoff(dt.date(2026, 11, 12)))
    assert (t, v) == (_k(2026, 11, 12, 6, 55), 2.0)


def test_nq_weekend_uses_friday_close_bar_and_stale_cap():
    s = po.NqSeries([(_k(2026, 11, 7, 6, 55), 5.0)])  # 토 06:55(금 미 동부 마감 직전)
    t, v = s.last_at_or_before(po.nq_now_cutoff(dt.date(2026, 11, 9)))  # 월 07:55 기준
    assert t == _k(2026, 11, 7, 6, 55) and v == 5.0
    assert s.last_at_or_before(_k(2026, 11, 12, 7, 55)) is None  # 72시간 넘게 오래됨


def test_nq_change_returns_times_and_now_cap():
    s = po.NqSeries(
        [(_k(2026, 11, 11, 15, 25), 100.0), (_k(2026, 11, 12, 1, 5), 101.0), (_k(2026, 11, 12, 7, 55), 105.0)]
    )
    ch = po.nq_change(s, dt.date(2026, 11, 11), dt.date(2026, 11, 12), now=_k(2026, 11, 12, 1, 15))
    assert ch["now_time"] == _k(2026, 11, 12, 1, 5) and ch["change_pct"] == pytest.approx(1.0)
    final = po.nq_change(s, dt.date(2026, 11, 11), dt.date(2026, 11, 12))
    assert final["now_time"] == _k(2026, 11, 12, 7, 55) and final["weak"] is False
    assert po.nq_change(po.NqSeries([]), dt.date(2026, 11, 11), dt.date(2026, 11, 12)) is None


# -- phase ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "now,phase,base,target,assumed",
    [
        (_k(2026, 11, 12, 0, 0), "forecast", "2026-11-11", "2026-11-12", False),  # 목 새벽
        (_k(2026, 11, 12, 7, 59), "forecast", "2026-11-11", "2026-11-12", False),
        (_k(2026, 11, 12, 8, 0), "opened", "2026-11-11", "2026-11-12", False),
        (_k(2026, 11, 12, 8, 59), "opened", "2026-11-11", "2026-11-12", False),
        (_k(2026, 11, 12, 9, 0), "hidden", "2026-11-12", "2026-11-12", False),
        (_k(2026, 11, 12, 15, 29), "hidden", "2026-11-12", "2026-11-12", False),
        (_k(2026, 11, 12, 15, 30), "forecast", "2026-11-12", "2026-11-13", True),
        (_k(2026, 11, 12, 23, 59), "forecast", "2026-11-12", "2026-11-13", True),
        (_k(2026, 11, 13, 20, 0), "forecast", "2026-11-13", "2026-11-16", True),  # 금 저녁 -> 월
        (_k(2026, 11, 14, 12, 0), "forecast", "2026-11-13", "2026-11-16", True),  # 토
        (_k(2026, 11, 15, 0, 0), "forecast", "2026-11-13", "2026-11-16", True),  # 일
        (_k(2026, 11, 16, 8, 30), "opened", "2026-11-13", "2026-11-16", False),  # 월 개장 직후
        (_k(2026, 12, 25, 10, 0), "forecast", "2026-12-24", "2026-12-28", True),  # 평일 휴장(성탄절)
    ],
)
def test_session_phase(now, phase, base, target, assumed):
    r = po.session_phase(now)
    assert r["phase"] == phase
    assert r["base_date"].isoformat() == base and r["target_date"].isoformat() == target
    assert r["target_date_assumed"] is assumed


# -- 워크포워드 ---------------------------------------------------------------------------


def test_walk_forward_exact_data_and_baseline():
    days, nq, closes, opens, xs = _synthetic(40)
    samples = po.build_samples(nq, closes, opens)
    rows = po.walk_forward(samples)
    assert len(rows) == len(samples) - po.MIN_SAMPLES  # 20일 확보 후부터
    assert rows[0]["date"] == samples[po.MIN_SAMPLES].date
    sm = po.walk_forward_summary(rows)
    assert sm["mae_pct"] < 1e-6  # 합성 데이터는 선형이라 오차 0
    assert sm["baseline_mae_pct"] > sm["mae_pct"]
    assert sm["direction_all"]["n"] == len(rows)
    assert sm["direction_nq_ge_1"]["n"] <= sm["direction_nq_ge_0_5"]["n"] <= sm["direction_all"]["n"]


def test_walk_forward_summary_empty():
    assert po.walk_forward_summary([]) == {"n": 0}
    assert po.walk_forward([]) == []


# -- 표본 외 기록 집계 ----------------------------------------------------------------------


class _L:
    def __init__(self, nq, exp, act):
        self.nq_change_pct, self.expected_gap_pct, self.actual_gap_pct = nq, exp, act
        self.error_pct = None if act is None else act - exp


def test_summarize_track():
    rows = [_L(1.0, 1.0, 2.0), _L(-1.0, -0.5, 0.5), _L(0.2, 0.1, -0.1), _L(2.0, 1.0, None)]
    t = router_mod.summarize_track(rows)
    assert t["n"] == 3
    assert t["mae_pct"] == pytest.approx((1.0 + 1.0 + 0.2) / 3, abs=0.01)
    assert t["hit_rate"] == 0.5  # |NQ|>=0.5 두 날 중 한 날만 부호 일치(0.2% 날은 제외)
    assert router_mod.summarize_track([]) == {"n": 0, "mae_pct": None, "hit_rate": None}


# -- API ----------------------------------------------------------------------------------


class _FakeResult:
    def scalars(self):
        return self

    def all(self):
        return []


class _FakeSession:
    async def execute(self, *a, **k):
        return _FakeResult()


@pytest_asyncio.fixture
async def api(monkeypatch):
    days, nq, closes, opens, xs = _synthetic(45, start=dt.date(2026, 9, 14))
    # 마지막 개장일 이후 "지금" 시점을 만들기 위해 NQ 봉 원본 목록을 다시 구성
    bars = []
    for d in days:
        bars.append((_k(d.year, d.month, d.day, 15, 25), 100.0))
        bars.append((_k(d.year, d.month, d.day, 7, 55), 100.0 * math.exp(xs[d])))
    state = {"now": _k(2026, 11, 12, 3, 0), "live": None, "closes": closes, "opens": opens}
    last = days[-1]
    # 기준일=2026-11-11의 종가/NQ를 DB에 둔 것으로, 대상일=11-12는 시가 없음(예측만)
    assert last >= dt.date(2026, 11, 11), last

    async def _db_bars(session):
        return [b for b in bars if b[0] <= _k(2026, 11, 12, 0, 30)]

    async def _live():
        return state["live"]

    async def _series(session, code):
        return dict(state["closes"]), {d: int(v) for d, v in state["opens"].items() if d < dt.date(2026, 11, 12)}

    async def _kclose(code, day):
        return None

    async def _nopen(code, day):
        return None

    monkeypatch.setattr(router_mod, "kst_now", lambda: state["now"])
    monkeypatch.setattr(router_mod, "load_db_nq_bars", _db_bars)
    monkeypatch.setattr(router_mod, "fetch_live_nq_bars", _live)
    monkeypatch.setattr(router_mod, "load_stock_series", _series)
    monkeypatch.setattr(router_mod, "fetch_krx_close_live", _kclose)
    monkeypatch.setattr(router_mod, "fetch_nxt_open_live", _nopen)
    router_mod._cache.update(ts=0.0, data=None)
    app.dependency_overrides[get_session] = lambda: _FakeSession()
    state["bars"] = bars
    yield state
    app.dependency_overrides.pop(get_session, None)
    router_mod._cache.update(ts=0.0, data=None)


async def _get():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/markets/premarket-outlook")
    return r


async def test_api_forecast_shape_and_live_merge(api):
    api["live"] = [(_k(2026, 11, 12, 2, 55), 101.0), (_k(2026, 11, 11, 15, 25), 100.0)]
    r = await _get()
    assert r.status_code == 200
    j = r.json()
    assert j["phase"] == "forecast" and j["target_date"] == "2026-11-12" and j["target_date_assumed"] is False
    assert j["base_date"] == "2026-11-11" and j["disclaimer"] == "참고용, 매매 신호 아님"
    nq = j["nq"]
    assert nq["now_time"].startswith("2026-11-12T03:00") and nq["source"] == "live"
    assert nq["change_pct"] == 1.0 and nq["weak"] is False and nq["age_minutes"] == 0
    assert nq["delay_note"] == "약 10분 지연"
    assert [i["code"] for i in j["items"]] == list(po.OUTLOOK_CODES)
    it = j["items"][0]
    assert it["status"] == "ok" and it["base_close"] == 1_000_000
    for k in ("n", "alpha_pct", "beta", "resid_std_pct", "resid_mad_pct", "expected_gap_pct",
              "expected_low_pct", "expected_high_pct", "expected_price", "track"):
        assert k in it
    assert it["actual_open"] is None and it["error_pct"] is None  # forecast에서는 비움
    assert it["track"] == {"n": 0, "mae_pct": None, "hit_rate": None}
    assert it["expected_gap_pct"] == pytest.approx(round((math.exp(ALPHA + BETA * math.log(1.01)) - 1) * 100, 2), abs=0.15)


async def test_api_yfinance_failure_falls_back_to_db_not_500(api):
    api["live"] = None
    r = await _get()
    assert r.status_code == 200
    nq = r.json()["nq"]
    assert nq["source"] == "db_fallback" and nq["now_time"].startswith("2026-11-11T15:30")  # DB 최신 봉 종료 시각 표기(밤사이 봉 없음)


async def test_api_no_nq_at_all_is_no_data(api, monkeypatch):
    async def _none(session):
        return []

    monkeypatch.setattr(router_mod, "load_db_nq_bars", _none)
    api["live"] = None
    r = await _get()
    j = r.json()
    assert r.status_code == 200 and j["nq"] is None
    assert all(i["status"] == "no_data" for i in j["items"])


async def test_api_kiwoom_failure_marks_no_data_not_500(api):
    api["closes"] = {}  # DB에 기준 종가 없음 + 키움 라이브도 None
    r = await _get()
    assert r.status_code == 200
    assert all(i["status"] == "no_data" and i["base_close"] is None for i in r.json()["items"])


async def test_api_hidden_returns_empty_items(api):
    api["now"] = _k(2026, 11, 12, 10, 0)
    j = (await _get()).json()
    assert j["phase"] == "hidden" and j["items"] == [] and j["nq"] is None


async def test_api_opened_actual_open_from_db_and_live_and_null(api, monkeypatch):
    api["now"] = _k(2026, 11, 12, 8, 30)
    api["live"] = [(_k(2026, 11, 12, 7, 55), 102.0), (_k(2026, 11, 11, 15, 25), 100.0)]

    async def _series(session, code):
        opens = {d: int(v) for d, v in api["opens"].items() if d < dt.date(2026, 11, 12)}
        if code == "005930":
            opens[dt.date(2026, 11, 12)] = 1_020_000  # DB에 08:00 시가 있음
        return dict(api["closes"]), opens

    async def _nopen(code, day):
        return 990_000 if code == "000660" else None  # 하이닉스만 라이브 폴백

    monkeypatch.setattr(router_mod, "load_stock_series", _series)
    monkeypatch.setattr(router_mod, "fetch_nxt_open_live", _nopen)
    j = (await _get()).json()
    assert j["phase"] == "opened"
    items = {i["code"]: i for i in j["items"]}
    s = items["005930"]
    assert s["actual_open"] == 1_020_000 and s["actual_gap_pct"] == 2.0
    assert s["error_pct"] == pytest.approx(2.0 - s["expected_gap_pct"], abs=0.02)
    assert items["000660"]["actual_open"] == 990_000 and items["000660"]["actual_gap_pct"] == -1.0
    assert items["005380"]["actual_open"] is None and items["005380"]["error_pct"] is None
    assert j["nq"]["now_time"].startswith("2026-11-12T08:00")  # 08:00 직전 봉 고정


async def test_api_cache_60s(api, monkeypatch):
    calls = {"n": 0}

    async def _live():
        calls["n"] += 1
        return None

    monkeypatch.setattr(router_mod, "fetch_live_nq_bars", _live)
    await _get()
    await _get()
    assert calls["n"] == 1


# -- 로그 잡(실제 DB, 롤백) -----------------------------------------------------------------


@pytest_asyncio.fixture
async def rb_session():
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()  # 항상 롤백 — 테스트 행이 실제 DB에 남지 않는다


async def _seed(session, code, days, closes, opens, skip_open_on=None):
    for d in days:
        session.add(KrxRegularClose(code=code, date=d, close=int(closes[d])))
        if d != skip_open_on:
            session.add(NxtIntradayBar(code=code, date=d, time="0800", open=int(opens[d]), high=1, low=1, close=1, volume=1))
    await session.flush()


async def test_log_job_upsert_idempotent_and_skips_without_open(rb_session, monkeypatch):
    days, nq, closes, opens, xs = _synthetic(40, start=dt.date(2026, 9, 14))
    bars = []
    for d in days:
        bars.append((_k(d.year, d.month, d.day, 15, 25), 100.0))
        bars.append((_k(d.year, d.month, d.day, 7, 55), 100.0 * math.exp(xs[d])))

    async def _db(session):
        return bars

    async def _live():
        return None

    monkeypatch.setattr(pol, "load_db_nq_bars", _db)
    monkeypatch.setattr(pol, "fetch_live_nq_bars", _live)
    monkeypatch.setattr(pol, "OUTLOOK_CODES", ("TSTA", "TSTB"))
    target = days[-1]
    await _seed(rb_session, "TSTA", days, closes, opens)
    await _seed(rb_session, "TSTB", days, closes, opens, skip_open_on=target)  # B는 그날 실제 시가 없음

    saved, msg = await pol.collect_premarket_outlook_log(rb_session, target)
    assert saved == 1 and "TSTB" in (msg or "")
    row = (await rb_session.execute(select(PremarketOutlookLog).where(PremarketOutlookLog.code == "TSTA"))).scalar_one()
    assert row.date == target and po.MIN_SAMPLES <= row.n <= len(days) - 1
    assert row.actual_open == int(opens[target]) and row.base_close == 1_000_000
    assert row.error_pct == pytest.approx(row.actual_gap_pct - row.expected_gap_pct)
    assert row.beta == pytest.approx(BETA, abs=0.02)
    # 재실행은 중복 없이 덮어쓴다
    await pol.collect_premarket_outlook_log(rb_session, target)
    cnt = (await rb_session.execute(select(func.count()).select_from(PremarketOutlookLog).where(PremarketOutlookLog.code.in_(["TSTA", "TSTB"])))).scalar_one()
    assert cnt == 1
    # 실제 DB 커밋 없이 롤백될 것이므로 외부에서 별도 확인(fixture)


async def test_log_job_skips_holiday(rb_session):
    saved, msg = await pol.collect_premarket_outlook_log(rb_session, dt.date(2026, 11, 14))  # 토
    assert saved == 0 and "휴장" in msg


async def test_no_test_rows_leaked_after_rollback():
    """위 테스트들이 롤백으로 정리됐는지 — TST 코드 행이 실제 DB에 없어야 한다(대소문자 정확 일치 비교)."""
    async with engine.connect() as conn:
        s = AsyncSession(bind=conn)
        for model in (PremarketOutlookLog, KrxRegularClose, NxtIntradayBar):
            n = (await s.execute(select(func.count()).select_from(model).where(model.code.in_(["TSTA", "TSTB"])))).scalar_one()
            assert n == 0, model.__tablename__
