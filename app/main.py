"""Ponto de entrada: `uvicorn app.main:app`."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.alerts.setup import build_alert_service, schedule_alert_jobs
from app.api.routes import router
from app.collectors import build_collectors
from app.config import BASE_DIR, get_settings
from app.core.db import Database
from app.core.http import HttpClient
from app.core.logging import setup_logging
from app.demo import DemoHttpClient, demo_settings
from app.scheduler import build_scheduler

log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    if settings.demo_mode:
        # nenhuma chamada externa: respostas sintéticas a partir das fixtures, banco separado
        settings = demo_settings(settings)
        http = DemoHttpClient()
    else:
        http = HttpClient(timeout=settings.http_timeout, max_retries=settings.http_max_retries)

    db = Database(settings.database_path)
    await db.connect()
    collectors = build_collectors(http, settings)
    scheduler = build_scheduler(collectors, db)
    alerts = build_alert_service(settings, db, collectors, http)
    if alerts:
        schedule_alert_jobs(scheduler, alerts, settings)

    app.state.db = db
    app.state.collectors = collectors
    app.state.demo = settings.demo_mode
    app.state.alerts = alerts
    scheduler.start()
    log.info(
        "aplicação iniciada",
        extra={"db": str(settings.database_path), "collectors": [c.name for c in collectors],
               "fred_key": bool(settings.fred_api_key), "demo": settings.demo_mode},
    )
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        await http.aclose()
        await db.close()


app = FastAPI(
    title="Acompanhamento do Bitcoin — API",
    description="Snapshot consolidado do mercado de Bitcoin a partir de APIs públicas.",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(router)
# Montado por último: tudo que não é /api ou /docs cai no front estático.
app.mount("/", StaticFiles(directory=BASE_DIR / "web", html=True), name="web")
