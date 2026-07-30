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
    from . import intraday_compaction as _intraday_compaction_collector  # noqa: F401
    from . import investor_warning as _investor_warning_collector  # noqa: F401
    from . import macro as _macro_collector  # noqa: F401
    from . import market_flow as _market_flow_collector  # noqa: F401
    from . import ohlcv as _ohlcv_collector  # noqa: F401
    from . import program_flow as _program_flow_collector  # noqa: F401
    from . import short_selling_market as _short_selling_market_collector  # noqa: F401
    from . import value_rank as _value_rank_collector  # noqa: F401

    # **알파벳순을 의도적으로 깬다 — 지우거나 재정렬하지 말 것(PLAN.md §5.35)**:
    # REGISTRY는 일반 dict라 삽입 순서 = 임포트 순서가 그대로
    # scheduler.py::_run_all_jobs의 순차 실행 순서가 된다(각 잡은 admin.py의
    # 수동 트리거 포함, run_job이 독립 세션/커밋 경계를 갖는다 — collectors/
    # base.py 참고). 아래 세 잡은 같은 날 안에서 실제 의존 관계가 있다:
    # watchlist_sync(오늘의 활동 상위 종목을 watchlist에 등록)
    #   -> stock_ohlcv_watchlist(그 watchlist 종목들의 캔들을 채움)
    #   -> volume_profile_daily(그 캔들로 거래량 프로파일을 계산)
    # 이 순서가 뒤집히면 그날은 "어제 기준 watchlist"로 캔들을 채우거나
    # "어제 기준 캔들"로 프로파일을 계산하게 된다 — 알파벳순(가나다순)을 그대로
    # 따르면 stock_ohlcv_watchlist가 volume_profile_snapshot보다도, watchlist_sync
    # 보다도 먼저 와 버려 이 의존 순서와 정반대가 된다. 그래서 이 셋만 예외적으로
    # 의존 순서대로 묶어서 임포트한다(파일명 자체는 여전히 알파벳 배치 그대로 두고
    # import 문 순서만 바꿈). 각 잡은 그럼에도 방어적으로 "그날 상위 데이터가 아직
    # 없으면 최근 가용 데이터로 자동 대체"하는 self-healing을 갖고 있다(수동 개별
    # 실행이나 향후 재정렬에 대비 — 각 모듈 docstring 참고).
    from . import watchlist_sync as _watchlist_sync_collector  # noqa: F401
    from . import stock_ohlcv_watchlist as _stock_ohlcv_watchlist_collector  # noqa: F401
    from . import volume_profile_snapshot as _volume_profile_snapshot_collector  # noqa: F401
