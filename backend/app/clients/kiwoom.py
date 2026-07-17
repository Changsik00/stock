"""Kiwoom Securities REST API client (PLAN.md §1, §5.4, §6 Phase 2-1).

키움 REST API(2025-03 출시, openapi.kiwoom.com)는 기존 OCX 방식과 달리 순수
HTTP라서 Mac/Linux에서도 동작한다. 이 모듈은 OAuth 접근토큰 발급·캐시, TR
(Transaction, api-id) 호출 공통 래퍼, per-TR token-bucket rate limiter를
제공한다.

## 스펙 출처 (2026-07-15 조사)

1. 공식 문서 https://openapi.kiwoom.com/guide/apiguide — WebFetch로 확인:
   - 실전 호스트 `https://api.kiwoom.com`, 모의 호스트 `https://mockapi.kiwoom.com`
   - 토큰 발급: `POST /oauth2/token`, `Content-Type: application/json;charset=UTF-8`,
     요청 body `{grant_type, appkey, secretkey}`, 응답 body
     `{return_code, return_msg, token_type, token, expires_dt}`
   - TR(예: ka10008) 상세 페이지에서 확인된 공통 헤더:
     - 요청: `authorization: Bearer <token>`(필수), `api-id`(필수, TR코드),
       `cont-yn`/`next-key`(선택, 연속조회 시 직전 응답 헤더값을 그대로 재전송)
     - 응답: `cont-yn`(다음 데이터 유무 Y/N), `next-key`(다음 조회 키), `api-id`
   - **주의**: SPA라 apiId 쿼리파라미터별 상세 필드까지는 WebFetch로 안정적으로
     긁히지 않았다(TR마다 다른 카테고리/URL이 나와야 하는데 일부 TR에서 직전 결과가
     재사용되는 현상을 확인함). 아래 `TR_RESOURCE_URL`의 개별 TR URL은 공식 문서
     대신 (2)를 근거로 삼았다.
2. GitHub https://github.com/younghwan91/kiwoom-rest-api (PyPI `kiwoom-client`,
   MIT, 실서버 스모크 테스트 `tests/integration_api_smoke.py` 포함) — `gh api`로
   소스 원문 확인:
   - `src/kiwoom_rest_api/base.py`: 요청 헤더 구성, POST + JSON body, HTTP 429 →
     지수 백오프 재시도, body의 `return_code`(0=성공, 5=요청 초과)로 성공 판정
   - `src/kiwoom_rest_api/domestic/stock_info.py`: `RESOURCE_URL = "/api/dostk/stkinfo"`
     아래 `ka10001`(종목기본정보), `ka10059`(투자자기관별종목별) 등록
   - README "요청 제한(Rate Limit)" 절 — **실측치**: TR(api_id)별 독립 버킷,
     지속 안전 속도 약 1 req/s(거부 0건), 순간 버스트 약 2건, 초과 시
     `HTTP 429` + body `{"return_code": 5, "return_msg": "허용된 요청 개수를
     초과하였습니다"}`. 이 값을 이 클라이언트의 기본 rate limit(1 req/s, burst 2)
     로 그대로 채택했다 — PLAN.md §1 "Rate limit 공식 미공개... 보수적으로 설계"
     방침과 일치.
   - `tests/integration_api_smoke.py`의 `PARAMS` 딕셔너리 — 실호출로 검증된
     TR별 요청 body 파라미터 예시. `ka10001: {"stk_cd": "005930"}`,
     `ka10059: {"dt": <YYYYMMDD>, "stk_cd": "005930", "amt_qty_tp": "1",
     "trde_tp": "0", "unit_tp": "1000"}`를 이 클라이언트의 편의 메서드 기본값으로
     그대로 사용했다.

`ka10059`의 URL을 공식 문서 TR 상세 페이지에서 조회하면 `/api/dostk/frgnistt`로
표시되기도 했는데, 이는 위 "주의" 사항(SPA 재사용 의심)과 충돌한다. 실계좌/모의
앱키가 없어 실호출로 확정할 수 없었으므로, 실제 통합 테스트 증거가 있는 (2)를
채택하고 `TR_RESOURCE_URL`을 한 곳에 모아 쉽게 고칠 수 있게 했다. **키를 받으면
`scripts/kiwoom_probe.py`로 가장 먼저 이 URL이 맞는지 확인할 것.**

## Phase 1.5-1 probe 시도 결과 (2026-07-17, 실호출 미완료 — 크리덴셜 블로커)

`.env`의 `KIWOOM_APP_KEY`/`KIWOOM_APP_SECRET`으로 `scripts/kiwoom_probe.py`를
실행했으나 **토큰 발급(`POST /oauth2/token`) 단계에서 막혔다**:

- 실전(`api.kiwoom.com`) / 모의(`mockapi.kiwoom.com`) **양쪽 호스트 모두**
  `return_code=3`, `return_msg="인증에 실패했습니다[8001:App Key와 Secret Key
  검증에 실패했습니다]"`로 거부됨. 즉 이 앱키/시크릿 쌍으로는 실전·모의 어느
  쪽도 인증되지 않는다 — 호스트 선택 문제가 아니라 키 자체(만료/미활성화/IP
  미등록/포털 오기재 등) 문제로 보인다.
- 클라이언트 코드 버그가 아님을 `curl`로 동일 요청을 직접 재현해 확인함(같은
  return_code=3). `.env` 파일 자체의 공백·개행 오염 여부도 hexdump로 확인함 —
  문제없음.
- 결과: **TR URL 실호출 검증과 rate limit 실측은 이번 회차에서 수행하지 못함.**
  아래 `TR_RESOURCE_URL`의 `ka10001`/`ka10059`/`ka20001`은 여전히 (2) GitHub
  소스코드 정적 분석 근거이며 "실호출로 확정"이 아니다. `ka20001`은 같은
  저장소의 `src/kiwoom_rest_api/domestic/sector.py`(`RESOURCE_URL =
  "/api/dostk/sect"`, `industry_current_price` → `ka20001`)와
  `tests/integration_api_smoke.py`의 `PARAMS["ka20001"] = {"mrkt_tp": "0",
  "inds_cd": SECTOR}`(`SECTOR = "001"` 종합KOSPI 주석)를 근거로 추가했다 —
  PLAN.md §3.5의 "`/api/dostk/sect` 유력" 추정과 일치.
- **다음 작업자 TODO**: 키움 포털에서 앱키/시크릿 재발급 또는 IP 등록 상태
  확인 후 `KIWOOM_APP_KEY`/`KIWOOM_APP_SECRET`을 갱신하고
  `scripts/kiwoom_probe.py`를 재실행해 (1) TR URL, (2) `ka20001` 응답의
  등락 종목수 필드 존재 여부, (3) rate limit을 실측할 것. 스크립트에는
  `ka20001`을 종목코드 001/101로 호출해 원본 JSON을 덤프하는 단계(`step_d`)를
  이미 추가해 뒀다.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)

# 실전/모의 호스트 (공식 문서 + README "환경 설정" 표, 2026-07-15 확인)
PROD_BASE_URL = "https://api.kiwoom.com"
MOCK_BASE_URL = "https://mockapi.kiwoom.com"

TOKEN_ENDPOINT = "/oauth2/token"

# TR(api-id) → 리소스 URL. 출처는 모듈 docstring 참고.
# 주의(2026-07-17): 크리덴셜 블로커로 실호출 검증 못함 — 전부 (2) GitHub 소스코드
# 정적 분석 근거. 확정 아님, probe 재실행 시 가장 먼저 검증할 것.
TR_RESOURCE_URL: dict[str, str] = {
    "ka10001": "/api/dostk/stkinfo",  # 종목기본정보요청
    "ka10059": "/api/dostk/stkinfo",  # 종목별투자자기관별요청
    "ka20001": "/api/dostk/sect",  # 업종현재가요청 (PLAN.md §3.5 breadth 선행 조건)
}

# README 실측치: TR별 지속 1 req/s(거부 0), 버스트 약 2건.
DEFAULT_RATE_LIMIT = 1.0
DEFAULT_RATE_BURST = 2

# 토큰 만료 30분 전에 선제 재발급 (PLAN.md §5.4).
TOKEN_REFRESH_MARGIN = dt.timedelta(minutes=30)

# 토큰 캐시 파일: backend/.kiwoom_token.json (이 파일은 backend/app/clients/kiwoom.py
# 에서 parents[2] == backend/). .gitignore에 등록되어 있음(평문 토큰 포함).
DEFAULT_TOKEN_CACHE_PATH = Path(__file__).resolve().parents[2] / ".kiwoom_token.json"

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0


class KiwoomAuthError(Exception):
    """앱키/시크릿이 없거나 토큰 발급 자체가 실패했을 때."""


class KiwoomAPIError(Exception):
    """TR 호출이 `return_code != 0`으로 실패했을 때(rate limit 소진 후 포함)."""

    def __init__(self, code: Any, message: str, response: dict[str, Any] | None = None):
        self.code = code
        self.message = message
        self.response = response
        super().__init__(f"[{code}] {message}")


@dataclass
class _TokenCache:
    access_token: str
    expires_at: dt.datetime  # tz-aware (UTC)
    is_mock: bool

    def is_valid(self) -> bool:
        return dt.datetime.now(dt.timezone.utc) < self.expires_at - TOKEN_REFRESH_MARGIN

    def to_json(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "expires_at": self.expires_at.isoformat(),
            "is_mock": self.is_mock,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "_TokenCache":
        return cls(
            access_token=data["access_token"],
            expires_at=dt.datetime.fromisoformat(data["expires_at"]),
            is_mock=data["is_mock"],
        )


class _AsyncTokenBucket:
    """Per-TR asyncio token-bucket rate limiter.

    키움의 rate limit이 TR(api_id)별 독립이라는 실측 근거(모듈 docstring 참고)에
    따라 TR마다 별도 버킷을 유지한다 — 서로 다른 TR을 섞어 호출할 때 불필요하게
    서로를 막지 않기 위함.
    """

    def __init__(self, rate: float, capacity: float | None = None) -> None:
        self.rate = rate
        self.capacity = float(capacity) if capacity is not None else float(rate)
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_refill)
        self._lock = asyncio.Lock()

    async def acquire(self, key: str) -> None:
        while True:
            async with self._lock:
                now = asyncio.get_running_loop().time()
                tokens, last_refill = self._buckets.get(key, (self.capacity, now))
                tokens = min(self.capacity, tokens + (now - last_refill) * self.rate)
                if tokens >= 1:
                    self._buckets[key] = (tokens - 1, now)
                    return
                # Not enough tokens: compute wait time, release lock while sleeping.
                wait = (1 - tokens) / self.rate
                self._buckets[key] = (tokens, now)
            await asyncio.sleep(wait)


class KiwoomClient:
    """키움 REST API 비동기 클라이언트.

    Args:
        app_key, app_secret: 미지정 시 `config.get_settings()`의
            `kiwoom_app_key`/`kiwoom_app_secret` 사용.
        mock: 미지정 시 `settings.kiwoom_mock`(.env `KIWOOM_MOCK=1`) 사용.
        rate_limit / rate_burst: TR당 초당 허용 요청 수 / 버스트 크기.
            기본값은 README 실측치(1 req/s, burst 2) — PLAN.md §5.4.
        token_cache_path: 접근토큰 캐시 파일 경로. 기본값은
            `backend/.kiwoom_token.json`.
        http_client: 테스트에서 `httpx.AsyncClient(transport=MockTransport(...))`
            등을 주입하기 위한 훅. 지정하지 않으면 실제 HTTP 클라이언트를 만든다.
    """

    def __init__(
        self,
        app_key: str | None = None,
        app_secret: str | None = None,
        mock: bool | None = None,
        rate_limit: float = DEFAULT_RATE_LIMIT,
        rate_burst: float = DEFAULT_RATE_BURST,
        token_cache_path: Path | None = None,
        http_client: httpx.AsyncClient | None = None,
        max_retries: int = _MAX_RETRIES,
    ) -> None:
        settings = get_settings()
        self.app_key = app_key if app_key is not None else settings.kiwoom_app_key
        self.app_secret = app_secret if app_secret is not None else settings.kiwoom_app_secret
        self.is_mock = settings.kiwoom_mock if mock is None else mock
        self.base_url = MOCK_BASE_URL if self.is_mock else PROD_BASE_URL
        self.token_cache_path = token_cache_path or DEFAULT_TOKEN_CACHE_PATH
        self.max_retries = max_retries

        self._client = http_client or httpx.AsyncClient(base_url=self.base_url, timeout=30.0)
        self._owns_client = http_client is None
        self._token: _TokenCache | None = None
        self._token_lock = asyncio.Lock()
        self._bucket = _AsyncTokenBucket(rate_limit, rate_burst)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "KiwoomClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # -- 인증 -----------------------------------------------------------

    def _require_keys(self) -> None:
        if not self.app_key or not self.app_secret:
            raise KiwoomAuthError(
                "키움 앱키/시크릿이 설정되지 않았습니다. .env의 KIWOOM_APP_KEY / "
                "KIWOOM_APP_SECRET을 채운 뒤 다시 시도하세요 "
                "(openapi.kiwoom.com에서 서비스 신청 후 발급, PLAN.md §6 Phase 0)."
            )

    def _load_cached_token(self) -> _TokenCache | None:
        if not self.token_cache_path.exists():
            return None
        try:
            data = json.loads(self.token_cache_path.read_text())
            cache = _TokenCache.from_json(data)
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning("키움 토큰 캐시 파일 파싱 실패, 무시하고 재발급: %s", exc)
            return None
        if cache.is_mock != self.is_mock:
            # 실전/모의 토큰을 섞어 쓰면 안 되므로 무시.
            return None
        return cache

    def _save_token_cache(self, cache: _TokenCache) -> None:
        try:
            self.token_cache_path.write_text(json.dumps(cache.to_json(), ensure_ascii=False))
        except OSError as exc:
            logger.warning("키움 토큰 캐시 파일 저장 실패(다음 요청 시 매번 재발급될 수 있음): %s", exc)

    async def _issue_token(self) -> _TokenCache:
        """POST /oauth2/token — 접근토큰발급 (au10001, 공식 문서 확인).

        만료(expires_dt)는 발급 시각 기준 24시간이 기본이지만, 서버가 돌려주는
        `expires_dt`(형식 `YYYYMMDDHHMMSS` 절대 시각으로 관측)를 우선 사용하고,
        형식이 다르거나 없으면 '지금부터 24시간'으로 보수적으로 폴백한다.
        """
        self._require_keys()
        resp = await self._client.post(
            TOKEN_ENDPOINT,
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "secretkey": self.app_secret,
            },
            headers={"Content-Type": "application/json;charset=UTF-8"},
        )
        if resp.status_code >= 400:
            raise KiwoomAuthError(
                f"키움 토큰 발급 실패: HTTP {resp.status_code} {resp.text[:300]}"
            )
        data = resp.json()
        return_code = data.get("return_code", 0)
        if return_code not in (0, None):
            raise KiwoomAuthError(
                f"키움 토큰 발급 실패: return_code={return_code} "
                f"return_msg={data.get('return_msg')!r}"
            )
        token = data.get("token") or data.get("access_token")
        if not token:
            raise KiwoomAuthError(f"키움 토큰 발급 응답에 token 필드가 없습니다: {data}")

        expires_at = self._parse_expires_dt(data.get("expires_dt"))
        cache = _TokenCache(access_token=token, expires_at=expires_at, is_mock=self.is_mock)
        self._save_token_cache(cache)
        logger.info(
            "키움 접근토큰 발급 완료 (%s, 만료 %s)",
            "모의" if self.is_mock else "실전",
            expires_at.isoformat(),
        )
        return cache

    @staticmethod
    def _parse_expires_dt(raw: str | None) -> dt.datetime:
        now = dt.datetime.now(dt.timezone.utc)
        if raw:
            for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    # 키움 서버 시각은 KST(UTC+9) 기준으로 관측됨.
                    parsed = dt.datetime.strptime(raw, fmt)
                    kst = parsed.replace(tzinfo=dt.timezone(dt.timedelta(hours=9)))
                    return kst.astimezone(dt.timezone.utc)
                except ValueError:
                    continue
            logger.warning("expires_dt 파싱 실패(%r), 24시간 뒤로 폴백", raw)
        return now + dt.timedelta(hours=24)

    async def _get_token(self) -> str:
        async with self._token_lock:
            if self._token is not None and self._token.is_valid():
                return self._token.access_token

            cached = self._load_cached_token()
            if cached is not None and cached.is_valid():
                self._token = cached
                return cached.access_token

            self._token = await self._issue_token()
            return self._token.access_token

    # -- TR 호출 ----------------------------------------------------------

    async def call_tr(
        self,
        api_id: str,
        body: dict[str, Any],
        cont_yn: str | None = None,
        next_key: str | None = None,
        resource_url: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """공통 TR 호출 래퍼.

        Returns:
            `(응답 body dict, {"cont-yn", "next-key", "api-id"} 응답 헤더 dict)`

        Raises:
            KiwoomAuthError: 앱키/시크릿 미설정 또는 토큰 발급 실패.
            KiwoomAPIError: `return_code != 0` (rate limit 소진 후 포함).
            httpx.HTTPStatusError: 429/5xx 재시도를 모두 소진한 뒤에도 실패.
        """
        self._require_keys()
        url = resource_url or TR_RESOURCE_URL.get(api_id)
        if not url:
            raise ValueError(
                f"api_id={api_id!r}의 리소스 URL을 모릅니다. resource_url을 "
                "직접 지정하거나 TR_RESOURCE_URL에 등록하세요."
            )

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            token = await self._get_token()
            await self._bucket.acquire(api_id)

            headers = {
                "Content-Type": "application/json;charset=UTF-8",
                "authorization": f"Bearer {token}",
                "api-id": api_id,
                "cont-yn": cont_yn or "N",
                "next-key": next_key or "",
            }
            try:
                resp = await self._client.post(url, json=body, headers=headers)
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    await self._backoff(attempt)
                    continue
                raise

            # HTTP 429 또는 5xx → 지수 백오프 재시도 (PLAN.md §5.4).
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=resp.request, response=resp
                )
                if attempt < self.max_retries:
                    await self._backoff(attempt)
                    continue
                resp.raise_for_status()

            resp.raise_for_status()
            data = resp.json()
            return_code = data.get("return_code", 0)
            # return_code == 5: "허용된 요청 개수를 초과" (rate limit, README 실측).
            # HTTP 200으로 오는 케이스도 있어 status_code만으로는 못 잡으므로 별도 처리.
            if return_code == 5 and attempt < self.max_retries:
                last_exc = KiwoomAPIError(return_code, data.get("return_msg", ""), data)
                await self._backoff(attempt)
                continue
            if return_code not in (0, None):
                raise KiwoomAPIError(return_code, data.get("return_msg", "Unknown error"), data)

            resp_headers = {
                "cont-yn": resp.headers.get("cont-yn", "N"),
                "next-key": resp.headers.get("next-key", ""),
                "api-id": resp.headers.get("api-id", api_id),
            }
            return data, resp_headers

        # 재시도 소진.
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("call_tr retry loop exited unexpectedly")  # pragma: no cover

    async def _backoff(self, attempt: int) -> None:
        delay = _RETRY_BASE_DELAY * (2**attempt)
        logger.warning("키움 API 재시도 대기 %.1fs (attempt %d)", delay, attempt + 1)
        await asyncio.sleep(delay)

    # -- 편의 메서드 --------------------------------------------------------

    async def stock_info(self, code: str) -> dict[str, Any]:
        """종목기본정보요청 (ka10001). `code`: 거래소별 종목코드(예: "005930")."""
        data, _ = await self.call_tr("ka10001", {"stk_cd": code})
        return data

    async def stock_investor_daily(
        self,
        code: str,
        date: dt.date | str | None = None,
        amt_qty_tp: str = "1",
        trde_tp: str = "0",
        unit_tp: str = "1000",
        cont_yn: str | None = None,
        next_key: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """종목별투자자기관별요청 (ka10059).

        Args:
            code: 종목코드.
            date: 조회 일자(기본값: 오늘, KST).
            amt_qty_tp: 금액수량구분 — "1"=금액, "2"=수량.
            trde_tp: 매매구분 — "0"=순매수, "1"=매수, "2"=매도.
            unit_tp: 단위구분 — "1000"=천주, "1"=단주.

        Returns:
            `(응답 body, 응답 헤더)` — 연속조회가 필요하면 헤더의 cont-yn/next-key를
            다음 호출의 cont_yn/next_key로 그대로 넘기면 된다.
        """
        if date is None:
            date_str = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime("%Y%m%d")
        elif isinstance(date, dt.date):
            date_str = date.strftime("%Y%m%d")
        else:
            date_str = date

        body = {
            "dt": date_str,
            "stk_cd": code,
            "amt_qty_tp": amt_qty_tp,
            "trde_tp": trde_tp,
            "unit_tp": unit_tp,
        }
        return await self.call_tr("ka10059", body, cont_yn=cont_yn, next_key=next_key)

    async def sector_current_price(
        self,
        inds_cd: str,
        mrkt_tp: str = "0",
        cont_yn: str | None = None,
        next_key: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """업종현재가요청 (ka20001) — PLAN.md §3.5 등락 종목수(breadth) 후보 TR.

        Args:
            inds_cd: 업종코드. "001"=종합(KOSPI), "101"=종합(KOSDAQ) (GitHub
                통합테스트 `SECTOR = "001"` 주석 근거, 2026-07-17 기준 실호출
                미검증 — 모듈 docstring 참고).
            mrkt_tp: 시장구분. 통합테스트 예시 기본값 "0".

        Returns:
            `(응답 body, 응답 헤더)`. 응답 body에 상승/하락/보합/상한/하한
            종목수 필드가 있는지는 아직 실호출로 확인되지 않았다 —
            `scripts/kiwoom_probe.py`의 `step_d`로 확인할 것.
        """
        body = {"mrkt_tp": mrkt_tp, "inds_cd": inds_cd}
        return await self.call_tr("ka20001", body, cont_yn=cont_yn, next_key=next_key)
