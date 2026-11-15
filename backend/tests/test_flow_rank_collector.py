"""app.collectors.flow_rank 단위 테스트 (stock_flow + 키움 ka10059 소스).

외부 호출(키움/네이버)과 DB 헬퍼는 전부 monkeypatch한다 — 실제 DB에 행을 남기지 않는다.
"""

import datetime as dt

import pytest

from app.collectors import flow_rank

D = dt.date(2026, 11, 13)


def test_build_rankings_splits_buy_sell_and_limits_top_n():
    items = [
        {"code": f"{i:06d}", "name": f"S{i}", "market": "kospi", "is_etf": False,
         "foreign": (i + 1) * (1 if i % 2 == 0 else -1), "institution": None}
        for i in range(100)
    ]
    r = flow_rank.build_rankings(items)
    buy = r[("foreign", "buy")]
    sell = r[("foreign", "sell")]
    assert len(buy) == flow_rank.TOP_N and len(sell) == flow_rank.TOP_N
    # buy는 양수만, 크기 내림차순 / sell은 음수의 절대값 내림차순
    assert all(x["net_value"] > 0 for x in buy)
    assert [x["net_value"] for x in buy] == sorted((x["net_value"] for x in buy), reverse=True)
    assert buy[0]["code"] == "000098" and sell[0]["code"] == "000099"
    assert sell[0]["net_value"] == 100  # 음수가 아니라 양수 크기로 저장
    # 값이 None인 investor는 비어 있다
    assert r[("institution", "buy")] == [] and r[("institution", "sell")] == []


def test_build_rankings_ignores_zero_and_tiebreaks_by_code():
    items = [
        {"code": "B", "foreign": 10}, {"code": "A", "foreign": 10}, {"code": "Z", "foreign": 0},
    ]
    r = flow_rank.build_rankings(items)
    assert [x["code"] for x in r[("foreign", "buy")]] == ["A", "B"]
    assert r[("foreign", "sell")] == []


def test_parse_etf_history_signed_and_value_abs():
    data = {
        "stk_invsr_orgn": [
            {"dt": "20261113", "frgnr_invsr": "-24530", "orgn": "+12607", "acc_trde_prica": "1502698"},
            {"dt": "20261112", "frgnr_invsr": "27996", "orgn": "12406", "acc_trde_prica": "1520104"},
            {"dt": "", "frgnr_invsr": "1"},
        ]
    }
    h = flow_rank.parse_etf_history(data)
    assert h[D] == {"foreign": -24530, "institution": 12607, "value": 1502698}
    assert h[dt.date(2026, 11, 12)]["foreign"] == 27996
    assert len(h) == 2


@pytest.mark.asyncio
async def test_compute_rankings_merges_stocks_and_etfs_with_turnover(monkeypatch):
    async def fake_items(session, date):
        return [
            {"code": "005930", "name": "삼성전자", "market": "kospi", "is_etf": False,
             "foreign": 500, "institution": -300},
        ]

    async def fake_turn(session, date, codes, fetch_missing):
        return {"005930": 1.5}

    async def fake_aum(session, date, codes):
        return {"122630": 100000}

    monkeypatch.setattr(flow_rank, "_load_stock_items", fake_items)
    monkeypatch.setattr(flow_rank, "_stock_turnover_map", fake_turn)
    monkeypatch.setattr(flow_rank, "_etf_aum_map", fake_aum)

    etf_hist = {"122630": {D: {"foreign": -900, "institution": 200, "value": 5000}}}
    r = await flow_rank.compute_rankings(
        None, D, etf_hist, {"122630": "KODEX 레버리지"}, {}, today=dt.date(2026, 11, 20)
    )
    assert [x["code"] for x in r[("foreign", "buy")]] == ["005930"]
    sell = r[("foreign", "sell")]
    assert sell[0]["code"] == "122630" and sell[0]["is_etf"] is True
    assert sell[0]["net_value"] == 900 and sell[0]["market"] == "kospi"
    assert sell[0]["turnover"] == 5.0  # 5000 / 100000 * 100
    assert r[("foreign", "buy")][0]["turnover"] == 1.5
    assert r[("institution", "buy")][0]["code"] == "122630"


