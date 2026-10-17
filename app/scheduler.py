"""Agendamento periódico dos coletores e persistência dos resultados."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.collectors.base import BaseCollector
from app.core.db import Database

log = logging.getLogger("scheduler")


async def run_collector(collector: BaseCollector, db: Database) -> bool:
    started = time.monotonic()
    try:
        result = await collector.collect()
    except Exception as exc:  # noqa: BLE001 — qualquer falha vira status de erro no card
        await db.save_failure(collector.name, f"{type(exc).__name__}: {exc}")
        log.error(
            "coleta falhou",
            extra={"collector": collector.name, "error": str(exc), "ms": round((time.monotonic() - started) * 1000)},
        )
        return False

    await db.save_success(collector.name, result.data)
    if result.series:
        await db.upsert_series(result.series)
    for batch in result.candles:
        await db.upsert_candles(batch.symbol, batch.interval, batch.rows)
    log.info(
        "coleta ok",
        extra={
            "collector": collector.name,
            "series_points": len(result.series),
            "ms": round((time.monotonic() - started) * 1000),
        },
    )
    return True


def build_scheduler(collectors: list[BaseCollector], db: Database) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    now = datetime.now(timezone.utc)
    for i, c in enumerate(collectors):
        scheduler.add_job(
            run_collector,
            "interval",
            seconds=c.interval_seconds,
            args=[c, db],
            id=c.name,
            # primeira execução logo no startup, escalonada para não disparar tudo junto
            next_run_time=now + timedelta(seconds=i * 0.5),
            max_instances=1,
            coalesce=True,
            misfire_grace_time=max(30, c.interval_seconds // 2),
            jitter=min(10, c.interval_seconds // 10),
        )
    return scheduler
