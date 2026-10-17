"""Resumo diário: conteúdo, alertas das últimas 24h e agendamento às 08:00 de São Paulo."""

import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import respx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.alerts.channels import N8nWebhookChannel, TelegramChannel
from app.alerts.service import AlertService
from app.alerts.setup import parse_hhmm, schedule_alert_jobs
from app.core.db import Database
from app.services.regime import compute_regime

TZ = ZoneInfo("America/Sao_Paulo")
TOKEN, CHAT, HOOK = "1:tok", "42", "http://localhost:5678/webhook/btc"
NOW = datetime(2026, 10, 18, 8, 0, tzinfo=TZ).timestamp()


def market_summary():
    s = {
        "price": {"value": 81_573.79, "change_24h_pct": -0.89},
        "derivatives": {"weighted_funding": {"rate_pct": 0.0094, "annualized_pct": 10.3}},
        "volatility": {"dvol": 37.4, "dvol_change_24h": 0.26},
        "etf": {"last_day": {"date": "2026-10-16", "total": -82.4}, "sum_5d": -225.6, "source": "SoSoValue"},
        "sentiment": {"value": 55},
        "trend": {"mayer_multiple": 1.03},
    }
    s["regime"] = compute_regime({"funding": 10.3, "fear_greed": 55, "mayer": 1.03, "etf_5d": -225.6, "mvrv": 1.51})
    return s


def alert_event(eid, fired_at, status="enviado", name="Funding médio ponderado alto", headline="Valor: +0,061% (limite > +0,050%)"):
    payload = {"rule": {"id": "funding-alto", "name": name}, "value": 0.061, "fired_at": "x", "headline": headline}
    return {"id": eid, "rule_key": "funding-alto", "kind": "alert", "fired_at": fired_at, "status": status,
            "message": "m", "payload": payload, "deliveries": {}, "attempts": 1}


@pytest.fixture
async def db():
    d = Database(":memory:")
    await d.connect()
    yield d
    await d.close()


@respx.mock
async def test_daily_summary_content_and_delivery(http, db):
    tg = respx.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage").respond(json={"ok": True})
    hook = respx.post(HOOK).respond(200)
    await db.insert_alert_event(alert_event("a1", NOW - 2 * 3600))
    await db.insert_alert_event(alert_event("a2", NOW - 3 * 3600, status="suprimido"))   # não entra
    await db.insert_alert_event(alert_event("a3", NOW - 30 * 3600))                       # mais de 24h
    channels = {"telegram": TelegramChannel(http, TOKEN, CHAT), "n8n": N8nWebhookChannel(http, HOOK)}
    svc = AlertService(db, [], [], channels, tz=TZ, dashboard_url="http://localhost:8000")

    event = await svc.send_daily_summary(now=NOW, summary=market_summary())

    text = json.loads(tg.calls[0].request.content)["text"]
    assert "<b>Resumo diário — BTC</b> · 18/10/2026" in text
    assert "Temperatura do mercado: <b>" in text and "• Mayer Multiple: 1,03× (nota" in text
    assert "Sem dados:" in text                                   # componentes ausentes ficam explícitos
    assert "Preço: <b>US$ 81.574</b> (−0,89% em 24h)" in text
    assert "Funding médio ponderado: +0,0094% por 8h (≈ +10,3% ao ano)" in text
    assert "DVOL: 37,40 (+0,26 pts em 24h)" in text
    assert "ETFs (16/10, último dia liquidado): −US$ 82,4 mi · 5 pregões: −US$ 225,6 mi · fonte: SoSoValue" in text
    assert "Alertas nas últimas 24h: 1" in text
    assert "• 18/10 06:00 Funding médio ponderado alto — Valor: +0,061% (limite &gt; +0,050%)" in text

    payload = json.loads(hook.calls[0].request.content)
    assert payload["type"] == "daily_summary" and payload["alerts_24h"][0]["rule_id"] == "funding-alto"
    assert payload["etf"]["last_day"]["date"] == "2026-10-16"
    assert event["status"] == "enviado"
    stored = await db.get_alert_events(NOW - 60)
    assert [e["kind"] for e in stored] == ["daily_summary"]


async def test_daily_summary_without_alerts_or_data(http, db):
    svc = AlertService(db, [], [], {}, tz=TZ, dashboard_url="http://localhost:8000")
    event = await svc.send_daily_summary(now=NOW, summary={"regime": {}})
    assert "Temperatura do mercado: dados insuficientes" in event["message"]
    assert "Alertas nas últimas 24h: nenhum" in event["message"]
    assert event["status"] == "sem_canal"


def test_parse_hhmm():
    assert parse_hhmm("08:00") == (8, 0) and parse_hhmm(" 21:30 ") == (21, 30)
    assert parse_hhmm("") is None
    for bad in ("8h", "25:00", "08:61", "08:00:00"):
        with pytest.raises(ValueError):
            parse_hhmm(bad)


def _settings(time="08:00", tz="America/Sao_Paulo"):
    return SimpleNamespace(alerts_interval=60, daily_summary_time=time, timezone=tz)


def test_daily_job_runs_at_8am_sao_paulo():
    scheduler = AsyncIOScheduler(timezone="UTC")
    svc = SimpleNamespace(run_cycle=lambda: None, send_daily_summary=lambda: None, retry_failed=lambda: None)
    schedule_alert_jobs(scheduler, svc, _settings())

    trigger = scheduler.get_job("daily-summary").trigger
    after = datetime(2026, 10, 17, 9, 0, tzinfo=TZ)
    nxt = trigger.get_next_fire_time(None, after)
    assert nxt == datetime(2026, 10, 18, 8, 0, tzinfo=TZ)
    assert nxt.utcoffset().total_seconds() == -3 * 3600            # 08:00 em São Paulo = 11:00 UTC


def test_invalid_or_empty_time_disables_only_the_summary():
    for value in ("", "8 da manhã"):
        scheduler = AsyncIOScheduler(timezone="UTC")
        svc = SimpleNamespace(run_cycle=lambda: None, send_daily_summary=lambda: None, retry_failed=lambda: None)
        schedule_alert_jobs(scheduler, svc, _settings(time=value))
        assert scheduler.get_job("daily-summary") is None
        assert scheduler.get_job("alerts") is not None
