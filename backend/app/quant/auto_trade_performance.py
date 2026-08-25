"""자동/수동 진입 방식별 성과 집계 (PLAN.md §5.76).

사용자가 "내가 직접 한 게 수익률이 더 좋다면 자동매매 프로그램의 존폐
유무가 중요하다"고 물어, `AutoTradeLog`를 매번 손으로 SQL 짜서 확인하는
대신 상시 집계로 만들었다. **이 모듈은 판단 문구를 만들지 않는다** —
자동/수동 각각의 승률·평균수익률 숫자만 계산할 뿐, "자동매매를 꺼라/
켜라" 같은 결론은 어디에도 없다(house rule §5, 종합 판단은 사용자 몫).

## pairing 원리

이 전략은 한 번에 포지션 하나만 허용한다(§5.54) — 그래서 로그를 ts
오름차순으로 훑으면서 진입(`entry`|`manual_entry`) 하나를 열고, 그 다음
청산(`exit_*`) 이벤트에서 닫는 단순 상태 기계로 전체 거래를 복원할 수
있다. 정상적으로는 항상 진입→청산이 번갈아 나온다 — 어긋나면(청산인데
열린 진입이 없음, 또는 진입인데 이미 열린 진입이 있음) 그 건은
`anomalies`에 남기고 통계에서 제외한다(조용히 틀린 숫자를 내지 않는다).

## KNOWN_BAD_LOG_IDS — 딱 하나뿐인 예외

2026-08-14 15:20 KST EOD강제청산(log id=1920)이 실제로는 체결되지 않았는데
(PLAN.md §5.71 실사고 — 실계좌 재조회로 미체결 확인, 5일간 손절 감시가
꺼져 있었음) 로그엔 `exit_eod_forced`로 남아 있다. 이걸 그대로 pairing에
넣으면 그 거래가 +0.19% 승리로 잘못 집계되고, 5일 뒤 진짜 청산(8/19
`exit_stop_loss`, -5.52%)은 짝 잃은 청산(orphan)이 된다. id=1920을
제외하면 — 그 사이 새 진입 이벤트가 전혀 없었으므로(§5.71 당시 상태가
계속 idle로 잘못 남아 있었을 뿐, 실제 매수는 없었음) — 8/14 진입이
자연스럽게 8/19 진짜 청산과 짝지어져 실제 경제적 결과가 정확히 복원된다.
이 예외는 §5.71로 이미 고쳐져 재발하지 않는다 — 일반화된 이상탐지
시스템을 만들지 않는다(과설계 금지).
"""

from __future__ import annotations

from typing import Any

KNOWN_BAD_LOG_IDS = frozenset({1920})

ENTRY_EVENT_TYPES = frozenset({"entry", "manual_entry"})
EXIT_EVENT_TYPES = frozenset(
    {"exit_stop_loss", "exit_trail", "exit_eod_forced", "exit_flow_reversal", "exit_manual"}
)

_EMPTY_GROUP: dict[str, Any] = {
    "n": 0,
    "win_rate_pct": None,
    "avg_return_pct": None,
    "cumulative_return_pct": 0.0,
}


def _summarize_group(trades: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(trades)
    if n == 0:
        return dict(_EMPTY_GROUP)
    returns = [t["pct_return"] for t in trades]
    wins = [r for r in returns if r > 0]
    return {
        "n": n,
        "win_rate_pct": round(len(wins) / n * 100, 2),
        "avg_return_pct": round(sum(returns) / n, 4),
        "cumulative_return_pct": round(sum(returns), 4),
    }


def summarize_by_entry_source(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """rows: ts 오름차순 ``{"id","ts","event_type","price"}`` 리스트(entry/exit류만
    포함해도 되고, 다른 타입이 섞여 있어도 무시한다 — 호출부가 미리 거르지
    않아도 안전).

    Returns ``{"by_source": {"auto": {...}, "manual": {...}}, "trades": [...],
    "anomalies": [...], "open_position": {...} | None}`` — ``by_source``의 각
    값은 ``{"n","win_rate_pct","avg_return_pct","cumulative_return_pct"}``.
    """
    open_entry: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []

    for row in rows:
        if row["id"] in KNOWN_BAD_LOG_IDS:
            continue

        event_type = row["event_type"]
        if event_type in ENTRY_EVENT_TYPES:
            if open_entry is not None:
                anomalies.append(
                    {
                        "type": "unexpected_second_entry",
                        "id": row["id"],
                        "ts": row["ts"],
                        "detail": f"이미 열린 진입(id={open_entry['id']})이 있는데 새 진입이 또 나옴",
                    }
                )
                # 새 진입을 현재 열린 포지션으로 취급(더 최신 정보를 신뢰) —
                # 이전 열린 진입은 짝을 못 찾은 채 버려진다(이미 anomalies에 기록됨).
            open_entry = {
                "id": row["id"],
                "ts": row["ts"],
                "price": row["price"],
                "source": "manual" if event_type == "manual_entry" else "auto",
            }
        elif event_type in EXIT_EVENT_TYPES:
            if open_entry is None:
                anomalies.append(
                    {
                        "type": "orphan_exit",
                        "id": row["id"],
                        "ts": row["ts"],
                        "detail": "이 청산과 짝지을 열린 진입을 찾지 못함",
                    }
                )
                continue
            pct_return = round((row["price"] - open_entry["price"]) / open_entry["price"] * 100, 4)
            trades.append(
                {
                    "entry_source": open_entry["source"],
                    "entry_id": open_entry["id"],
                    "entry_ts": open_entry["ts"],
                    "entry_price": open_entry["price"],
                    "exit_id": row["id"],
                    "exit_ts": row["ts"],
                    "exit_price": row["price"],
                    "exit_reason": event_type,
                    "pct_return": pct_return,
                }
            )
            open_entry = None

    open_position = None
    if open_entry is not None:
        open_position = {
            "entry_id": open_entry["id"],
            "entry_ts": open_entry["ts"],
            "entry_price": open_entry["price"],
            "entry_source": open_entry["source"],
        }

    by_source = {
        "auto": _summarize_group([t for t in trades if t["entry_source"] == "auto"]),
        "manual": _summarize_group([t for t in trades if t["entry_source"] == "manual"]),
    }

    return {
        "by_source": by_source,
        "trades": trades,
        "anomalies": anomalies,
        "open_position": open_position,
    }
