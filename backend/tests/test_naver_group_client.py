"""Unit tests for app.clients.naver_group (sise_group.naver parsing, PLAN.md §4.6/§6 3.6-3).

No real network involved. UPJONG_BODY is a trimmed sample of the actual
`finance.naver.com/sise/sise_group.naver?type=upjong` response, captured via requests
on 2026-07-18 (rows kept verbatim, unrelated markup trimmed) — see clients/naver_group.py
module docstring for the full real-response verification notes (79 upjong / 266 theme
rows on a single page, no pagination, no 거래대금/시총 columns on this page).
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
        {"name": "문구류", "change_rate": 8.27},
        {"name": "반도체와반도체장비", "change_rate": -10.07},
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
