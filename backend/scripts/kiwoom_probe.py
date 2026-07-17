"""키움 REST API 실호출 검증 스크립트 (PLAN.md §6 Phase 2-1 완료 기준).

.env에 KIWOOM_APP_KEY / KIWOOM_APP_SECRET을 채운 뒤 실행한다. 기본은 모의투자
서버(mockapi.kiwoom.com, KIWOOM_MOCK=1)를 가정하지만, .env의 KIWOOM_MOCK 값을
그대로 따른다 — 실전키만 있다면 KIWOOM_MOCK=0(또는 미설정)으로 두면 된다.

수행 내용:
    (a) 토큰 발급 확인 — POST /oauth2/token, 캐시 파일(backend/.kiwoom_token.json)
        생성 여부까지 확인
    (b) ka10001(종목기본정보요청) 단건 호출 — 삼성전자(005930)
    (d) ka20001(업종현재가요청) 호출 — 업종코드 001(종합KOSPI)/101(종합KOSDAQ),
        응답 원본 필드 전체를 덤프해 상승/상한/보합/하락/하한 종목수 필드
        존재 여부를 사람이 눈으로 확인한다 (PLAN.md §3.5 breadth 선행 조건,
        이 스크립트의 가장 중요한 산출물).
    (c) rate limit 실측 — 같은 TR(ka10001)을 서버 부담을 고려해 최대 40회,
        최대 30초까지 연속 호출하며 각 호출의 결과(OK/429/에러)와 지연을 기록,
        표로 출력한 뒤 권장 rate 값을 제안한다. 클라이언트 내장 rate limiter를
        끄고(rate_limit=None 상당 — 아주 높은 값으로 설정) 호출하므로 서버 쪽
        실제 한도가 드러난다.

주의:
    - kiwoom.py의 TR_RESOURCE_URL은 ka10001/ka10059 모두 "/api/dostk/stkinfo"로
      가정했다(모듈 docstring의 출처 설명 참고). 이 스크립트의 (b) 단계가
      404/타 에러로 실패하면 가장 먼저 이 URL 가정이 틀렸을 가능성을 의심할 것.
    - (c)는 서버에 최대 40회 연속 요청을 보낸다. 계정 차단 등 리스크를 낮추기
      위해 상한을 뒀지만, 그래도 실행 전 이 사실을 인지할 것.

사용법:
    cd backend
    venv/bin/python scripts/kiwoom_probe.py
    venv/bin/python scripts/kiwoom_probe.py --rate-probe-max 20   # 상한 조정
    venv/bin/python scripts/kiwoom_probe.py --skip-rate-probe     # (a)(b)만
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.clients.kiwoom import (  # noqa: E402
    KiwoomAPIError,
    KiwoomAuthError,
    KiwoomClient,
)
from app.config import get_settings  # noqa: E402

HARD_CALL_CAP = 40
HARD_TIME_CAP_SEC = 30.0
PROBE_STOCK_CODE = "005930"  # 삼성전자


async def step_a_token(client: KiwoomClient) -> bool:
    print("\n=== (a) 토큰 발급 확인 ===")
    try:
        token = await client._get_token()  # noqa: SLF001 — probe script, intentional
    except KiwoomAuthError as exc:
        print(f"실패: {exc}")
        return False
    print(f"발급 성공: 토큰 …{token[-8:]} (마지막 8자만 표시)")
    print(f"캐시 파일: {client.token_cache_path} (존재: {client.token_cache_path.exists()})")
    return True


async def step_b_stock_info(client: KiwoomClient) -> bool:
    print("\n=== (b) ka10001 단건 호출 (005930 삼성전자) ===")
    try:
        data = await client.stock_info(PROBE_STOCK_CODE)
    except KiwoomAPIError as exc:
        print(f"API 에러: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"예외: {type(exc).__name__}: {exc}")
        return False
    name = data.get("stk_nm", "?")
    price = data.get("cur_prc", "?")
    print(f"성공: stk_nm={name!r} cur_prc={price!r}")
    print(f"응답 필드 수: {len(data)} (return_code/return_msg 포함)")
    return True


SECTOR_CODES = {"001": "종합KOSPI", "101": "종합KOSDAQ"}


async def step_d_sector_breadth(client: KiwoomClient) -> bool:
    """ka20001(업종현재가) 응답 원본 필드 덤프 — PLAN.md §3.5 breadth 선행 조건.

    상승/상한/보합/하락/하한 종목수 필드가 실제로 존재하는지가 이 probe의
    가장 중요한 산출물이다. 필드명을 미리 알 수 없으므로 응답 dict 전체를
    키-값으로 덤프해서 사람이 눈으로 확인하게 한다.
    """
    print("\n=== (d) ka20001 업종현재가 — 등락 종목수 필드 확인 ===")
    ok = True
    for code, label in SECTOR_CODES.items():
        print(f"\n--- inds_cd={code} ({label}) ---")
        try:
            data, _headers = await client.sector_current_price(code)
        except KiwoomAPIError as exc:
            print(f"API 에러: {exc}")
            ok = False
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"예외: {type(exc).__name__}: {exc}")
            ok = False
            continue
        print(f"응답 필드 수: {len(data)}")
        for k, v in data.items():
            print(f"  {k!r}: {v!r}")
    return ok


async def step_c_rate_probe(client: KiwoomClient, max_calls: int) -> None:
    max_calls = min(max_calls, HARD_CALL_CAP)
    print(
        f"\n=== (c) rate limit 실측 (최대 {max_calls}회, 최대 {HARD_TIME_CAP_SEC:.0f}초, "
        f"ka10001 반복 호출) ==="
    )
    print("클라이언트 내장 rate limiter는 매우 높게 설정해 서버 쪽 실제 한도를 관측한다.")

    rows: list[dict[str, object]] = []
    start = time.monotonic()
    first_429_at: int | None = None

    for i in range(1, max_calls + 1):
        elapsed_total = time.monotonic() - start
        if elapsed_total > HARD_TIME_CAP_SEC:
            print(f"  (시간 상한 {HARD_TIME_CAP_SEC:.0f}초 도달, {i - 1}회에서 중단)")
            break

        t0 = time.monotonic()
        status = "?"
        detail = ""
        try:
            # call_tr을 직접 써서 클라이언트 재시도 로직을 건너뛰고 서버 응답을
            # 그대로 관측한다(재시도가 끼면 실측 타이밍이 왜곡됨).
            token = await client._get_token()  # noqa: SLF001
            resp = await client._client.post(  # noqa: SLF001
                "/api/dostk/stkinfo",
                json={"stk_cd": PROBE_STOCK_CODE},
                headers={
                    "Content-Type": "application/json;charset=UTF-8",
                    "authorization": f"Bearer {token}",
                    "api-id": "ka10001",
                    "cont-yn": "N",
                    "next-key": "",
                },
            )
            if resp.status_code == 429:
                status = "429"
                if first_429_at is None:
                    first_429_at = i
            elif resp.status_code >= 400:
                status = f"HTTP{resp.status_code}"
            else:
                body = resp.json()
                rc = body.get("return_code", 0)
                if rc == 5:
                    status = "rc=5(초과)"
                    if first_429_at is None:
                        first_429_at = i
                elif rc not in (0, None):
                    status = f"rc={rc}"
                else:
                    status = "OK"
        except Exception as exc:  # noqa: BLE001
            status = "EXC"
            detail = f"{type(exc).__name__}: {exc}"

        dt_ms = (time.monotonic() - t0) * 1000
        rows.append({"n": i, "status": status, "ms": round(dt_ms, 1), "detail": detail})

    # 표 출력
    print(f"\n{'#':>3}  {'status':<12}  {'ms':>8}  detail")
    for r in rows:
        print(f"{r['n']:>3}  {r['status']:<12}  {r['ms']:>8}  {r['detail']}")

    total_elapsed = time.monotonic() - start
    ok_count = sum(1 for r in rows if r["status"] == "OK")
    print(f"\n총 {len(rows)}회, 성공 {ok_count}회, 소요 {total_elapsed:.1f}초")

    if first_429_at is None:
        print(
            f"결과: {len(rows)}회 연속 호출에서 429/rc=5를 관측하지 못함 — 상한 내에서는 "
            "제한에 도달하지 않음. kiwoom.py의 기본값(1 req/s, burst 2, 커뮤니티 실측 기반)을 "
            "그대로 유지하는 것을 권장. 더 공격적인 값을 원하면 이 스크립트의 "
            "--rate-probe-max를 늘려 재실행."
        )
    else:
        sustained_rate = (first_429_at - 1) / total_elapsed if total_elapsed > 0 else 0
        print(
            f"결과: {first_429_at}번째 호출에서 첫 제한 발생. 그 직전까지 처리량 기준 "
            f"약 {sustained_rate:.2f} req/s. 권장: 여유를 두고 이 값의 약 70% 이하로 "
            f"kiwoom.py의 rate_limit을 설정할 것(예: {sustained_rate * 0.7:.2f} req/s)."
        )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rate-probe-max", type=int, default=HARD_CALL_CAP, help="rate probe 최대 호출 횟수"
    )
    parser.add_argument("--skip-rate-probe", action="store_true", help="(c) 단계 건너뛰기")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.kiwoom_app_key or not settings.kiwoom_app_secret:
        print(
            "KIWOOM_APP_KEY / KIWOOM_APP_SECRET이 .env에 없습니다. "
            "키 발급 후 .env에 채우고 다시 실행하세요. (지금은 스킵)"
        )
        return 2

    server = "모의투자(mockapi.kiwoom.com)" if settings.kiwoom_mock else "실전(api.kiwoom.com)"
    print(f"서버: {server}")

    client = KiwoomClient()
    try:
        ok_a = await step_a_token(client)
        if not ok_a:
            return 1
        ok_b = await step_b_stock_info(client)
        if not ok_b:
            return 1
        await step_d_sector_breadth(client)
        if not args.skip_rate_probe:
            await step_c_rate_probe(client, args.rate_probe_max)
    finally:
        await client.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
