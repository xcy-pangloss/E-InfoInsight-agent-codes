"""FastAPI 入口"""

import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import data, crawl, dashboard, quality, config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = FastAPI(
    title="e-InfoInsight 管理系统",
    description="武汉IT企业智能评级系统 — 网页管理后端",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(data.router)
app.include_router(crawl.router)
app.include_router(dashboard.router)
app.include_router(quality.router)
app.include_router(config.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
