import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import admin, basis, etf, flow_rank, groups, macro, markets, stocks

load_dotenv()
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ENABLE_SCHEDULER=1 -> 평일 18:00 Asia/Seoul 일별 배치(collectors/scheduler.py).
    # ENABLE_LIVE_REFRESH=1 -> 장중 60초 간격 라이브 캐시 선제 워밍
    # (collectors/live_refresh.py, routers/markets.py의 breadth/live·flow/live·
    # attention 캐시). 서로 독립적인 토글이라 하나만 켜거나 둘 다 켤 수 있다 —
    # 둘 다 켜도 무해하다(서로 다른 캐시/테이블을 건드림). 둘 다 기본 꺼짐(PLAN.md §5.1).
    scheduler_enabled = os.environ.get("ENABLE_SCHEDULER") == "1"
    live_refresh_enabled = os.environ.get("ENABLE_LIVE_REFRESH") == "1"

    if scheduler_enabled:
        from .collectors.scheduler import start_scheduler

        start_scheduler()
    if live_refresh_enabled:
        from .collectors.live_refresh import start_live_refresh_scheduler

        start_live_refresh_scheduler()

    yield

    if scheduler_enabled:
        from .collectors.scheduler import shutdown_scheduler

        shutdown_scheduler()
    if live_refresh_enabled:
        from .collectors.live_refresh import shutdown_live_refresh_scheduler

        shutdown_live_refresh_scheduler()


app = FastAPI(title="수급 분석 대시보드 API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

app.include_router(markets.router)
app.include_router(basis.router)
app.include_router(stocks.router)
app.include_router(etf.router)
app.include_router(macro.router)
app.include_router(flow_rank.router)
app.include_router(groups.router)
app.include_router(admin.router)


@app.get("/api/health")
def health():
    return {"ok": True}
