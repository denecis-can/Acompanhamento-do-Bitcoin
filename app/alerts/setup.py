"""Monta o serviço de alertas a partir das configurações e agenda os jobs."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.alerts.channels import Channel, DemoChannel, N8nWebhookChannel, TelegramChannel
from app.alerts.rules import RuleError, load_rules
from app.alerts.service import AlertService
from app.collectors.base import BaseCollector
from app.config import Settings
from app.core.db import Database

log = logging.getLogger("alerts")

FIRST_CHECK_DELAY = 90  # s: deixa os coletores rodarem antes da primeira avaliação
RETRY_INTERVAL = 300


def build_alert_service(settings: Settings, db: Database, collectors: list[BaseCollector], http) -> AlertService | None:
    path = settings.alerts_file
    if not path.exists():
        log.warning("arquivo de regras não encontrado; alertas desativados", extra={"file": str(path)})
        return None
    try:
        rules = load_rules(path)
    except RuleError as exc:
        log.error("regras de alerta inválidas; alertas desativados", extra={"file": str(path), "error": str(exc)})
        return None

    channels: dict[str, Channel]
    if settings.demo_mode:
        channels = {"telegram": DemoChannel("telegram"), "n8n": DemoChannel("n8n")}
    else:
        channels = {
            "telegram": TelegramChannel(http, settings.telegram_bot_token, settings.telegram_chat_id),
            "n8n": N8nWebhookChannel(http, settings.n8n_webhook_url),
        }
    service = AlertService(
        db, collectors, rules, channels,
        tz=ZoneInfo(settings.timezone), dashboard_url=settings.dashboard_url, demo=settings.demo_mode,
    )
    log.info("alertas ativos", extra={"rules": len(rules), "channels": service.channel_status(), "demo": settings.demo_mode})
    return service


def schedule_alert_jobs(scheduler: AsyncIOScheduler, service: AlertService, settings: Settings) -> None:
    now = datetime.now(timezone.utc)
    scheduler.add_job(
        service.run_cycle, "interval", seconds=settings.alerts_interval, id="alerts",
        next_run_time=now + timedelta(seconds=FIRST_CHECK_DELAY), max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        service.retry_failed, "interval", seconds=RETRY_INTERVAL, id="alerts-retry",
        next_run_time=now + timedelta(seconds=RETRY_INTERVAL), max_instances=1, coalesce=True,
    )
