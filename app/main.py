"""Ponto de entrada: `uvicorn app.main:app`."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.collectors import build_collectors
from app.config import BASE_DIR, get_settings
from app.core.db import Database
from app.core.http import HttpClient
from app.core.logging import setup_logging
from app.scheduler import build_scheduler

log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)

    db = Database(settings.database_path)
    await db.connect()
    http = HttpClient(timeout=settings.http_timeout, max_retries=settings.http_max_retries)
    collectors = build_collectors(http, settings)
    scheduler = build_scheduler(collectors, db)

    app.state.db = db
    app.state.collectors = collectors
    scheduler.start()
    log.info(
        "aplicação iniciada",
        extra={"db": str(settings.database_path), "collectors": [c.name for c in collectors],
               "fred_key": bool(settings.fred_api_key)},
    )
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        await http.aclose()
        await db.close()


app = FastAPI(
    title="BTC Dashboard API",
    description="Snapshot consolidado do mercado de Bitcoin a partir de APIs públicas.",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(router)
# Montado por último: tudo que não é /api ou /docs cai no front estático.
app.mount("/", StaticFiles(directory=BASE_DIR / "web", html=True), name="web")
