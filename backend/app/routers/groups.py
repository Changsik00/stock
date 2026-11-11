"""GET /api/groups — 업종/테마별 최신(또는 지정일) 등락률 스냅샷 (PLAN.md §4.6/§6 3.6-3).

DB 전용 조회다(§5.4 "DB 캐싱 우선") — collectors/group_snapshot.py가 미리 적재해 둔
group_snapshot 테이블을 그대로 읽어 반환할 뿐, 이 라우터에서 네이버를 직접 호출하지
않는다.

**주의(작업 지시)**: 이 라우터는 아직 ``main.py``에 등록하지 않는다 — 병렬로 진행
중인 다른 작업과의 main.py 충돌을 피하기 위해 통합 단계에서 별도로 배선한다. 그래서
테스트도 이 모듈의 ``router``를 ``TestClient``/``ASGITransport``에 직접 include해서
검증한다(tests/test_groups_router.py 참고).

## GET /api/groups/live (PLAN.md §4.7 3단 갱신 주기, 2026-07-20 장중 실측)

장중 실측 결과 ``sise_group.naver`` 목록 페이지의 등락률(change_rate)이 장중
시세에 맞춰 갱신됨을 확인 — 5~10분 캐시로 편입한다. **그룹 상세 페이지(구성
종목 거래대금 합산, clients/naver_group.fetch_group_value)는 그룹당 1회씩
345회 호출에 2~3분이 걸려 5~10분 주기에 맞지 않으므로 라이브 엔드포인트에서는
호출하지 않는다** — 목록 페이지(그룹 타입당 1회, 총 2회)만 재조회해 등락률만
갱신하고, value(거래대금)/market_sum은 항상 null로 둔다(호출자가 필요하면
직전 EOD `/api/groups` 스냅샷의 value와 이름 기준으로 병합 — 이 라우터는 병합
책임이 없다, 프런트 DashboardPage가 담당). DB(group_snapshot)에는 쓰지 않는다
(§3.5 원칙).

**장 마감 게이트(2026-07-20 도입, 2026-11-11 이슈 #3로 수정)**: 원래는 장 마감이면
네이버를 아예 호출하지 않고 마지막 캐시를 재사용했으나, 15:30 직전 값이 굳는 문제가
있어(실측 업종 1위 8.40 → 9.12) 이제 "확정 기준 시각"(가장 최근에 지난 18:05 KST)
이후 조회된 캐시가 있을 때만 재호출 없이 ``market_closed: true``로 재사용하고, 그렇지
않으면 TTL마다 재조회한다. 마감 재조회 실패는 502가 아니라 기존 캐시/빈 rows 폴백이다
(``_warm_groups_live`` docstring 참고).

## GET /api/groups/top-stocks (PLAN.md §5.12 "업종·테마 트리맵 클릭 → 대장 종목
TOP10", 2026-07-22)

트리맵 박스를 클릭했을 때 그 그룹의 구성 종목 중 거래대금 상위 종목을 보여주는
참고용 탐색 기능이다(순위·사실 나열이지 매매 추천이 아니다 — §5 "중립 계기판"
원칙). 장 마감 여부와 무관하게 항상 캐시된 값을 재사용해도 되는 기능이라(실시간
시황 카드가 아님) 이 엔드포인트에는 ``is_market_closed`` 게이트가 없다.

그룹 상세 조회(``naver_group.fetch_group_constituents``)에는 ``no``(그룹 내부
ID)가 필요한데 이건 목록 페이지(``fetch_group_snapshot``) 호출로만 얻을 수 있고
``GroupSnapshot`` DB 모델에도 없다 — 이름→no 매핑을 ``_group_name_to_no_cache``에
그룹 타입별로 캐싱한다(TTL 1일, 목록이 자주 안 바뀌므로 길게 잡아도 된다).
캐시 미스 시 목록을 통째로 재조회해(``fetch_group_snapshot``, 페이징 없이 한
번에 79~266개) 채운다.

구성 종목 상세 조회 결과는 ``(type, name, limit)``을 키로 5분(``TOP_STOCKS_TTL_
SECONDS``) 캐시한다 — 트리맵을 반복 클릭해도 네이버에 매번 새로 요청하지
않기 위함(``_groups_live_cache``와 같은 TTL+락 패턴).

Returns ``{"type": ..., "name": ..., "rows": [{"code","name","change_rate",
"value"}, ...], "cached_at": iso8601}``. 목록에 없는 이름이면 404, 상세 조회
자체가 실패하면(NaverGroupError) 502.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients import naver_group
from ..db import get_session
from ..market_hours import CLOSED_REFETCH_TTL_SECONDS, is_market_closed, kst_now, required_confirm_kst
from ..models import GroupSnapshot

logger = logging.getLogger(__name__)

router = APIRouter(tags=["groups"])

GROUP_TYPES = {"upjong", "theme"}

# 1분 장중 라이브 캐시 TTL — collectors/live_refresh.py의 60초 인터벌 잡과 맞춘다.
# 2026-07-21(§5.5-2) 7분→1분: 목록 페이지 1~2회 조회뿐인 가벼운 호출이라 값 비용
# 없이 당길 수 있다고 판단해 프런트 폴링 주기만 먼저 옮겼는데, 이 TTL 상수와
# live_refresh.py의 스케줄러 잡 배정을 같이 옮기는 걸 빠뜨려 실제로는 계속 7분
# 캐시로 응답하는 회귀가 있었다(§5.6 후속 사용자 지적으로 재발견, 90초 간격
# 재호출에도 byte-for-byte 동일 응답으로 실측 확인). 이번에 TTL도 함께 맞춘다.
LIVE_TTL_SECONDS = 60

# "fetched_at": 소스에서 마지막으로 성공 조회한 KST 시각(aware datetime) — 마감 후
# 확정 기준 시각(18:05) 이후 조회분인지 판정용(payload 스키마에는 넣지 않는다).
_groups_live_cache: dict[str, dict] = {
    "upjong": {"ts": 0.0, "data": None, "fetched_at": None},
    "theme": {"ts": 0.0, "data": None, "fetched_at": None},
}
_groups_live_cache_lock = asyncio.Lock()

# 이름→no 매핑 캐시(그룹 상세 조회에 no가 필요, top-stocks 전용) — 목록이 자주
# 안 바뀌므로 TTL을 하루로 길게 잡는다(모듈 docstring 참고).
NAME_TO_NO_TTL_SECONDS = 86_400

_group_name_to_no_cache: dict[str, dict] = {
    "upjong": {"ts": 0.0, "data": None},
    "theme": {"ts": 0.0, "data": None},
}
_group_name_to_no_cache_lock = asyncio.Lock()

# 그룹 상세(구성 종목 TOP N) 캐시 — (type, name, limit) 키, 5분 TTL(모듈 docstring
# 참고, 반복 클릭으로 네이버에 과호출하지 않기 위함).
TOP_STOCKS_TTL_SECONDS = 300

_group_top_stocks_cache: dict[tuple[str, str, int], dict] = {}
_group_top_stocks_cache_lock = asyncio.Lock()


def _fetch_group_list_blocking(group_type: str) -> list[dict]:
    return naver_group.fetch_group_snapshot(group_type)


def _fetch_group_constituents_blocking(group_type: str, no: int, limit: int) -> list[dict]:
    return naver_group.fetch_group_constituents(group_type, no, limit=limit)


async def _warm_group_name_to_no(group_type: str) -> dict[str, int]:
    """group_type의 이름→no 매핑을 채우고 반환한다 — TTL 만료 전이면 캐시 그대로
    돌려준다(``_warm_groups_live``와 같은 TTL+락 패턴)."""
    now = time.monotonic()
    async with _group_name_to_no_cache_lock:
        entry = _group_name_to_no_cache[group_type]
        cached = entry["data"]
        if cached is not None and (now - entry["ts"]) < NAME_TO_NO_TTL_SECONDS:
            return cached

        try:
            rows = await asyncio.to_thread(_fetch_group_list_blocking, group_type)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"group list fetch failed: {str(e)[:200]}") from e

        mapping = {r["name"]: r["no"] for r in rows}
        entry["data"] = mapping
        entry["ts"] = now
        return mapping


async def _warm_top_stocks(group_type: str, name: str, no: int, limit: int) -> dict:
    """(group_type, name, limit) 캐시를 채우고 payload를 반환한다."""
    now = time.monotonic()
    cache_key = (group_type, name, limit)
    async with _group_top_stocks_cache_lock:
        entry = _group_top_stocks_cache.get(cache_key)
        cached = entry["data"] if entry is not None else None
        if cached is not None and (now - entry["ts"]) < TOP_STOCKS_TTL_SECONDS:
            return cached

        try:
            rows = await asyncio.to_thread(
                _fetch_group_constituents_blocking, group_type, no, limit
            )
        except naver_group.NaverGroupError as e:
            raise HTTPException(502, f"group constituents fetch failed: {str(e)[:200]}") from e

        payload = {
            "type": group_type,
            "name": name,
            "rows": rows,
            "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        _group_top_stocks_cache[cache_key] = {"ts": now, "data": payload}
        return payload


async def _warm_groups_live(group_type: str) -> dict:
    """groups/live 캐시(group_type별 독립)를 채우고 payload를 반환한다 — 라우트
    핸들러와 collectors/live_refresh.py의 60초 인터벌 잡이 공유한다.

    **장중**: TTL마다 목록 페이지를 재조회한다(실패 시 502).

    **장 마감(2026-11-11 버그 수정, 이슈 #3)**: 예전에는 마감이면 네이버 호출을 생략하고
    마지막 캐시를 굳혀서 15:30 직전 값이 남았다(실측 업종 1위 등락률 8.40 → 장 마감 후
    실제 9.12). 이제 "확정 기준 시각"(가장 최근에 지난 18:05 KST,
    ``last_close_confirm_kst``/``required_confirm_kst``) 이후에 소스에서 실제 조회한 캐시면 재호출 없이
    ``market_closed: true``로 재사용하고(밤새/주말/다음날 개장 전 재호출 없음), 아니면
    (캐시 없음 또는 18:05 이전 조회분) 5분(``CLOSED_REFETCH_TTL_SECONDS``) 간격으로 재조회한다. 조회 시각은 캐시 엔트리의
    ``fetched_at``에 따로 보관한다(payload 스키마 불변). 마감 재조회가 실패하면 502 대신
    기존 캐시(있으면)를 ``market_closed: true``로, 없으면 빈 rows로 응답하고 warning을
    남기며 ``fetched_at``은 갱신하지 않아 다음 TTL에 재시도된다. DB에는 쓰지 않는다."""
    now = time.monotonic()
    async with _groups_live_cache_lock:
        entry = _groups_live_cache[group_type]
        cached = entry["data"]
        fetched_at = entry.get("fetched_at")
        now_kst = kst_now()
        closed = is_market_closed(now_kst)
        if closed and cached is not None and fetched_at is not None and fetched_at >= required_confirm_kst(now_kst):
            # 확정 기준 시각 이후에 조회된 값 — 네이버 재호출 없이 재사용.
            if not cached.get("market_closed"):
                cached = {**cached, "market_closed": True}
                entry["data"] = cached
            return cached
        # 마감 후 확정 전 구간은 장중(60초)보다 느린 간격으로 재조회한다.
        ttl = CLOSED_REFETCH_TTL_SECONDS if closed else LIVE_TTL_SECONDS
        if cached is not None and (now - entry["ts"]) < ttl:
            if closed and not cached.get("market_closed"):
                cached = {**cached, "market_closed": True}  # 장중에 만든 캐시가 마감 직후 TTL 내 재사용되는 경우
            return cached

        try:
            rows = await asyncio.to_thread(_fetch_group_list_blocking, group_type)
        except Exception as e:  # noqa: BLE001
            if not closed:
                raise HTTPException(502, f"groups live fetch failed: {str(e)[:200]}") from e
            logger.warning("groups_live(%s): 장마감 재조회 실패, 기존 캐시/빈 값 폴백: %s", group_type, e)
            if cached is not None:
                payload = {**cached, "market_closed": True}
            else:
                payload = {
                    "type": group_type,
                    "rows": [],
                    "market_closed": True,
                    "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                }
            # fetched_at은 건드리지 않는다(확정 조회 아님) — ts만 갱신해 다음 TTL에 재시도.
            entry["data"] = payload
            entry["ts"] = now
            return payload

        payload = {
            "type": group_type,
            "rows": [
                {"name": r["name"], "change_rate": r["change_rate"], "value": None, "market_sum": None}
                for r in rows
            ],
            "market_closed": closed,
            "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        entry["data"] = payload
        entry["ts"] = now
        entry["fetched_at"] = now_kst
        return payload


@router.get("/api/groups")
async def group_snapshot_list(
    type: str = Query("upjong", description="upjong(업종) 또는 theme(테마)"),
    date: dt.date | None = Query(None, description="생략 시 해당 group_type의 최신 날짜"),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    if type not in GROUP_TYPES:
        raise HTTPException(400, f"type must be one of {sorted(GROUP_TYPES)}")

    target_date = date
    if target_date is None:
        target_date = (
            await session.execute(
                select(func.max(GroupSnapshot.date)).where(GroupSnapshot.group_type == type)
            )
        ).scalar()

    if target_date is None:
        return []

    stmt = (
        select(GroupSnapshot)
        .where(GroupSnapshot.group_type == type, GroupSnapshot.date == target_date)
        .order_by(GroupSnapshot.change_rate.desc().nullslast())
    )
    rows = (await session.execute(stmt)).scalars().all()

    return [
        {
            "name": r.name,
            "change_rate": float(r.change_rate) if r.change_rate is not None else None,
            "value": r.value,
            "market_sum": r.market_sum,
        }
        for r in rows
    ]


@router.get("/api/groups/live")
async def group_snapshot_live(
    type: str = Query("upjong", description="upjong(업종) 또는 theme(테마)"),
) -> dict:
    """업종/테마 등락률 장중 라이브(PLAN.md §4.7, 2026-07-20 실측 편입).

    목록 페이지만 재조회해 7분 메모리 캐시로 감싼다(모듈 docstring 참고 —
    거래대금 합산은 라이브에서 하지 않는다). DB(group_snapshot)에는 쓰지 않는다.
    장 마감 후에도 18:05(확정 기준 시각) 이후 값이 확보될 때까지는 TTL마다 재조회하고,
    확보된 뒤에는 재호출 없이 ``market_closed: true``로 재사용한다(2026-11-11 버그
    수정 이슈 #3, `_warm_groups_live` docstring 참고). 마감 재조회 실패는 502가 아니라
    기존 캐시/빈 rows 폴백.

    Returns ``{"type": ..., "rows": [{"name", "change_rate", "value": null,
    "market_sum": null}, ...], "cached_at": iso8601}``.
    """
    if type not in GROUP_TYPES:
        raise HTTPException(400, f"type must be one of {sorted(GROUP_TYPES)}")
    return await _warm_groups_live(type)


@router.get("/api/groups/top-stocks")
async def group_top_stocks(
    type: str = Query("upjong", description="upjong(업종) 또는 theme(테마)"),
    name: str = Query(..., description="그룹명(트리맵 박스의 name 그대로)"),
    limit: int = Query(10, ge=1, le=50),
) -> dict:
    """업종·테마 트리맵 박스 클릭 → 대장 종목 TOP N (PLAN.md §5.12, 모듈 docstring
    참고). 이름→no 매핑(1일 TTL) + 구성 종목 조회(5분 TTL) 두 단계 캐시를 거친다.

    Returns ``{"type", "name", "rows": [{"code","name","change_rate","value"}, ...],
    "cached_at"}``. 목록에 없는 이름이면 404.
    """
    if type not in GROUP_TYPES:
        raise HTTPException(400, f"type must be one of {sorted(GROUP_TYPES)}")

    name_to_no = await _warm_group_name_to_no(type)
    no = name_to_no.get(name)
    if no is None:
        raise HTTPException(404, f"group not found: type={type} name={name!r}")

    return await _warm_top_stocks(type, name, no, limit)
