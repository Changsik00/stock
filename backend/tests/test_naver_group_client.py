"""Unit tests for app.clients.naver_group (sise_group.naver / sise_group_detail.naver
parsing, PLAN.md §4.6/§6 3.6-3).

No real network involved. UPJONG_BODY/DETAIL_BODY_UPJONG/DETAIL_BODY_THEME are trimmed
samples of actual `finance.naver.com/sise/sise_group*.naver` responses, captured via
requests on 2026-07-18 (rows kept verbatim, unrelated markup trimmed) — see
clients/naver_group.py module docstring for the full real-response verification notes
(list: 79 upjong / 266 theme rows on a single page, no pagination, no 거래대금/시총
columns; detail: 거래대금 컬럼 있음(백만원 단위), 시총 컬럼 없음, 페이징 없음, 테마
타입만 "테마 편입 사유" 칸이 하나 더 있지만 숫자 컬럼 인덱스에는 영향 없음).
"""

from __future__ import annotations

import pytest
import requests

from app.clients import naver_group

UPJONG_BODY = """
<table summary="업종별 전일대비 시세에 관한 표이며 등락현황 정보를 제공합니다." cellpadding="0" cellspacing="0" class="type_1" style="table-layout:fixed">
	<tr>
		<th rowspan="2" style="border-left:none;">업종명</th>
		<th rowspan="2">전일대비</th>
	</tr>
	<tr><td colspan="7" class="blank_07"></td></tr>
	<tr>
		<td style="padding-left:10px;"><a href="/sise/sise_group_detail.naver?type=upjong&no=332">문구류</a></td>
		<td class="number">
			<span class="tah p11 red01">
			+8.27%
			</span>
		</td>
		<td class="number">1</td>
		<td class="number">1</td>
		<td class="number">0</td>
		<td class="number">0</td>
		<td class="tc"><div class="graph_type_1" style="width:80px;"><div class="graph_bar" style="width:82%;"><span class="graph_txt">82%</span></div></div></td>
	</tr>
	<tr>
		<td style="padding-left:10px;"><a href="/sise/sise_group_detail.naver?type=upjong&no=278">반도체와반도체장비</a></td>
		<td class="number">
			<span class="tah p11 nv01">
			-10.07%
			</span>
		</td>
		<td class="number">30</td>
		<td class="number">3</td>
		<td class="number">0</td>
		<td class="number">27</td>
		<td class="tc"><div class="graph_type_1" style="width:80px;"><div class="graph_bar" style="width:10%;"><span class="graph_txt">10%</span></div></div></td>
	</tr>
</table>
"""

# 실측(2026-07-18) https://finance.naver.com/sise/sise_group_detail.naver?type=upjong&no=278
# 응답 중 두 종목 행만 남기고 트리밍(업종 타입 — 테마 편입 사유 칸 없음). 거래대금
# (인덱스 6번째 number 컬럼) 값 그대로: 122, 8076.
DETAIL_BODY_UPJONG = """
<table summary="업종별 시세 리스트" cellpadding="0" cellspacing="0" class="type_5">
<thead>
<tr style="height:29px">
<th>종목명</th><th>현재가</th><th>전일비</th><th>등락률</th><th>매수호가</th><th>매도호가</th><th>거래량</th><th>거래대금</th><th>전일거래량</th><th>토론</th>
</tr>
</thead>
<tbody>
<tr onMouseOver="mouseOver(this)" onMouseOut="mouseOut(this)" >
	<td class="name"><div class="name_area"><a href="/item/main.naver?code=365590">하이딥</a> <span class="dot">*</span></div></td>
	<td class="number" style="padding-right:15px;">1,068</td>
	<td class="number" style="padding-right:15px;">
		<em class="bu_p bu_pup"><span class="blind">상승</span></em><span class="tah p11 red02">
		102
		</span>
	</td>
	<td class="number" style="padding-right:20px;">
		<span class="tah p11 red01">
		+10.56%
		</span>
	</td>
	<td class="number" style="padding-right:20px;">1,048</td>
	<td class="number" style="padding-right:20px;">1,068</td>
	<td class="number" style="padding-right:20px;">124,044</td>
	<td class="number" style="padding-right:20px;">122</td>
	<td class="number" style="padding-right:20px;">136,604</td>
	<td class="center"><a href="/item/board.naver?code=365590">토론</a></td>
</tr>
<tr onMouseOver="mouseOver(this)" onMouseOut="mouseOut(this)" >
	<td class="name"><div class="name_area"><a href="/item/main.naver?code=413300">티엘엔지니어링</a> <span class="dot"></span></div></td>
	<td class="number" style="padding-right:15px;">2,380</td>
	<td class="number" style="padding-right:15px;">
		<em class="bu_p bu_pup"><span class="blind">상승</span></em><span class="tah p11 red02">
		220
		</span>
	</td>
	<td class="number" style="padding-right:20px;">
		<span class="tah p11 red01">
		+10.19%
		</span>
	</td>
	<td class="number" style="padding-right:20px;">0</td>
	<td class="number" style="padding-right:20px;">2,350</td>
	<td class="number" style="padding-right:20px;">50,000</td>
	<td class="number" style="padding-right:20px;">8,076</td>
	<td class="number" style="padding-right:20px;">10,000</td>
	<td class="center"><a href="/item/board.naver?code=413300">토론</a></td>
</tr>
<tr><td colspan="12" class="blank_09"></td></tr>
<tr><td colspan="12" class="division_line_1"></td></tr>
</tbody>
</table>
"""

