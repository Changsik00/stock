"""수집기 패키지 — `register_all()`이 모든 collectors.* 모듈을 임포트해 `base.REGISTRY`를
채운다(각 모듈이 `REGISTRY["job"] = collect_fn` 부작용을 갖고 있음).

**단일 진입점으로 통일한 이유(2026-07-21)**: 예전엔 이 임포트 목록이
`routers/admin.py`에만 있었다. `app/worker.py`(평일 18:00 KST 일별 배치 전용
프로세스)가 스케줄러만 띄우고 이 목록을 몰라서 REGISTRY가 비어(`0 jobs
registered`) 배치가 아무 일도 안 하는 버그가 있었다. `register_all()`을
admin.py·worker.py 양쪽이 호출하게 해 앞으로 수집기가 추가돼도 한 곳만 고치면
되게 했다.
"""

from __future__ import annotations


def register_all() -> None:
    from . import breadth as _breadth_collector  # noqa: F401
    from . import etf_master as _etf_master_collector  # noqa: F401
    from . import flow_path as _flow_path_collector  # noqa: F401
    from . import flow_rank as _flow_rank_collector  # noqa: F401
    from . import futures_flow as _futures_flow_collector  # noqa: F401
    from . import group_snapshot as _group_snapshot_collector  # noqa: F401
    from . import macro as _macro_collector  # noqa: F401
    from . import market_flow as _market_flow_collector  # noqa: F401
    from . import ohlcv as _ohlcv_collector  # noqa: F401
    from . import program_flow as _program_flow_collector  # noqa: F401
    from . import value_rank as _value_rank_collector  # noqa: F401
