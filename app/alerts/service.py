"""Ciclo de alertas: monta o summary, avalia as regras, registra e entrega.

Cada disparo vira uma linha em alert_events com o status:
- `enviado`: todos os canais habilitados da regra aceitaram;
- `parcial` / `falhou`: algum canal (ou todos) falhou; reenviado pelo job de retry;
- `suprimido`: cruzamento dentro do cooldown, não enviado;
- `sem_canal`: nenhum canal da regra está configurado no .env;
- `demo`: DEMO_MODE, nunca enviado.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from zoneinfo import ZoneInfo

from app.alerts.channels import Channel, DeliveryError, Message
from app.alerts.engine import evaluate
from app.alerts.messages import alert_message, daily_message
from app.alerts.rules import Rule
from app.collectors.base import BaseCollector
from app.core.db import Database
from app.services.summary import get_summary

log = logging.getLogger("alerts")

MAX_ATTEMPTS = 5
RETRY_WINDOW = 24 * 3600
SENT, PARTIAL, FAILED, SUPPRESSED, NO_CHANNEL, DEMO = "enviado", "parcial", "falhou", "suprimido", "sem_canal", "demo"
DAILY_KEY = "resumo-diario"


def overall_status(deliveries: dict[str, str]) -> str:
    sent = [c for c, s in deliveries.items() if s == SENT]
    failed = [c for c, s in deliveries.items() if s.startswith(FAILED)]
    if failed:
        return PARTIAL if sent else FAILED
    return SENT if sent else NO_CHANNEL


class AlertService:
    def __init__(
        self,
        db: Database,
        collectors: list[BaseCollector],
        rules: list[Rule],
        channels: dict[str, Channel],
        *,
        tz: ZoneInfo,
        dashboard_url: str,
        demo: bool = False,
    ) -> None:
        self.db = db
        self.collectors = collectors
        self.rules = rules
        self.channels = channels
        self.tz = tz
        self.dashboard_url = dashboard_url
        self.demo = demo

    def channel_status(self) -> dict[str, bool]:
        """Quais canais estão configurados (sem expor os valores)."""
        return {name: ch.enabled for name, ch in self.channels.items()}

    async def run_cycle(self, now: float | None = None, summary: dict[str, Any] | None = None) -> list[dict]:
        now = now or time.time()
        summary = summary or await get_summary(self.db, self.collectors)
        states = await self.db.get_alert_states()
        fires, new_states = evaluate(self.rules, summary, states, now)
        for key, state in new_states.items():
            await self.db.save_alert_state(key, state)

        events = []
        for fire in fires:
            text, payload = alert_message(fire, summary, now, self.tz, self.dashboard_url, self.demo)
            event = {
                "id": payload["event_id"], "rule_key": fire.key, "kind": "alert", "fired_at": now,
                "message": text, "payload": payload, "deliveries": {}, "attempts": 0,
            }
            if fire.suppressed:
                event["status"] = SUPPRESSED
            else:
                event["deliveries"], event["status"] = await self.deliver(Message(text, payload), fire.rule.channels)
                event["attempts"] = 1
            await self.db.insert_alert_event(event)
            log.info("alerta", extra={"rule": fire.key, "status": event["status"], "value": fire.value})
            events.append(event)
        return events

    async def send_daily_summary(self, now: float | None = None, summary: dict[str, Any] | None = None) -> dict:
        """Resumo diário para todos os canais configurados; registrado em alert_events."""
        now = now or time.time()
        summary = summary or await get_summary(self.db, self.collectors)
        recent = [
            e for e in await self.db.get_alert_events(now - 24 * 3600)
            if e["kind"] == "alert" and e["status"] != SUPPRESSED
        ]
        text, payload = daily_message(summary, recent, now, self.tz, self.dashboard_url, self.demo)
        deliveries, status = await self.deliver(Message(text, payload), tuple(self.channels))
        event = {
            "id": payload["event_id"], "rule_key": DAILY_KEY, "kind": "daily_summary", "fired_at": now,
            "status": status, "message": text, "payload": payload, "deliveries": deliveries, "attempts": 1,
        }
        await self.db.insert_alert_event(event)
        log.info("resumo diário", extra={"status": status, "alerts_24h": len(recent)})
        return event

    async def deliver(self, message: Message, channel_names: tuple[str, ...] | list[str]) -> tuple[dict[str, str], str]:
        if self.demo:
            return {name: DEMO for name in channel_names}, DEMO
        deliveries: dict[str, str] = {}
        for name in channel_names:
            channel = self.channels.get(name)
            if channel is None or not channel.enabled:
                deliveries[name] = "desativado"
                continue
            try:
                await channel.send(message)
                deliveries[name] = SENT
            except DeliveryError as exc:
                deliveries[name] = f"{FAILED}: {exc}"[:300]
                log.warning("envio falhou", extra={"channel": name, "error": str(exc)})
        return deliveries, overall_status(deliveries)

    async def retry_failed(self, now: float | None = None) -> int:
        """Reenvia, só pelos canais que falharam, eventos das últimas 24h (até 5 tentativas)."""
        if self.demo:
            return 0
        now = now or time.time()
        retried = 0
        for status in (FAILED, PARTIAL):
            for event in await self.db.get_alert_events(now - RETRY_WINDOW, status=status):
                if event["attempts"] >= MAX_ATTEMPTS:
                    continue
                pending = [c for c, s in event["deliveries"].items() if s.startswith(FAILED)]
                redo, _ = await self.deliver(Message(event["message"], event["payload"]), pending)
                deliveries = {**event["deliveries"], **redo}
                await self.db.update_alert_delivery(event["id"], overall_status(deliveries), deliveries, event["attempts"] + 1)
                retried += 1
        return retried
