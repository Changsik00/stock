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
    # ENABLE_SCHEDULER=1일 때만 배치 스케줄러를 기동한다 (기본 꺼짐, PLAN.md §5.1/§6).
    if os.environ.get("ENABLE_SCHEDULER") == "1":
        from .collectors.scheduler import shutdown_scheduler, start_scheduler

        start_scheduler()
        yield
        shutdown_scheduler()
    else:
        yield


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
