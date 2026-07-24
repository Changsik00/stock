"""Unit/DB tests for app.quant.etf_weight_changes (PLAN.md §5.25, ETF 비중 변화 감지).

순수 함수(``_classify``/``_sort_key``)는 DB 없이 직접 검증하고, ``_two_most_recent_dates``/
``compute_etf_weight_changes``는 실 dev Postgres(app.db.async_session_factory)를 쓴다
(test_concentration_backtest.py와 동일한 하우스 패턴). etf_holdings에는 실제로
2026-07-* 데이터가 이미 적재돼 있으므로(§5.25 알테오젠 검증 근거), 그보다 항상
미래인 2099-04-* 날짜로 테스트 행을 심어 실데이터와 절대 겹치지 않게 격리한다
(_two_most_recent_dates는 date DESC 정렬이라 미래 날짜가 항상 "가장 최근"으로 잡힌다).

"스냅샷 2개 미만" 엣지 케이스는 이 방식으로 재현할 수 없다 — 실 DB에 이미
2026-07-* 4개 날짜가 있어 테이블을 비우지 않는 한 "전체 날짜 2개 미만"을 만들
수 없기 때문이다(실데이터를 지우는 위험한 짓은 하지 않는다). 대신
``_two_most_recent_dates``를 monkeypatch해 그 반환값(None)에 대해
``compute_etf_weight_changes``가 올바른 분기를 타는지만 검증한다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.db import async_session_factory, engine
from app.models import EtfHolding, Stock
from app.quant import etf_weight_changes as ewc

OLDER_DATE = dt.date(2099, 4, 1)  # _two_most_recent_dates가 무시해야 하는, 더 오래된 날짜
PREV_DATE = dt.date(2099, 4, 2)
CURR_DATE = dt.date(2099, 4, 3)

ETF_ACTIVE = "990001"  # 이름에 "액티브" 포함
ETF_PASSIVE = "990002"  # 이름에 "액티브" 없음

STOCK_A = "990010"  # 비중확대(작은 폭)
STOCK_B = "990011"  # 비중축소(큰 폭)
STOCK_C = "990012"  # 임계값 미만 -> 제외(노이즈)
STOCK_D = "990013"  # 신규편입(작은 비중)
STOCK_E = "990014"  # 편출(작은 비중)
STOCK_F = "990015"  # 신규편입(큰 비중)
STOCK_G = "990016"  # 편출(큰 비중)

STOCKS = {
    ETF_ACTIVE: ("테스트바이오액티브", True),
    ETF_PASSIVE: ("테스트바이오ETF", True),
    STOCK_A: ("테스트종목A", False),
    STOCK_B: ("테스트종목B", False),
    STOCK_C: ("테스트종목C", False),
    STOCK_D: ("테스트종목D", False),
    STOCK_E: ("테스트종목E", False),
    STOCK_F: ("테스트종목F", False),
    STOCK_G: ("테스트종목G", False),
}

ALL_TEST_STOCK_CODES = list(STOCKS.keys())
ALL_TEST_DATES = [OLDER_DATE, PREV_DATE, CURR_DATE]


async def _clear_test_rows() -> None:
    async with async_session_factory() as session:
        await session.execute(EtfHolding.__table__.delete().where(EtfHolding.date.in_(ALL_TEST_DATES)))
        await session.execute(Stock.__table__.delete().where(Stock.code.in_(ALL_TEST_STOCK_CODES)))
        await session.commit()


@pytest.fixture
async def seeded_full():
    """PREV_DATE/CURR_DATE 사이 6가지 이벤트(비중확대/비중축소×2강도, 신규편입/
    편출×2강도) + 노이즈 1건(임계값 미만이라 제외돼야 함)을 심는다. OLDER_DATE에도
    한 행을 심어 _two_most_recent_dates가 이를 무시하는지 함께 검증한다."""
    await _clear_test_rows()
    async with async_session_factory() as session:
        for code, (name, is_etf) in STOCKS.items():
            session.add(Stock(code=code, name=name, market="KOSDAQ", is_etf=is_etf))
        await session.flush()

        # OLDER_DATE: 무시돼야 하는 더 오래된 스냅샷 (극단적인 값이라 실수로 섞이면
        # 바로 티가 남).
        session.add(EtfHolding(etf_code=ETF_ACTIVE, date=OLDER_DATE, stock_code=STOCK_A, weight=999.0))

        # PREV_DATE
        session.add(EtfHolding(etf_code=ETF_ACTIVE, date=PREV_DATE, stock_code=STOCK_A, weight=5.0))
        session.add(EtfHolding(etf_code=ETF_ACTIVE, date=PREV_DATE, stock_code=STOCK_B, weight=8.0))
        session.add(EtfHolding(etf_code=ETF_ACTIVE, date=PREV_DATE, stock_code=STOCK_C, weight=3.0))
        session.add(EtfHolding(etf_code=ETF_ACTIVE, date=PREV_DATE, stock_code=STOCK_G, weight=6.0))
        session.add(EtfHolding(etf_code=ETF_PASSIVE, date=PREV_DATE, stock_code=STOCK_E, weight=2.0))

        # CURR_DATE
        session.add(EtfHolding(etf_code=ETF_ACTIVE, date=CURR_DATE, stock_code=STOCK_A, weight=6.0))  # +1.0
        session.add(EtfHolding(etf_code=ETF_ACTIVE, date=CURR_DATE, stock_code=STOCK_B, weight=6.5))  # -1.5
        session.add(EtfHolding(etf_code=ETF_ACTIVE, date=CURR_DATE, stock_code=STOCK_C, weight=3.05))  # +0.05 노이즈
        session.add(EtfHolding(etf_code=ETF_ACTIVE, date=CURR_DATE, stock_code=STOCK_F, weight=9.0))  # 신규편입
        session.add(EtfHolding(etf_code=ETF_PASSIVE, date=CURR_DATE, stock_code=STOCK_D, weight=4.0))  # 신규편입
        await session.commit()
    yield
    await _clear_test_rows()
    await engine.dispose()


# ---------------------------------------------------------------------------
# 순수 함수 (DB 무관)
# ---------------------------------------------------------------------------


def test_classify_new_entry():
    assert ewc._classify(None, 5.0) == (ewc.EVENT_NEW, None)


def test_classify_removed():
    assert ewc._classify(5.0, None) == (ewc.EVENT_REMOVED, None)


def test_classify_expand_at_threshold_boundary():
    # 정확히 임계값(0.2)이면 포함(>=).
    assert ewc._classify(5.0, 5.2) == (ewc.EVENT_EXPAND, pytest.approx(0.2))


def test_classify_shrink_at_threshold_boundary():
    assert ewc._classify(5.2, 5.0) == (ewc.EVENT_SHRINK, pytest.approx(-0.2))


def test_classify_below_threshold_is_excluded():
    assert ewc._classify(5.0, 5.1) is None
    assert ewc._classify(5.1, 5.0) is None


def test_sort_key_orders_by_abs_delta_then_no_delta_group_by_weight():
    changes = [
        {"delta": 1.0, "curr_weight": 6.0, "prev_weight": 5.0},
        {"delta": -1.5, "curr_weight": 6.5, "prev_weight": 8.0},
        {"delta": None, "curr_weight": 9.0, "prev_weight": None},  # 신규편입, 큰 비중
        {"delta": None, "curr_weight": None, "prev_weight": 6.0},  # 편출, 중간 비중
        {"delta": None, "curr_weight": 4.0, "prev_weight": None},  # 신규편입, 작은 비중
    ]
    changes.sort(key=ewc._sort_key)
    ordered_deltas = [c["delta"] for c in changes]
    assert ordered_deltas == [-1.5, 1.0, None, None, None]
    no_delta_weights = [c["curr_weight"] or c["prev_weight"] for c in changes[2:]]
    assert no_delta_weights == [9.0, 6.0, 4.0]


# ---------------------------------------------------------------------------
# DB 기반 (실 dev Postgres, 2099-04 먼 미래 날짜로 격리)
# ---------------------------------------------------------------------------


async def test_two_most_recent_dates_ignores_older_and_returns_prev_curr_order(seeded_full):
    async with async_session_factory() as session:
        result = await ewc._two_most_recent_dates(session)
    assert result == (PREV_DATE, CURR_DATE)


async def test_compute_classifies_all_four_events_and_excludes_noise(seeded_full):
    async with async_session_factory() as session:
        result = await ewc.compute_etf_weight_changes(session, limit=200)

    assert result["prev_date"] == PREV_DATE.isoformat()
    assert result["curr_date"] == CURR_DATE.isoformat()

    by_code = {c["code"]: c for c in result["changes"]}

    # STOCK_C(노이즈, +0.05)는 결과에 아예 없어야 한다.
    assert STOCK_C not in by_code

    assert by_code[STOCK_A]["event"] == ewc.EVENT_EXPAND
    assert by_code[STOCK_A]["prev_weight"] == pytest.approx(5.0)
    assert by_code[STOCK_A]["curr_weight"] == pytest.approx(6.0)
    assert by_code[STOCK_A]["delta"] == pytest.approx(1.0)

    assert by_code[STOCK_B]["event"] == ewc.EVENT_SHRINK
    assert by_code[STOCK_B]["delta"] == pytest.approx(-1.5)

    assert by_code[STOCK_F]["event"] == ewc.EVENT_NEW
    assert by_code[STOCK_F]["prev_weight"] is None
    assert by_code[STOCK_F]["curr_weight"] == pytest.approx(9.0)

    assert by_code[STOCK_D]["event"] == ewc.EVENT_NEW
    assert by_code[STOCK_D]["curr_weight"] == pytest.approx(4.0)

    assert by_code[STOCK_G]["event"] == ewc.EVENT_REMOVED
    assert by_code[STOCK_G]["prev_weight"] == pytest.approx(6.0)
    assert by_code[STOCK_G]["curr_weight"] is None

    assert by_code[STOCK_E]["event"] == ewc.EVENT_REMOVED
    assert by_code[STOCK_E]["prev_weight"] == pytest.approx(2.0)

    # is_active — ETF_ACTIVE(테스트바이오액티브)를 경유한 변화는 True, ETF_PASSIVE는 False.
    assert by_code[STOCK_A]["is_active"] is True
    assert by_code[STOCK_D]["is_active"] is False

    # 정렬: |delta| 내림차순(B=-1.5, A=+1.0) 먼저, 그 다음 신규편입/편출을 남은
    # 비중 내림차순(F=9.0, G=6.0, D=4.0, E=2.0)으로.
    order = [c["code"] for c in result["changes"]]
    assert order == [STOCK_B, STOCK_A, STOCK_F, STOCK_G, STOCK_D, STOCK_E]


async def test_compute_code_filter_narrows_to_single_stock(seeded_full):
    async with async_session_factory() as session:
        result = await ewc.compute_etf_weight_changes(session, code=STOCK_A)
    assert [c["code"] for c in result["changes"]] == [STOCK_A]


async def test_compute_active_only_filter(seeded_full):
    async with async_session_factory() as session:
        result = await ewc.compute_etf_weight_changes(session, active_only=True, limit=200)
    codes = {c["code"] for c in result["changes"]}
    # ETF_ACTIVE 경유 변화만 남는다 (A/B/F/G) — ETF_PASSIVE 경유(D/E)는 빠진다.
    assert codes == {STOCK_A, STOCK_B, STOCK_F, STOCK_G}
    assert all(c["is_active"] for c in result["changes"])


async def test_compute_event_filter(seeded_full):
    async with async_session_factory() as session:
        result = await ewc.compute_etf_weight_changes(session, event=ewc.EVENT_NEW, limit=200)
    assert {c["code"] for c in result["changes"]} == {STOCK_D, STOCK_F}
    assert all(c["event"] == ewc.EVENT_NEW for c in result["changes"])


async def test_compute_limit_truncates_after_sort(seeded_full):
    async with async_session_factory() as session:
        result = await ewc.compute_etf_weight_changes(session, limit=2)
    # 정렬 후 상위 2개(|delta| 최대 두 건) = STOCK_B, STOCK_A.
    assert [c["code"] for c in result["changes"]] == [STOCK_B, STOCK_A]


async def test_compute_returns_empty_with_reason_when_insufficient_history(monkeypatch):
    """실 DB에는 이미 2026-07-* 4개 스냅샷이 있어 "날짜 2개 미만" 상태를 진짜로
    재현할 수 없다(실데이터를 지우지 않는다는 원칙) — _two_most_recent_dates 자체를
    monkeypatch해서 그 반환값(None)에 대해 compute_etf_weight_changes가 예외를
    던지지 않고 문서화된 빈 결과 모양을 내는지만 검증한다."""

    async def _fake_none(session):
        return None

    monkeypatch.setattr(ewc, "_two_most_recent_dates", _fake_none)

    async with async_session_factory() as session:
        result = await ewc.compute_etf_weight_changes(session)

    assert result["prev_date"] is None
    assert result["curr_date"] is None
    assert result["changes"] == []
    assert "reason" in result
