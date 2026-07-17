"""Dump one raw day of data from each dataset used by the dashboard.

Run after KRX approves the idx/kospi_dd_trd, idx/kosdaq_dd_trd, drv/fut_bydd_trd
datasets in 마이페이지, to confirm the actual field names before trusting the
parsing logic in app/services.py.

Usage: python -m scripts.smoke_test [YYYYMMDD]
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from app.krx_client import KRXClient  # noqa: E402

load_dotenv()


def previous_weekday(d: date) -> date:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def main():
    bas_dd = sys.argv[1] if len(sys.argv) > 1 else previous_weekday(date.today() - timedelta(days=1)).strftime("%Y%m%d")
    client = KRXClient()

    for category, endpoint in [
        ("idx", "kospi_dd_trd"),
        ("idx", "kosdaq_dd_trd"),
        ("drv", "fut_bydd_trd"),
    ]:
        print(f"\n=== {category}/{endpoint} @ {bas_dd} ===")
        rows = client.daily(category, endpoint, bas_dd)
        print(f"{len(rows)} rows")
        for row in rows[:3]:
            print(json.dumps(row, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
