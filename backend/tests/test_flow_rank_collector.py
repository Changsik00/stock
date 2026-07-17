"""Unit tests for app.collectors.flow_rank.collect_flow_rank.

No real network/DB involved — naver fetch functions and the DB upsert helper are
monkeypatched (same pattern as tests/test_ohlcv_collector.py). Pins down the design
decisions documented in collectors/flow_rank.py's module docstring:

1. target_date is not sent to the source (it doesn't support a date query) — whatever
   dates the source returns get written, and target_date only affects the message text.
2. kospi + kosdaq candidates are merged and re-ranked by |net_value| descending into a
   single per-(investor, side) rank space (flow_rank has no market column).
3. buy와 sell 양쪽 side를 모두 수집하고, sell의 소스 음수 값은 정렬에 절대값으로
   쓰인다 (양수 정규화 자체는 _upsert_rank_rows 내부 — abs() 저장).
4. 회전율: ETF는 fetch_etf_list의 벌크 값으로, 개별주만 종목당 1회
   fetch_stock_market_value로 조회하며 그룹 간 중복 코드는 dedup된다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.collectors import flow_rank

DATE1 = dt.date(2026, 7, 15)
DATE2 = dt.date(2026, 7, 16)

ETF_ITEMS = [
    # KODEX 200: 거래대금 1,938,809백만 / 시총 24,377,900백만 -> 회전율 7.9531%
    {"code": "069500", "name": "KODEX 200", "amount_million": 1938809, "aum_million": 24377900},
    # KODEX 레버리지: aum 없음 -> 회전율 스킵(NULL)
    {"code": "122630", "name": "KODEX 레버리지", "amount_million": 500, "aum_million": None},
]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(flow_rank.time, "sleep", lambda _seconds: None)


def _blocks_for(market: str, side: str) -> list[dict]:
    # kospi has the bigger |net_value| rows so the merge/re-rank order is verifiable.
    # sell rows carry the source's negative sign (clients/naver_rank.py docstring).
    sign = -1 if side == "sell" else 1
    if market == "kospi":
        return [
            {
                "date": DATE1,
                "rows": [
                    {"code": "000660", "name": "SK하이닉스", "net_value": sign * 700, "quantity": sign * 3},
                ],
            },
            {
                "date": DATE2,
                "rows": [
                    {"code": "005930", "name": "삼성전자", "net_value": sign * 500, "quantity": sign * 2},
                ],
            },
        ]
    return [
        {
            "date": DATE1,
            "rows": [
                {"code": "069500", "name": "KODEX 200", "net_value": sign * 900, "quantity": sign * 8},
            ],
        },
        {
            "date": DATE2,
            "rows": [
                {"code": "122630", "name": "KODEX 레버리지", "net_value": sign * 100, "quantity": sign * 1},
            ],
        },
    ]


def _patch_common(monkeypatch, upserted=None, mv_calls=None):
    def fake_fetch_deal_rank(market, investor, side):
        return _blocks_for(market, side)

    def fake_fetch_etf_list():
        return ETF_ITEMS

    def fake_fetch_market_value(code):
        if mv_calls is not None:
            mv_calls.append(code)
        return {
            "accumulated_trading_value_million": 100,
            "market_value_million": 10000,  # -> turnover 1.0%
        }

    async def fake_upsert(session, date, investor, side, rows, etf_codes, turnover_map):
        if upserted is not None:
            upserted.append((date, investor, side, list(rows), etf_codes, dict(turnover_map)))
        return len(rows)

    monkeypatch.setattr(flow_rank, "_fetch_deal_rank_blocking", fake_fetch_deal_rank)
    monkeypatch.setattr(flow_rank, "_fetch_etf_list_blocking", fake_fetch_etf_list)
    monkeypatch.setattr(flow_rank, "_fetch_stock_market_value_blocking", fake_fetch_market_value)
    monkeypatch.setattr(flow_rank, "_upsert_rank_rows", fake_upsert)


async def test_collect_flow_rank_merges_markets_and_reranks_by_abs_net_value(monkeypatch):
    upserted: list[tuple] = []
    _patch_common(monkeypatch, upserted=upserted)

    total, message = await flow_rank.collect_flow_rank(session=None, target_date=DATE2)

    # 2 investors x 2 sides x 2 dates = 8 upsert calls, 2 rows each => total 16.
    assert total == 16
    assert len(upserted) == 8

    foreign_buy_d1 = next(u for u in upserted if u[1] == "foreign" and u[2] == "buy" and u[0] == DATE1)
    # KODEX 200 (900) outranks SK하이닉스 (700) once kospi+kosdaq are merged.
    assert [r["code"] for r in foreign_buy_d1[3]] == ["069500", "000660"]
    assert foreign_buy_d1[4] == {"069500", "122630"}

    # sell 쪽도 |net_value| 기준으로 같은 순서여야 한다 (-900이 -700보다 앞).
    foreign_sell_d1 = next(
        u for u in upserted if u[1] == "foreign" and u[2] == "sell" and u[0] == DATE1
    )
    assert [r["code"] for r in foreign_sell_d1[3]] == ["069500", "000660"]

    # turnover_map: ETF는 벌크 계산(069500만 — 122630은 aum 없음), 개별주는 1.0%.
    turnover_map = foreign_buy_d1[5]
    assert turnover_map["069500"] == pytest.approx(7.9531, abs=1e-4)
    assert "122630" not in turnover_map
    assert turnover_map["000660"] == pytest.approx(1.0)
    assert turnover_map["005930"] == pytest.approx(1.0)

    # target_date (DATE2) is one of the dates actually returned -> no "ignored" note.
    assert message is not None
    assert "무시됨" not in message
    assert DATE1.isoformat() in message
    assert DATE2.isoformat() in message


async def test_collect_flow_rank_tags_rows_with_source_market(monkeypatch):
    """§4.6 3.6-1: 코스피+코스닥 병합 이후에도 각 row가 어느 시장에서 왔는지
    (FlowRank.market) row dict에 남아 있어야 한다 — _upsert_rank_rows가 이 값을
    그대로 저장한다."""
    upserted: list[tuple] = []
    _patch_common(monkeypatch, upserted=upserted)

    await flow_rank.collect_flow_rank(session=None, target_date=DATE2)

    foreign_buy_d1 = next(u for u in upserted if u[1] == "foreign" and u[2] == "buy" and u[0] == DATE1)
    by_code = {r["code"]: r["market"] for r in foreign_buy_d1[3]}
    assert by_code == {"069500": "kosdaq", "000660": "kospi"}


async def test_upsert_rank_rows_persists_market_column():
    """실제 _upsert_rank_rows(모킹하지 않은 버전)가 row별 market을 pg_insert
    values에 그대로 실어 보내는지 — 세션 execute를 가로채 검증한다."""
    captured_stmts = []

    class FakeSession:
        async def execute(self, stmt):
            captured_stmts.append(stmt)

    rows = [
        {"code": "000660", "name": "SK하이닉스", "net_value": 700, "quantity": 3, "market": "kospi"},
        {"code": "069500", "name": "KODEX 200", "net_value": 500, "quantity": 2, "market": "kosdaq"},
    ]
    session = FakeSession()

    count = await flow_rank._upsert_rank_rows(
        session, DATE1, "foreign", "buy", rows, etf_codes={"069500"}, turnover_map={}
    )

    assert count == 2
    markets = [stmt.compile().params["market"] for stmt in captured_stmts]
    assert markets == ["kospi", "kosdaq"]


async def test_collect_flow_rank_notes_when_target_date_not_returned(monkeypatch):
    _patch_common(monkeypatch)

    other_date = dt.date(2099, 1, 1)
    _total, message = await flow_rank.collect_flow_rank(session=None, target_date=other_date)

    assert message is not None
    assert "무시됨" in message
    assert other_date.isoformat() in message


async def test_collect_flow_rank_queries_both_markets_investors_and_sides(monkeypatch):
    calls = []

    def fake_fetch(market, investor, side):
        calls.append((market, investor, side))
        return _blocks_for(market, side)

    _patch_common(monkeypatch)
    monkeypatch.setattr(flow_rank, "_fetch_deal_rank_blocking", fake_fetch)

    await flow_rank.collect_flow_rank(session=None, target_date=DATE2)

    assert sorted(calls) == sorted(
        (market, investor, side)
        for side in ("buy", "sell")
        for investor in ("foreign", "institution")
        for market in ("kospi", "kosdaq")
    )


async def test_collect_flow_rank_dedups_turnover_lookups_across_groups(monkeypatch):
    """같은 개별주가 buy/sell, foreign/institution 여러 그룹에 나타나도
    fetch_stock_market_value는 코드당 1회만 불려야 하고, ETF 코드는 아예 불리지
    않아야 한다."""
    mv_calls: list[str] = []
    _patch_common(monkeypatch, mv_calls=mv_calls)

    await flow_rank.collect_flow_rank(session=None, target_date=DATE2)

    # 개별주는 000660/005930 두 개뿐 — 그룹이 8개(2 investors x 2 sides x 2 dates)라도
    # 각 1회씩만.
    assert sorted(mv_calls) == ["000660", "005930"]


async def test_collect_flow_rank_survives_turnover_fetch_failure(monkeypatch):
    """개별주 하나의 회전율 조회가 실패해도 배치는 계속 진행되고, 그 코드만
    turnover_map에서 빠진다."""
    upserted: list[tuple] = []
    _patch_common(monkeypatch, upserted=upserted)

    def failing_fetch(code):
        if code == "005930":
            raise RuntimeError("boom")
        return {"accumulated_trading_value_million": 100, "market_value_million": 10000}

    monkeypatch.setattr(flow_rank, "_fetch_stock_market_value_blocking", failing_fetch)

    total, _message = await flow_rank.collect_flow_rank(session=None, target_date=DATE2)

    assert total == 16
    turnover_map = upserted[0][5]
    assert "005930" not in turnover_map
    assert turnover_map["000660"] == pytest.approx(1.0)
