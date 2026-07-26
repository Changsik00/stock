"""Unit tests for app.collectors.program_stock (Kiwoom ka90013 파싱, PLAN.md §5.29).

Pure functions only — no DB/network. Pins down the module docstring's design
decisions, based on the real probe response shape confirmed 2026-07-26
(clients/kiwoom.py "ka90013(종목일별프로그램매매추이요청) 실측 확정" 절):

1. ka90013 has no arb(차익)/non-arb(비차익) split fields — only a single
   `prm_netprps_amt` total. `parse_program_trade_rows` must leave
   arb_net/non_arb_net as None and only fill total_net.
2. `prm_netprps_amt` encodes negative values with a *doubled* sign character
   (e.g. "--676861" == -676861, observed via real probe) while positives stay
   single-signed ("+182002"). `_parse_signed_amount` must handle both, plus
   plain int/float/None/empty-string inputs defensively (same style as
   collectors/program_flow.py::_parse_int).
3. Rows with an unparseable `dt` are skipped, not crashed on.
"""

from __future__ import annotations

import datetime as dt

from app.collectors.program_stock import _parse_signed_amount, parse_program_trade_rows


def test_parse_signed_amount_handles_doubled_negative_sign():
    # 실측(2026-07-26, 005930 2026-07-24): "--676861" == -676861.
    assert _parse_signed_amount("--676861") == -676861


def test_parse_signed_amount_handles_single_positive_sign():
    assert _parse_signed_amount("+182002") == 182002


def test_parse_signed_amount_handles_plain_int_and_float():
    assert _parse_signed_amount(42) == 42
    assert _parse_signed_amount(-3.0) == -3


def test_parse_signed_amount_handles_none_and_empty_string():
    assert _parse_signed_amount(None) is None
    assert _parse_signed_amount("") is None
    assert _parse_signed_amount("   ") is None


def test_parse_signed_amount_handles_commas():
    assert _parse_signed_amount("--1,234,567") == -1234567


def _row(date_str: str, netprps: str) -> dict:
    # 실측 응답(005930, 2026-07-26 probe)의 필드 부분집합 — 파싱에 쓰지 않는
    # 필드(cur_prc/flu_rt 등)는 생략해도 무방함을 함께 확인하는 셈.
    return {
        "dt": date_str,
        "prm_sell_amt": "2445048",
        "prm_buy_amt": "1768187",
        "prm_netprps_amt": netprps,
        "stex_tp": "KRX",
    }


def test_parse_program_trade_rows_fills_total_net_only():
    data = {
        "stk_daly_prm_trde_trnsn": [
            _row("20260724", "--676861"),
            _row("20260723", "+182002"),
        ]
    }
    rows = parse_program_trade_rows(data, "005930")

    assert rows == [
        {
            "code": "005930",
            "date": dt.date(2026, 7, 24),
            "arb_net": None,
            "non_arb_net": None,
            "total_net": -676861,
        },
        {
            "code": "005930",
            "date": dt.date(2026, 7, 23),
            "arb_net": None,
            "non_arb_net": None,
            "total_net": 182002,
        },
    ]


def test_parse_program_trade_rows_skips_unparseable_date():
    data = {"stk_daly_prm_trde_trnsn": [_row("not-a-date", "+1")]}
    assert parse_program_trade_rows(data, "005930") == []


def test_parse_program_trade_rows_handles_missing_array():
    assert parse_program_trade_rows({"return_code": 0}, "005930") == []
