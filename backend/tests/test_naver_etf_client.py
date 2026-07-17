"""Unit tests for app.clients.naver_etf (etfItemList + etfAnalysis parsing).

No real network involved. Fixtures below are trimmed real responses captured via
curl on 2026-07-18 (see PLAN.md §4.5/§6 Phase 3.5-1):
  - ``finance.naver.com/api/sise/etfItemList.nhn`` (EUC-KR encoded text/plain JSON)
  - ``m.stock.naver.com/api/stock/{code}/etfAnalysis``
"""

from __future__ import annotations

import datetime as dt

import pytest
import requests

from app.clients import naver_etf

# --- etfItemList fixture (real items across tabs: domestic index (1), domestic theme
# w/ single-stock leveraged (2), domestic derivatives (3), mixed (7), foreign (4)) ---
ETF_LIST_ITEMS = [
    {
        "itemcode": "069500",
        "etfTabCode": 1,
        "itemname": "KODEX 200",
        "nowVal": 109000,
        "risefall": "5",
        "changeVal": -7735,
        "changeRate": -6.63,
        "nav": 108739.0,
        "threeMonthEarnRate": 15.599,
        "quant": 17769408,
        "amonut": 1938809,
        "marketSum": 243779,
    },
    {
        "itemcode": "360750",
        "etfTabCode": 4,
        "itemname": "TIGER 미국S&P500",
        "nowVal": 27860,
        "risefall": "5",
        "changeVal": -65,
        "changeRate": -0.23,
        "nav": 27801.0,
        "threeMonthEarnRate": 8.3157,
        "quant": 103606527,
        "amonut": 2895335,
        "marketSum": 201066,
    },
    {
        "itemcode": "133690",
        "etfTabCode": 4,
        "itemname": "TIGER 미국나스닥100",
        "nowVal": 193610,
        "risefall": "5",
        "changeVal": -2890,
        "changeRate": -1.47,
        "nav": 193291.0,
        "threeMonthEarnRate": 12.716,
        "quant": 2335415,
        "amonut": 453189,
        "marketSum": 115043,
    },
    {
        # 실측 2026-07-18: tab2인데 실물 주식(000660 90.58%)을 보유 — 이름 필터로
        # 배제하면 안 되는 대표 사례(거래대금 전체 1위였음).
        "itemcode": "0193T0",
        "etfTabCode": 2,
        "itemname": "KODEX SK하이닉스단일종목레버리지",
        "nowVal": 14585,
        "risefall": "5",
        "changeVal": -4215,
        "changeRate": -22.42,
        "nav": 14498.0,
        "threeMonthEarnRate": None,
        "quant": 284167180,
        "amonut": 4208729,
        "marketSum": 38041,
    },
    {
        # tab3(국내 파생) — 실측: 삼성전자 18.46% 등 실물 바스켓 보유 -> 포함 대상.
        "itemcode": "122630",
        "etfTabCode": 3,
        "itemname": "KODEX 레버리지",
        "nowVal": 120500,
        "risefall": "5",
        "changeVal": -18970,
        "changeRate": -13.6,
        "nav": 121118.0,
        "threeMonthEarnRate": 16.8371,
        "quant": 14259535,
        "amonut": 1724865,
        "marketSum": 61094,
    },
    {
        # tab7(혼합) — 채권혼합형이지만 국내 주식(삼전/하이닉스)을 실보유 -> 후보 포함.
        "itemcode": "0162Z0",
        "etfTabCode": 7,
        "itemname": "RISE 삼성전자SK하이닉스채권혼합50",
        "nowVal": 12345,
        "risefall": "5",
        "changeVal": -100,
        "changeRate": -0.5,
        "nav": 12340.0,
        "threeMonthEarnRate": 3.0,
        "quant": 100,
        "amonut": 144217,
        "marketSum": 4361,
    },
]


def _etckr_body(items: list[dict]) -> bytes:
    import json

    payload = json.dumps({"resultCode": "success", "result": {"etfItemList": items}}, ensure_ascii=False)
    return payload.encode("euc-kr")


