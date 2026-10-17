"""Rotas /api/alerts: estado das regras e histórico, sem expor segredos."""

import time
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI

from app.alerts.channels import N8nWebhookChannel, TelegramChannel
from app.alerts.rules import Rule
from app.alerts.service import AlertService
from app.api.routes import router
from app.core.db import Database

TOKEN, CHAT, HOOK = "999:super-secreto", "123456789", "http://localhost:5678/webhook/segredo-xyz"


@pytest.fixture
async def client(http):
    db = Database(":memory:")
    await db.connect()
    rule = Rule(id="funding-alto", name="Funding alto", type="threshold", path="x", op=">", value=0.05, unit="%")
    channels = {"telegram": TelegramChannel(http, TOKEN, CHAT), "n8n": N8nWebhookChannel(http, "")}
    svc = AlertService(db, [], [rule], channels, tz=ZoneInfo("America/Sao_Paulo"), dashboard_url="http://localhost:8000")
    app = FastAPI()
    app.include_router(router)
    app.state.db, app.state.alerts = db, svc
    await svc.run_cycle(now=1.0, summary={"x": 0.01})  # estado inicial
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c, db
    await db.close()


async def test_alerts_route_shows_rules_and_channel_flags_only(client):
    c, _ = client
    resp = await c.get("/api/alerts")
    body = resp.json()
    assert body["enabled"] is True and body["channels"] == {"telegram": True, "n8n": False}
    assert body["rules"][0]["condition"] == "> 0,05%" and body["rules"][0]["states"]["funding-alto"]["armed"] == 1
    for secret in (TOKEN, CHAT, HOOK):
        assert secret not in resp.text


async def test_alert_events_route(client):
    c, db = client
    await db.insert_alert_event({
        "id": "e1", "rule_key": "funding-alto", "kind": "alert", "fired_at": time.time(),
        "status": "enviado", "message": "m", "attempts": 1, "deliveries": {"telegram": "enviado"},
        "payload": {"rule": {"name": "Funding alto"}, "headline": "Valor: +0,061%"},
    })
    events = (await c.get("/api/alerts/events?hours=24")).json()["events"]
    assert events == [{
        "id": "e1", "kind": "alert", "rule_key": "funding-alto", "status": "enviado", "fired_at": events[0]["fired_at"],
        "deliveries": {"telegram": "enviado"}, "attempts": 1, "name": "Funding alto", "headline": "Valor: +0,061%",
    }]


async def test_alerts_disabled_without_service():
    app = FastAPI()
    app.include_router(router)
    app.state.alerts = None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/api/alerts")).json() == {"enabled": False, "rules": [], "channels": {}}