# 실측(2026-07-18) https://finance.naver.com/sise/sise_group_detail.naver?type=theme&no=30
# 응답 중 한 종목 행만 남기고 트리밍(테마 타입 — "테마 편입 사유" 칸이 종목명 뒤에
# 하나 더 있음, class="number"가 아니라 숫자 컬럼 인덱스에는 영향 없음을 검증).
DETAIL_BODY_THEME = """
<table summary="업종별 시세 리스트" cellpadding="0" cellspacing="0" class="type_5">
<thead>
<tr style="height:29px">
<th colspan="2">종목명</th><th>현재가</th><th>전일비</th><th>등락률</th><th>매수호가</th><th>매도호가</th><th>거래량</th><th>거래대금</th><th>전일거래량</th><th>토론</th>
</tr>
</thead>
<tbody>
<tr onMouseOver="mouseOver(this)" onMouseOut="mouseOut(this)" >
	<td class="name"><div class="name_area"><a href="/item/main.naver?code=023790">동일스틸럭스</a> <span class="dot">*</span></div></td>
	<td>
		<div class="theme_info_area">
			<a href="javascript:;" class="btn_history">사유</a>
		</div>
	</td>
	<td class="number" style="padding-right:15px;">1,583</td>
	<td class="number" style="padding-right:15px;">
		<em class="bu_p bu_pup2"><span class="blind">상한가</span></em><span class="tah p11 red02">
		365
		</span>
	</td>
	<td class="number" style="padding-right:20px;">
		<span class="tah p11 red01">
		+29.97%
		</span>
	</td>
	<td class="number" style="padding-right:20px;">1,583</td>
	<td class="number" style="padding-right:20px;">0</td>
	<td class="number" style="padding-right:20px;">5,359,336</td>
	<td class="number" style="padding-right:20px;">8,076</td>
	<td class="number" style="padding-right:20px;">122,620</td>
	<td class="center"><a href="/item/board.naver?code=023790">토론</a></td>
</tr>
<tr><td colspan="12" class="blank_09"></td></tr>
<tr><td colspan="12" class="division_line_1"></td></tr>
</tbody>
</table>
"""


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


def test_fetch_group_snapshot_parses_name_and_signed_rate(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return _FakeResponse(UPJONG_BODY)

    monkeypatch.setattr(naver_group.requests, "get", fake_get)

    rows = naver_group.fetch_group_snapshot("upjong")

    assert captured["url"] == naver_group.LIST_URL
    assert captured["params"] == {"type": "upjong"}
    assert captured["headers"]["User-Agent"]

    assert rows == [
        {"name": "문구류", "change_rate": 8.27, "no": 332},
        {"name": "반도체와반도체장비", "change_rate": -10.07, "no": 278},
    ]


def test_fetch_group_snapshot_rejects_unknown_group_type():
    with pytest.raises(ValueError):
        naver_group.fetch_group_snapshot("sector")


def test_fetch_group_snapshot_raises_on_no_rows(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse("<div>no data here</div>")

    monkeypatch.setattr(naver_group.requests, "get", fake_get)

    with pytest.raises(naver_group.NaverGroupError):
        naver_group.fetch_group_snapshot("theme")


def test_fetch_group_snapshot_uses_theme_type_param(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["params"] = params
        return _FakeResponse(UPJONG_BODY)

    monkeypatch.setattr(naver_group.requests, "get", fake_get)

    naver_group.fetch_group_snapshot("theme")

    assert captured["params"] == {"type": "theme"}


def test_fetch_group_value_sums_trade_value_upjong(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(DETAIL_BODY_UPJONG)

    monkeypatch.setattr(naver_group.requests, "get", fake_get)

    value = naver_group.fetch_group_value("upjong", 278)

    assert captured["url"] == naver_group.DETAIL_URL
    assert captured["params"] == {"type": "upjong", "no": 278}
    assert value == 122 + 8076


def test_fetch_group_value_sums_trade_value_theme_ignores_reason_column(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse(DETAIL_BODY_THEME)

    monkeypatch.setattr(naver_group.requests, "get", fake_get)

    value = naver_group.fetch_group_value("theme", 30)

    assert value == 8076


def test_fetch_group_value_raises_on_no_constituent_rows(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse("<div>no data here</div>")

    monkeypatch.setattr(naver_group.requests, "get", fake_get)

    with pytest.raises(naver_group.NaverGroupError):
        naver_group.fetch_group_value("upjong", 999999)


def test_fetch_group_value_rejects_unknown_group_type():
    with pytest.raises(ValueError):
        naver_group.fetch_group_value("sector", 1)
