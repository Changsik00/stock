"""Unit tests for app.collectors.value_rank.collect_value_rank (PLAN.md §4.6 3.6-1).

No real network/DB involved — naver_value_rank.fetch_all / naver_rank.fetch_etf_codes
and the DB upsert are monkeypatched (same pattern as tests/test_flow_rank_collector.py).
Pins down the module docstring's design decisions:

1. target_date is not sent to the source — whatever date(s) the source's rows carry
   get written, and target_date only affects the log message text.
2. Only the top TOP_N rows per market are upserted (the source already returns rows
   sorted by value_million descending, per naver_value_rank.fetch_all's contract).
3. turnover = value_million / market_value_million * 100, computed straight from the
   source payload with no extra per-stock API calls.
4. is_etf comes from naver_rank.fetch_etf_codes(), not the source's own stockEndType.
5. **stocks 마스터 upsert (2026-07-18 추가)** — the *entire* per-market universe
   (not just TOP_N) gets upserted into stocks via ``_upsert_stock_master``, with
   name/market refreshed but is_etf left alone (new rows default False, existing
   rows keep whatever etf_master.py already set — see that function's docstring).
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.collectors import value_rank

DATE = dt.date(2026, 7, 16)


def _row(code, name, value_million, market_value_million, change_rate=1.0):
    return {
        "code": code,
        "name": name,
        "value_million": value_million,
        "market_value_million": market_value_million,
        "change_rate": change_rate,
        "stock_end_type": "stock",
    }


def _patch_common(
    monkeypatch, kospi_rows, kosdaq_rows, etf_codes=None, upserted=None, stock_master_upserted=None
):
    def fake_fetch_etf_codes():
        return etf_codes or set()

    def fake_fetch_all(market, sleep_seconds=0.0):
        rows = kospi_rows if market == "kospi" else kosdaq_rows
        return {"date": DATE, "rows": rows}

    async def fake_upsert(session, date, market, rows, codes):
        if upserted is not None:
            upserted.append((date, market, list(rows), set(codes)))
        return min(len(rows), value_rank.TOP_N)

    async def fake_upsert_stock_master(session, market, rows):
        if stock_master_upserted is not None:
            stock_master_upserted.append((market, list(rows)))
        return len(rows)

    monkeypatch.setattr(value_rank.naver_rank, "fetch_etf_codes", fake_fetch_etf_codes)
    monkeypatch.setattr(value_rank.naver_value_rank, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(value_rank, "_upsert_market_rows", fake_upsert)
    monkeypatch.setattr(value_rank, "_upsert_stock_master", fake_upsert_stock_master)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(value_rank.time, "sleep", lambda _seconds: None)


async def test_collect_value_rank_upserts_both_markets(monkeypatch):
    kospi_rows = [_row("005930", "삼성전자", 6838413, 1490801045)]
    kosdaq_rows = [_row("069500", "KODEX 200", 1938809, 24377850)]
    upserted: list[tuple] = []
    stock_master_upserted: list[tuple] = []
    _patch_common(
        monkeypatch,
        kospi_rows,
        kosdaq_rows,
        etf_codes={"069500"},
        upserted=upserted,
        stock_master_upserted=stock_master_upserted,
    )

    total, message = await value_rank.collect_value_rank(session=None, target_date=DATE)

    assert total == 2
    assert len(upserted) == 2
    markets_seen = {u[1] for u in upserted}
    assert markets_seen == {"kospi", "kosdaq"}
    for date, _market, rows, codes in upserted:
        assert date == DATE
        assert codes == {"069500"}
    assert message is not None
    assert "무시됨" not in message
    assert DATE.isoformat() in message

    # 문제 1 수정: TOP_N 슬라이스 전 전량이 stocks 마스터로도 넘어간다.
    assert len(stock_master_upserted) == 2
    stock_markets_seen = {m for m, _rows in stock_master_upserted}
    assert stock_markets_seen == {"kospi", "kosdaq"}
    assert "stocks 마스터 upsert 2건" in message


async def test_collect_value_rank_notes_when_target_date_not_returned(monkeypatch):
    _patch_common(monkeypatch, [_row("005930", "삼성전자", 100, 1000)], [])

    other_date = dt.date(2099, 1, 1)
    _total, message = await value_rank.collect_value_rank(session=None, target_date=other_date)

    assert message is not None
    assert "무시됨" in message
    assert other_date.isoformat() in message


async def test_upsert_market_rows_computes_turnover_and_truncates_to_top_n(monkeypatch):
    """실제 _upsert_market_rows(모킹하지 않은 버전)가 turnover를 올바르게 계산하고
    TOP_N을 넘는 행은 저장하지 않는지 — pg_insert 호출을 가로채 검증한다."""
    captured_stmts = []

    class FakeSession:
        async def execute(self, stmt):
            captured_stmts.append(stmt)

    rows = [_row(f"{i:06d}", f"종목{i}", 1000 - i, 10000) for i in range(value_rank.TOP_N + 5)]
    session = FakeSession()

    count = await value_rank._upsert_market_rows(session, DATE, "kospi", rows, etf_codes={"000000"})

    assert count == value_rank.TOP_N
    assert len(captured_stmts) == value_rank.TOP_N

    first_stmt = captured_stmts[0]
    compiled = first_stmt.compile()
    params = compiled.params
    assert params["value"] == 1000
    assert params["turnover"] == pytest.approx(10.0)  # 1000/10000*100
    assert params["is_etf"] is True
    assert params["rank"] == 1
    assert params["market"] == "kospi"

    last_stmt = captured_stmts[-1]
    last_params = last_stmt.compile().params
    assert last_params["rank"] == value_rank.TOP_N


async def test_upsert_market_rows_leaves_turnover_null_when_market_value_missing(monkeypatch):
    captured_stmts = []

    class FakeSession:
        async def execute(self, stmt):
            captured_stmts.append(stmt)

    rows = [_row("005930", "삼성전자", 100, None)]
    session = FakeSession()

    await value_rank._upsert_market_rows(session, DATE, "kospi", rows, etf_codes=set())

    params = captured_stmts[0].compile().params
    assert params["turnover"] is None
    assert params["is_etf"] is False


async def test_upsert_stock_master_inserts_full_universe_with_uppercase_market():
    """문제 1 수정 — TOP_N 슬라이스와 무관하게 넘겨받은 rows 전량을 upsert하고,
    market은 models.py 관례(KOSPI/KOSDAQ)에 맞춰 대문자로 저장한다."""
    captured_stmts = []

    class FakeSession:
        async def execute(self, stmt):
            captured_stmts.append(stmt)

    rows = [_row(f"{i:06d}", f"종목{i}", 1000 - i, 10000) for i in range(value_rank.TOP_N + 5)]
    session = FakeSession()

    count = await value_rank._upsert_stock_master(session, "kospi", rows)

    assert count == value_rank.TOP_N + 5  # TOP_N으로 잘리지 않는다(value_rank와 다름)
    assert len(captured_stmts) == value_rank.TOP_N + 5

    params = captured_stmts[0].compile().params
    assert params["code"] == "000000"
    assert params["name"] == "종목0"
    assert params["market"] == "KOSPI"
    assert params["is_etf"] is False  # 신규 insert는 항상 False


async def test_upsert_stock_master_kosdaq_label_and_conflict_set_excludes_is_etf():
    """market_label이 kosdaq -> 'KOSDAQ'로 매핑되고, ON CONFLICT의 SET 절에
    is_etf가 아예 없어(Postgres가 기존 값을 그대로 보존) etf_master.py가 이미
    적재해 둔 is_etf=True를 이 upsert가 절대 덮어쓰지 못함을 검증한다."""
    captured_stmts = []

    class FakeSession:
        async def execute(self, stmt):
            captured_stmts.append(stmt)

    rows = [_row("069500", "KODEX 200", 100, 1000)]
    session = FakeSession()

    await value_rank._upsert_stock_master(session, "kosdaq", rows)

    stmt = captured_stmts[0]
    params = stmt.compile().params
    assert params["market"] == "KOSDAQ"

    # on_conflict_do_update의 SET 절 컬럼 목록을 직접 검사 — is_etf가 없어야
    # Postgres가 ON CONFLICT에서 기존 값을 그대로 보존한다.
    update_cols = {col_name for col_name, _value in stmt._post_values_clause.update_values_to_set}
    assert update_cols == {"name", "market"}
    assert "is_etf" not in update_cols
