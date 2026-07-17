"""Thin client for the official KRX Open API (https://openapi.krx.co.kr).

Auth: header `AUTH_KEY: <key>`. Each dataset (idx/kospi_dd_trd, idx/kosdaq_dd_trd,
drv/fut_bydd_trd, ...) also needs a separate per-dataset approval in 마이페이지
before calls succeed — until approved, calls return HTTP 401 with
respMsg "Unauthorized API Call".
"""

import os

import requests

BASE_URL = "https://data-dbg.krx.co.kr/svc/apis"


class KRXAuthError(Exception):
    pass


class KRXClient:
    def __init__(self, api_key: str | None = None, timeout: int = 10):
        self.api_key = api_key or os.environ.get("KRX_OPENAPI_KEY")
        if not self.api_key:
            raise KRXAuthError("KRX_OPENAPI_KEY is not set")
        self.timeout = timeout
        self.session = requests.Session()

    def daily(self, category: str, endpoint: str, bas_dd: str) -> list[dict]:
        """Return OutBlock_1 rows for one trading day, or [] on a non-trading day."""
        url = f"{BASE_URL}/{category}/{endpoint}"
        resp = self.session.get(
            url,
            params={"basDd": bas_dd},
            headers={"AUTH_KEY": self.api_key},
            timeout=self.timeout,
        )
        if resp.status_code == 401:
            raise KRXAuthError(f"{category}/{endpoint}: {resp.text}")
        resp.raise_for_status()
        data = resp.json()
        return data.get("OutBlock_1", [])