class _FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200, json_data: dict | None = None, text: str = ""):
        self.content = content
        self.status_code = status_code
        self._json_data = json_data
        self.text = text or (content.decode("euc-kr", errors="replace") if content else "")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._json_data


def test_fetch_etf_list_decodes_euckr_and_normalizes_units(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        assert url == naver_etf.LIST_URL
        assert headers["User-Agent"]
        return _FakeResponse(_etckr_body(ETF_LIST_ITEMS))

    monkeypatch.setattr(naver_etf.requests, "get", fake_get)

    items = naver_etf.fetch_etf_list()

    assert len(items) == 6
    kodex200 = items[0]
    assert kodex200["code"] == "069500"
    assert kodex200["name"] == "KODEX 200"
    assert kodex200["tab_code"] == 1
    assert kodex200["amount_million"] == 1938809  # amonut as-is (already 백만원)
    # marketSum(억원) * 100 -> 백만원. 243779 * 100 = 24377900.
    assert kodex200["aum_million"] == 24377900


def test_fetch_etf_list_raises_on_empty_result(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return _FakeResponse(_etckr_body([]))

    monkeypatch.setattr(naver_etf.requests, "get", fake_get)

    with pytest.raises(naver_etf.NaverEtfError):
        naver_etf.fetch_etf_list()


def _raw_items():
    return [
        {
            "code": it["itemcode"],
            "name": it["itemname"],
            "tab_code": it["etfTabCode"],
            "nav": it["nav"],
            "now_value": it["nowVal"],
            "quant": it["quant"],
            "amount_million": it["amonut"],
            "aum_million": it["marketSum"] * 100,
        }
        for it in ETF_LIST_ITEMS
    ]


def test_select_domestic_equity_targets_includes_tabs_1237_no_name_filter():
    """유니버스 개정(2026-07-18): tab 1/2/3/7 전부, 이름 필터 폐지.

    단일종목 레버리지(tab2)·KODEX 레버리지(tab3)·채권혼합(tab7)이 모두 후보에
    남고, 해외주식(tab4)만 제외된다. 정렬은 거래대금 내림차순.
    """
    targets = naver_etf.select_domestic_equity_targets(_raw_items(), top_n=300)

    codes = [t["code"] for t in targets]
    assert "360750" not in codes  # tab 4 (해외주식)
    assert "133690" not in codes  # tab 4 (해외주식)
    # 거래대금 내림차순: 단일종목레버리지(4.2조) > KODEX200 > KODEX레버리지 > 채권혼합
    assert codes == ["0193T0", "069500", "122630", "0162Z0"]


def test_select_domestic_equity_targets_respects_top_n_cut():
    targets = naver_etf.select_domestic_equity_targets(_raw_items(), top_n=2)
    assert [t["code"] for t in targets] == ["0193T0", "069500"]


@pytest.mark.parametrize(
    "raw,expected_million",
    [
        ("24조 3,779억", 24_377_900),
        ("703억", 70_300),
        ("-72.9억", -7_290),
        ("6.79억", 679),
        ("3조 909억", 3_090_900),
        ("-", None),
        (None, None),
        ("0", 0),
        ("295억", 29_500),
    ],
)
def test_parse_won_string_to_million(raw, expected_million):
    assert naver_etf.parse_won_string_to_million(raw) == expected_million


# --- etfAnalysis fixture (trimmed real KODEX 200 response, 2026-07-18) ---
ETF_ANALYSIS_KODEX200 = {
    "itemCode": "069500",
    "itemName": "KODEX 200",
    "marketValue": "24조 3,779억",
    "totalNav": "26조 1,995억",
    "nav": 117145.18,
    "cumulativeNetInflowList": {
        "referenceDate": "2026.07.15",
        "cumulativeNetInflow1d": "703억",
        "cumulativeNetInflow1w": "8,684억",
        "cumulativeNetInflow1m": "7,240억",
        "cumulativeNetInflow3m": "-7,321억",
        "cumulativeNetInflow6m": "3조 909억",
        "cumulativeNetInflowYtd": "2조 5,105억",
        "cumulativeNetInflow1y": "4조 1,357억",
    },
    "countryPortfolioList": [
        {"detailTypeCode": "KR", "weight": 98.98},
        {"detailTypeCode": "US", "weight": 0.0},
    ],
    "etfTop10MajorConstituentAssets": [
        {"seq": 1, "itemCode": "005930", "itemName": "삼성전자", "stockCount": "6,978", "etfWeight": "32.73%"},
        {"seq": 2, "itemCode": "000660", "itemName": "SK하이닉스", "stockCount": "829", "etfWeight": "28.09%"},
        {"seq": 3, "itemCode": "402340", "itemName": "SK스퀘어", "stockCount": "138", "etfWeight": "3.08%"},
    ],
}

# Foreign-holding ETF: itemCode empty + etfWeight "-" for every row (real TIGER 미국S&P500 shape).
ETF_ANALYSIS_FOREIGN = {
    "itemCode": "360750",
    "itemName": "TIGER 미국S&P500",
    "marketValue": "20조 1,066억",
    "totalNav": "20조 545억",
    "nav": 27810.92,
    "cumulativeNetInflowList": {
        "referenceDate": "2026.07.15",
        "cumulativeNetInflow1d": "-",
        "cumulativeNetInflow1w": "2,548억",
        "cumulativeNetInflow1m": "9,143억",
        "cumulativeNetInflow3m": "2조 8,158억",
        "cumulativeNetInflow6m": "4조 4,729억",
        "cumulativeNetInflowYtd": "5조 3,783억",
        "cumulativeNetInflow1y": "8조 1,827억",
    },
    "etfTop10MajorConstituentAssets": [
        {"seq": 1, "itemCode": "", "itemName": "NVIDIA CORP", "stockCount": "349", "etfWeight": "-"},
        {"seq": 2, "itemCode": "", "itemName": "APPLE INC", "stockCount": "211", "etfWeight": "-"},
    ],
}


def test_fetch_etf_analysis_uses_mobile_endpoint_and_user_agent(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        assert url == naver_etf.ANALYSIS_URL.format(code="069500")
        assert headers["User-Agent"]
        return _FakeResponse(b"", json_data=ETF_ANALYSIS_KODEX200)

    monkeypatch.setattr(naver_etf.requests, "get", fake_get)

    data = naver_etf.fetch_etf_analysis("069500")
    assert data["itemCode"] == "069500"


def test_fetch_etf_analysis_raises_on_unexpected_response(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return _FakeResponse(b"", json_data={"error": "boom"}, text='{"error": "boom"}')

    monkeypatch.setattr(naver_etf.requests, "get", fake_get)

    with pytest.raises(naver_etf.NaverEtfError):
        naver_etf.fetch_etf_analysis("069500")


def test_parse_top10_holdings_domestic():
    holdings = naver_etf.parse_top10_holdings(ETF_ANALYSIS_KODEX200)

    assert holdings == [
        {"stock_code": "005930", "stock_name": "삼성전자", "weight": 32.73, "shares": 6978},
        {"stock_code": "000660", "stock_name": "SK하이닉스", "weight": 28.09, "shares": 829},
        {"stock_code": "402340", "stock_name": "SK스퀘어", "weight": 3.08, "shares": 138},
    ]


def test_parse_top10_holdings_skips_rows_without_itemcode_or_weight():
    # Foreign-holding shape: itemCode empty + etfWeight "-" for every row -> all skipped.
    holdings = naver_etf.parse_top10_holdings(ETF_ANALYSIS_FOREIGN)
    assert holdings == []


# --- 단일종목 레버리지 (실측 2026-07-18, KODEX SK하이닉스단일종목레버리지 0193T0) ---
# 실물 주식 + 원화현금 + 개별선물 구조. 주식 행만 살아남아야 한다.
ETF_ANALYSIS_SINGLE_STOCK_LEVERAGED = {
    "itemCode": "0193T0",
    "itemName": "KODEX SK하이닉스단일종목레버리지",
    "etfTop10MajorConstituentAssets": [
        {"seq": 1, "itemCode": "000660", "itemName": "SK하이닉스", "stockCount": "205", "etfWeight": "90.58%"},
        {"seq": 2, "itemCode": "", "itemName": "원화현금", "stockCount": "-", "etfWeight": "9.42%"},
        {"seq": 3, "itemCode": "", "itemName": "2026-08 SK하이닉스개별선물", "stockCount": "25", "etfWeight": "-"},
    ],
}

# --- 인버스형 (실측 2026-07-18, KODEX 인버스 114800) — 주식 보유 없음 -> 빈 결과가 정상 ---
ETF_ANALYSIS_INVERSE = {
    "itemCode": "114800",
    "itemName": "KODEX 인버스",
    "etfTop10MajorConstituentAssets": [
        {"seq": 1, "itemCode": "", "itemName": "원화현금", "stockCount": "-", "etfWeight": "100.00%"},
        {"seq": 2, "itemCode": "", "itemName": "설정현금액", "stockCount": "-", "etfWeight": "100.00%"},
        {"seq": 3, "itemCode": "", "itemName": "2026-09 코스피200지수선물", "stockCount": "7", "etfWeight": "-"},
    ],
}

# --- 채권혼합형 데이터 한계 (실측 2026-07-18, RISE 삼성전자SK하이닉스채권혼합50 0162Z0) ---
# 주식 행에 itemCode는 있으나 etfWeight가 "-" -> 비중을 알 수 없어 제외(문서화된 한계).
ETF_ANALYSIS_BOND_MIXED_NO_WEIGHT = {
    "itemCode": "0162Z0",
    "itemName": "RISE 삼성전자SK하이닉스채권혼합50",
    "etfTop10MajorConstituentAssets": [
        {"seq": 1, "itemCode": "005930", "itemName": "삼성전자", "stockCount": "1,269", "etfWeight": "-"},
        {"seq": 2, "itemCode": "000660", "itemName": "SK하이닉스", "stockCount": "170", "etfWeight": "-"},
        {"seq": 3, "itemCode": "", "itemName": "국고02125-2706(17-3)", "stockCount": "-", "etfWeight": "-"},
    ],
}


def test_parse_top10_holdings_single_stock_leveraged_keeps_real_stock():
    holdings = naver_etf.parse_top10_holdings(ETF_ANALYSIS_SINGLE_STOCK_LEVERAGED)
    assert holdings == [
        {"stock_code": "000660", "stock_name": "SK하이닉스", "weight": 90.58, "shares": 205},
    ]


def test_parse_top10_holdings_inverse_has_no_stock_rows():
    # 인버스/선물형은 look-through 기여 0이 정상 동작 — 현금/선물 행은 코드가 없어 전부 제외.
    assert naver_etf.parse_top10_holdings(ETF_ANALYSIS_INVERSE) == []


def test_parse_top10_holdings_bond_mixed_without_weight_drops_out():
    # 비중 없는 주식 행은 계산 불가라 제외 — holdings가 비어 유니버스에서 자연 탈락.
    assert naver_etf.parse_top10_holdings(ETF_ANALYSIS_BOND_MIXED_NO_WEIGHT) == []


def test_parse_net_inflow_snapshot_uses_1d_field_only():
    snap = naver_etf.parse_net_inflow_snapshot(ETF_ANALYSIS_KODEX200)

    assert snap["reference_date"] == dt.date(2026, 7, 15)
    assert snap["net_inflow_1d_million"] == 70_300
    # raw keeps the full dict so callers could use other periods later if needed.
    assert snap["raw"]["cumulativeNetInflow1y"] == "4조 1,357억"


def test_parse_net_inflow_snapshot_handles_missing_1d_value():
    snap = naver_etf.parse_net_inflow_snapshot(ETF_ANALYSIS_FOREIGN)
    assert snap["net_inflow_1d_million"] is None
    assert snap["reference_date"] == dt.date(2026, 7, 15)


def test_parse_nav_aum():
    result = naver_etf.parse_nav_aum(ETF_ANALYSIS_KODEX200)
    assert result["nav"] == 117145.18
    assert result["aum_million"] == 24_377_900