@pytest.mark.asyncio
async def test_collect_dates_skips_incomplete_and_saves_complete(monkeypatch):
    bad = dt.date(2026, 11, 12)

    async def fake_check(session, d):
        return (False, f"{d} 부족") if d == bad else (True, "")

    saved = []

    async def fake_prepare(session, end, today=None):
        return {}, {}, {}

    async def fake_compute(session, d, hist, names, aum, today=None):
        return {("foreign", "buy"): [{"code": "A"}] * 3}

    async def fake_save(session, d, ranks):
        saved.append(d)
        return 3

    monkeypatch.setattr(flow_rank, "check_complete", fake_check)
    monkeypatch.setattr(flow_rank, "prepare_etf_data", fake_prepare)
    monkeypatch.setattr(flow_rank, "compute_rankings", fake_compute)
    monkeypatch.setattr(flow_rank, "save_rankings", fake_save)

    total, notes = await flow_rank.collect_flow_rank_dates(None, [bad, D])
    assert total == 3 and saved == [D]
    assert any("건너뜀" in n for n in notes)


@pytest.mark.asyncio
async def test_collect_dates_all_incomplete_does_not_call_kiwoom(monkeypatch):
    async def fake_check(session, d):
        return False, "부족"

    async def boom(*a, **k):
        raise AssertionError("ETF 조회가 호출되면 안 된다")

    monkeypatch.setattr(flow_rank, "check_complete", fake_check)
    monkeypatch.setattr(flow_rank, "prepare_etf_data", boom)
    total, notes = await flow_rank.collect_flow_rank_dates(None, [D])
    assert total == 0 and notes


@pytest.mark.asyncio
async def test_collect_flow_rank_uses_recoverable_dates(monkeypatch):
    seen = {}

    async def fake_recover(session, target):
        return [dt.date(2026, 11, 12), target]

    async def fake_dates(session, dates, today=None):
        seen["dates"] = dates
        return 160, ["ok"]

    monkeypatch.setattr(flow_rank, "_recoverable_dates", fake_recover)
    monkeypatch.setattr(flow_rank, "collect_flow_rank_dates", fake_dates)
    rows, msg = await flow_rank.collect_flow_rank(None, D)
    assert rows == 160 and msg == "ok" and seen["dates"][-1] == D


@pytest.mark.asyncio
async def test_prepare_etf_data_universe_and_single_failure_tolerated(monkeypatch):
    async def fake_history(session, today):
        return {"OLD001": "과거ETF"}

    etf_list = [
        {"code": f"E{i:03d}", "name": f"ETF{i}", "amount_million": 1000 - i, "aum_million": 10_000}
        for i in range(250)
    ]
    monkeypatch.setattr(flow_rank, "_load_etf_history_codes", fake_history)
    monkeypatch.setattr(flow_rank, "_fetch_etf_list_blocking", lambda: etf_list)

    captured = {}

    async def fake_hist(codes, end):
        captured["codes"] = codes
        return {c: {D: {"foreign": 1, "institution": 1, "value": 1}} for c in codes}

    monkeypatch.setattr(flow_rank, "_fetch_etf_histories", fake_hist)
    hist, names, aum = await flow_rank.prepare_etf_data(None, D, today=D)
    assert len(captured["codes"]) == flow_rank.ETF_TOP_N + 1  # 상위 200 + 과거 등장 1
    assert "OLD001" in captured["codes"] and "E000" in captured["codes"]
    assert "E249" not in captured["codes"]
    assert names["OLD001"] == "과거ETF" and aum["E000"] == 10_000


@pytest.mark.asyncio
async def test_prepare_etf_data_survives_naver_list_failure(monkeypatch):
    async def fake_history(session, today):
        return {"OLD001": "과거ETF"}

    def boom():
        raise RuntimeError("naver down")

    async def fake_hist(codes, end):
        return {}

    monkeypatch.setattr(flow_rank, "_load_etf_history_codes", fake_history)
    monkeypatch.setattr(flow_rank, "_fetch_etf_list_blocking", boom)
    monkeypatch.setattr(flow_rank, "_fetch_etf_histories", fake_hist)
    hist, names, aum = await flow_rank.prepare_etf_data(None, D, today=D)
    assert names == {"OLD001": "과거ETF"} and aum == {}


def test_registered_in_registry():
    from app.collectors.base import REGISTRY

    assert REGISTRY["flow_rank"] is flow_rank.collect_flow_rank
