"""Tests for collectors.accumulation_screener.collect_accumulation_screener —
개인 매도/외국인·기관 전환 매집 관찰 스크리너 배치 잡 (관찰용, 매매 신호 아님).

Real dev Postgres via app.db.async_session_factory (same house pattern as
tests/test_stock_flow_scan.py/test_scalp_tracker.py). Test codes ("999801"/
"999802") don't collide with real KRX codes.

The three external collaborators are monkeypatched, never real network/real
stocks table:
- collectors.accumulation_screener._load_stock_universe -> fixed 2-code list
  (this repo's real dev stocks table already has ~3900 non-ETF rows —
  monkeypatching the universe query, not the stocks table itself, keeps this
  test fast and isolated, same seam-isolation idea as test_stock_flow_scan.py
  monkeypatching routers.flow_rank._warm_value_rank_live instead of touching
  value_rank).
- clients.kiwoom.KiwoomClient -> a fake that (a) returns canned ka10059
  bodies per test code and (b) counts constructions, verifying "exactly one
  KiwoomClient instance for the entire sweep" (PLAN.md §5.20-1 rate-limiter
  reasoning, unchanged for this new sweep).

stock_ohlcv (price series) is seeded directly in Postgres per test — this
collector never fetches candles itself, it only reads
services.get_stock_series_from_db, so real DB rows are the simplest way to
control that input.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import delete, select

from app.clients import kiwoom as kiwoom_module
from app.collectors import accumulation_screener
from app.db import async_session_factory, engine
from app.models import AccumulationPick, Stock, StockFlow, StockOhlcv

TEST_CODE_A = "999801"  # 3개 조건 전부 충족하도록 설계 -> 매칭돼야 함
TEST_CODE_B = "999802"  # 개인이 순매수(조건1 위반)로 설계 -> 매칭되면 안 됨
TEST_CODES = [TEST_CODE_A, TEST_CODE_B]

TARGET_DATE = dt.date(2099, 1, 11)

# 가격 시계열 — get_stock_series_from_db가 changeRate 계산에 "구간 이전 1일"을
# 더 필요로 하므로(services.py 참고) 11일치(day0 + 10거래일)를 seed한다. 매일
# +0.6%씩 완만하게 올려 10일 누적 약 +6.2%(조건3 하한 2%~상한 15% 안), 일간
# 등락폭은 항상 0.6%(조건3의 5% 이하)로 설계했다.
PRICE_DATES = [dt.date(2099, 1, d) for d in range(1, 12)]  # 01-01 .. 01-11 (11일)
_closes = [10000.0]
for _ in range(10):
    _closes.append(round(_closes[-1] * 1.006, 2))
PRICE_CLOSES = _closes  # len == 11, PRICE_CLOSES[0]은 창 이전(day0) 종가

# 수급 시계열 — 최근 10거래일(FLOW_DATES). 앞 5일이 "직전5일", 뒤 5일이 "최근5일".
FLOW_DATES = [dt.date(2099, 1, d) for d in range(2, 12)]  # 01-02 .. 01-11 (10일)
PRIOR5_DATES = FLOW_DATES[:5]
RECENT5_DATES = FLOW_DATES[5:]


@pytest.fixture(autouse=True)
async def _dispose_engine_per_test():
    yield
    await engine.dispose()


async def _clear_test_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(delete(AccumulationPick).where(AccumulationPick.code.in_(TEST_CODES)))
        await session.execute(delete(StockFlow).where(StockFlow.code.in_(TEST_CODES)))
        await session.execute(delete(StockOhlcv).where(StockOhlcv.code.in_(TEST_CODES)))
        await session.execute(delete(Stock).where(Stock.code.in_(TEST_CODES)))
        await session.commit()


@pytest.fixture
async def seeded_stocks_and_prices():
    """Stock 마스터 + 두 코드 모두 동일한(매칭 조건3을 만족하는) 가격 시계열을
    seed한다 — 수급(stock_flow)은 이 컬렉터 자신이 upsert하므로 여기서 심지
    않는다."""
    await _clear_test_rows()
    async with async_session_factory() as session:
        session.add(Stock(code=TEST_CODE_A, name="테스트매집A", market="KOSPI", is_etf=False))
        session.add(Stock(code=TEST_CODE_B, name="테스트매집B", market="KOSDAQ", is_etf=False))
        await session.flush()
        for code in TEST_CODES:
            for date_, close in zip(PRICE_DATES, PRICE_CLOSES):
                session.add(
                    StockOhlcv(
                        code=code,
                        date=date_,
                        open=int(close),
                        high=int(close),
                        low=int(close),
                        close=int(close),
                        volume=1000,
                        value=int(close) * 1000,
                    )
                )
        await session.commit()
    yield
    await _clear_test_rows()


def _fake_ka10059_response(daily_values: dict[dt.date, tuple[int, int, int]]) -> dict:
    """daily_values: date -> (individual, foreign, institution) net_value(백만원)."""
    rows = []
    for date_, (individual, foreign, inst) in daily_values.items():
        rows.append(
            {
                "dt": date_.strftime("%Y%m%d"),
                "ind_invsr": str(individual),
                "frgnr_invsr": str(foreign),
                "orgn": str(inst),
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
        )
    return {"return_code": 0, "return_msg": "", "stk_invsr_orgn": rows}


def _daily_values_for(individual_per_day: int) -> dict[dt.date, tuple[int, int, int]]:
    """개인은 매일 individual_per_day(고정), 외국인+기관계는 직전5일 20/일
    (합 100), 최근5일 60/일(합 300)로 전환/가속 패턴을 만든다(조건2 충족)."""
    out: dict[dt.date, tuple[int, int, int]] = {}
    for d in PRIOR5_DATES:
        out[d] = (individual_per_day, 10, 10)  # foreign+inst = 20/day
    for d in RECENT5_DATES:
        out[d] = (individual_per_day, 30, 30)  # foreign+inst = 60/day
    return out


# 코드 A: 개인 -50/일(10일 순매도 -500) -> 조건1 충족 -> 3개 조건 전부 충족(매칭)
_RESPONSE_A = _fake_ka10059_response(_daily_values_for(individual_per_day=-50))
# 코드 B: 개인 +50/일(10일 순매수 +500) -> 조건1 위반 -> 매칭되면 안 됨
_RESPONSE_B = _fake_ka10059_response(_daily_values_for(individual_per_day=50))


class _FakeKiwoomClient:
    """__init__ 호출 횟수를 클래스 변수에 기록 — "스윕 전체에 인스턴스가 정확히
    하나만 생성됐는지"가 이 테스트 파일의 핵심 단언 중 하나다(PLAN.md §5.20-1과
    동일한 rate-limiter 근거, tests/test_stock_flow_scan.py 참고)."""

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
        response = _RESPONSE_A if code == TEST_CODE_A else _RESPONSE_B
        return response, {"cont-yn": "N", "next-key": "", "api-id": "ka10059"}


@pytest.fixture(autouse=True)
def _reset_fake_client_state():
    _FakeKiwoomClient.instance_count = 0
    _FakeKiwoomClient.calls = []
    yield


async def _fake_universe(session):
    return [(TEST_CODE_A, "테스트매집A", "KOSPI"), (TEST_CODE_B, "테스트매집B", "KOSDAQ")]


async def test_single_kiwoom_client_for_entire_sweep(monkeypatch, seeded_stocks_and_prices):
    monkeypatch.setattr(accumulation_screener, "_load_stock_universe", _fake_universe)
    monkeypatch.setattr(kiwoom_module, "KiwoomClient", _FakeKiwoomClient)

    async with async_session_factory() as session:
        rows, message = await accumulation_screener.collect_accumulation_screener(session, TARGET_DATE)
        await session.commit()

    assert _FakeKiwoomClient.instance_count == 1
    assert set(_FakeKiwoomClient.calls) == {TEST_CODE_A, TEST_CODE_B}
    assert isinstance(rows, int)
    assert message is not None


async def test_only_matching_code_is_upserted_into_accumulation_pick(
    monkeypatch, seeded_stocks_and_prices
):
    monkeypatch.setattr(accumulation_screener, "_load_stock_universe", _fake_universe)
    monkeypatch.setattr(kiwoom_module, "KiwoomClient", _FakeKiwoomClient)

    async with async_session_factory() as session:
        rows, _message = await accumulation_screener.collect_accumulation_screener(session, TARGET_DATE)
        await session.commit()

    assert rows == 1  # 코드 A만 매칭

    async with async_session_factory() as verify_session:
        picks = (
            await verify_session.execute(
                select(AccumulationPick).where(AccumulationPick.code.in_(TEST_CODES))
            )
        ).scalars().all()

    by_code = {p.code: p for p in picks}
    assert TEST_CODE_A in by_code
    assert TEST_CODE_B not in by_code  # 개인 순매수(조건1 위반) -> 매칭 안 됨

    pick = by_code[TEST_CODE_A]
    assert pick.name == "테스트매집A"
    assert pick.market == "KOSPI"
    assert float(pick.individual_net_10d) == -500.0
    assert float(pick.foreign_inst_net_recent5d) == 300.0
    assert float(pick.foreign_inst_net_prior5d) == 100.0
    assert 2.0 <= float(pick.price_return_10d_pct) <= 15.0
    assert float(pick.max_abs_daily_return_10d_pct) <= 5.0
    assert pick.reason is not None
    # 관찰 기록이지 평가 문구가 섞이면 안 된다(§5 house rule).
    assert "추천" not in pick.reason


async def test_stock_flow_rows_are_upserted_for_both_codes_regardless_of_match(
    monkeypatch, seeded_stocks_and_prices
):
    """매칭 여부와 무관하게 upsert(stock_flow)는 두 코드 다 일어나야 한다 —
    패턴 판정은 그 upsert 이후의 별도 단계일 뿐이다."""
    monkeypatch.setattr(accumulation_screener, "_load_stock_universe", _fake_universe)
    monkeypatch.setattr(kiwoom_module, "KiwoomClient", _FakeKiwoomClient)

    async with async_session_factory() as session:
        await accumulation_screener.collect_accumulation_screener(session, TARGET_DATE)
        await session.commit()

    async with async_session_factory() as verify_session:
        flow_rows = (
            await verify_session.execute(select(StockFlow).where(StockFlow.code.in_(TEST_CODES)))
        ).scalars().all()

    codes_with_flow = {r.code for r in flow_rows}
    assert codes_with_flow == {TEST_CODE_A, TEST_CODE_B}


async def test_one_code_failure_does_not_block_the_rest_of_the_sweep(
    monkeypatch, seeded_stocks_and_prices
):
    """PLAN.md 작업 지시: 코드 하나 실패해도 나머지 스윕을 막으면 안 된다."""
    monkeypatch.setattr(accumulation_screener, "_load_stock_universe", _fake_universe)

    class _PartialFailKiwoomClient(_FakeKiwoomClient):
        async def stock_investor_daily(self, code: str):
            if code == TEST_CODE_A:
                type(self).calls.append(code)
                raise RuntimeError("일시적 조회 실패(테스트)")
            return await super().stock_investor_daily(code)

    monkeypatch.setattr(kiwoom_module, "KiwoomClient", _PartialFailKiwoomClient)

    async with async_session_factory() as session:
        rows, _message = await accumulation_screener.collect_accumulation_screener(session, TARGET_DATE)
        await session.commit()

    assert _PartialFailKiwoomClient.instance_count == 1
    # B는 개인 순매수라 애초에 매칭 대상이 아니므로 rows == 0(A는 실패해서
    # 아예 처리 안 됐고, B는 처리됐지만 패턴 불일치).
    assert rows == 0

    async with async_session_factory() as verify_session:
        rows_a = (
            await verify_session.execute(select(StockFlow).where(StockFlow.code == TEST_CODE_A))
        ).scalars().all()
        rows_b = (
            await verify_session.execute(select(StockFlow).where(StockFlow.code == TEST_CODE_B))
        ).scalars().all()

    assert rows_a == []  # A는 조회 자체가 실패해 아무것도 upsert되지 않았다.
    assert len(rows_b) > 0  # B는 정상 처리됐다.


async def test_collect_fn_does_not_commit_session(monkeypatch, seeded_stocks_and_prices):
    """collect_fn 계약(collectors/base.py): run_job이 트랜잭션을 소유하므로
    collect_fn 자신은 commit하면 안 된다 — 여기서 명시적으로 rollback한 뒤
    별도 세션에서 아무 것도 안 보여야 실제로 커밋되지 않았다는 증거가 된다."""
    monkeypatch.setattr(accumulation_screener, "_load_stock_universe", _fake_universe)
    monkeypatch.setattr(kiwoom_module, "KiwoomClient", _FakeKiwoomClient)

    async with async_session_factory() as session:
        rows, _message = await accumulation_screener.collect_accumulation_screener(session, TARGET_DATE)
        assert rows == 1  # sanity: 매칭이 실제로 일어났다(뭔가 쓸 게 있었다)
        await session.rollback()  # 커밋 안 했으므로 여기서 버려도 DB에 안 남아야 한다

    async with async_session_factory() as verify_session:
        picks = (
            await verify_session.execute(
                select(AccumulationPick).where(AccumulationPick.code.in_(TEST_CODES))
            )
        ).scalars().all()
        flow_rows = (
            await verify_session.execute(select(StockFlow).where(StockFlow.code.in_(TEST_CODES)))
        ).scalars().all()

    assert picks == []
    assert flow_rows == []


async def test_empty_universe_returns_zero_without_error(monkeypatch, seeded_stocks_and_prices):
    async def _empty_universe(session):
        return []

    monkeypatch.setattr(accumulation_screener, "_load_stock_universe", _empty_universe)
    monkeypatch.setattr(kiwoom_module, "KiwoomClient", _FakeKiwoomClient)

    async with async_session_factory() as session:
        rows, message = await accumulation_screener.collect_accumulation_screener(session, TARGET_DATE)

    assert rows == 0
    assert _FakeKiwoomClient.instance_count == 0  # 유니버스가 비었으면 클라이언트조차 안 연다
    assert message is not None
