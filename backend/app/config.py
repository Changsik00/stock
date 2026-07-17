"""Application settings loaded from environment variables / .env.

Uses pydantic-settings so all config lives in one typed place. Fields map 1:1
to the environment variables documented in PLAN.md §5.4.
"""

from functools import lru_cache

from dotenv import find_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# The repo's .env lives at the repository root, but uvicorn is normally run
# from backend/. python-dotenv's find_dotenv() walks up from the CWD to
# locate it (same behavior main.py already relies on via load_dotenv()).
_ENV_FILE = find_dotenv(usecwd=True) or ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = (
        "postgresql+asyncpg://stock:stock@localhost:5433/stock"
    )

    # 키움증권 REST API
    kiwoom_app_key: str | None = None
    kiwoom_app_secret: str | None = None
    # KIWOOM_MOCK=1 → 모의투자 서버(mockapi.kiwoom.com) 사용. 기본값(0/미설정)은 실전
    # (api.kiwoom.com). PLAN.md §6 Phase 2-1 — 모의 앱키를 먼저 발급받아 검증하는 흐름.
    kiwoom_mock: bool = False

    # 한국투자증권(KIS) REST API
    kis_app_key: str | None = None
    kis_app_secret: str | None = None

    # 한국은행 ECOS API
    ecos_api_key: str | None = None

    # 기존 KRX Open API (시세)
    krx_openapi_key: str | None = None

    # data.krx.co.kr 무료 회원 로그인 (증권사 계좌 아님). 2026-02 KRX 데이터 포털 개편
    # 이후 pykrx(clients/pykrx_client.py)가 시장 수급을 크롤링하려면 필수 — 없으면
    # data.krx.co.kr의 모든 통계 JSON 엔드포인트가 익명 요청을 HTTP 400 "LOGOUT"으로 거부한다.
    krx_id: str | None = None
    krx_pw: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
