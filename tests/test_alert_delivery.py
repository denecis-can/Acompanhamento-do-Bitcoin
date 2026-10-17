"""Envio de alertas: Telegram, webhook do n8n, registro no SQLite, demo e retry."""

import json
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from app.alerts.channels import DemoChannel, DeliveryError, Message, N8nWebhookChannel, TelegramChannel
from app.alerts.commands import CommandRouter
from app.alerts.messages import br, fmt_value, signed
from app.alerts.rules import Rule
from app.alerts.service import AlertService
from app.core.db import Database

TOKEN = "123456:ABC-segredo"
CHAT = "987654321"
HOOK = "http://localhost:5678/webhook/btc-alertas"
TG_URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
TZ = ZoneInfo("America/Sao_Paulo")


@pytest.fixture
async def db():
    d = Database(":memory:")
    await d.connect()
    yield d
    await d.close()


def summary(x, price=81_500.0):
    return {"x": x, "price": {"value": price, "change_24h_pct": -1.2}, "regime": {"score": -15.0, "label": "Neutro"}}


def service(db, http, *, demo=False, rules=None, telegram=True, n8n=True):
    channels = {
        "telegram": DemoChannel("telegram") if demo else TelegramChannel(http, TOKEN if telegram else "", CHAT),
        "n8n": DemoChannel("n8n") if demo else N8nWebhookChannel(http, HOOK if n8n else ""),
    }
    rule = Rule(id="teste", name="Funding <alto> & caro", type="threshold", path="x", op=">", value=10,
                hysteresis=2, unit="%", cooldown=4 * 3600)
    return AlertService(db, [], rules or [rule], channels, tz=TZ, dashboard_url="http://localhost:8000", demo=demo)


async def fire(svc, db):
    """Estado inicial abaixo do limite e depois um cruzamento."""
    await svc.run_cycle(now=1_000.0, summary=summary(5))
    return await svc.run_cycle(now=1_060.0, summary=summary(12))


# --- formatação --------------------------------------------------------------------------

def test_brazilian_number_format():
    assert br(81573.79) == "81.573,79" and signed(-0.89) == "−0,89" and signed(5.1, 1) == "+5,1"
    assert fmt_value(-412.34, "US$ mi") == "−US$ 412,3 mi"
    assert fmt_value(0.061, "%") == "+0,061%" and fmt_value(10.4, "pts") == "+10,4 pts"
    assert fmt_value(20, "") == "20"


# --- canais --------------------------------------------------------------------------------

@respx.mock
async def test_telegram_sends_escaped_html_to_configured_chat(http, db):
    tg = respx.post(TG_URL).respond(json={"ok": True, "result": {}})
    hook = respx.post(HOOK).respond(200)

    events = await fire(service(db, http), db)

    assert [e["status"] for e in events] == ["enviado"]
    body = json.loads(tg.calls[0].request.content)
    assert body["chat_id"] == CHAT and body["parse_mode"] == "HTML"
    assert "Funding &lt;alto&gt; &amp; caro" in body["text"]      # nome escapado
    assert "Valor: <b>+12,00%</b> (limite &gt; +10,00%)" in body["text"]
    assert "BTC US$ 81.500 (−1,20% 24h)" in body["text"]
    sent = json.loads(hook.calls[0].request.content)
    assert sent == events[0]["payload"]                            # n8n recebe o mesmo payload do alerta
    assert sent["schema"] == "btc-dashboard.alert.v1" and sent["rule"]["id"] == "teste" and sent["value"] == 12


@respx.mock
async def test_telegram_error_never_leaks_token(http):
    respx.post(TG_URL).respond(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})
    with pytest.raises(DeliveryError) as exc:
        await TelegramChannel(http, TOKEN, CHAT).send(Message("oi", {}))
    assert "401" in str(exc.value) and TOKEN not in str(exc.value)

    respx.post(TG_URL).mock(side_effect=httpx.ConnectError(f"falha ao conectar em {TG_URL}"))
    with pytest.raises(DeliveryError) as exc:
        await TelegramChannel(http, TOKEN, CHAT).send(Message("oi", {}))
    assert TOKEN not in str(exc.value) and "***" in str(exc.value)


def test_n8n_url_validation(http):
    assert not N8nWebhookChannel(http, "").enabled
    assert not N8nWebhookChannel(http, "ftp://localhost/x").enabled
    assert N8nWebhookChannel(http, HOOK).enabled
    assert N8nWebhookChannel(http, "http://host.docker.internal:5678/webhook/x").enabled


# --- registro no SQLite --------------------------------------------------------------------

@respx.mock
async def test_every_alert_is_recorded_and_not_repeated(http, db):
    respx.post(TG_URL).respond(json={"ok": True})
    respx.post(HOOK).respond(200)
    svc = service(db, http)

    await fire(svc, db)
    assert await svc.run_cycle(now=1_120.0, summary=summary(15)) == []  # continua acima: nada novo

    rows = await db.get_alert_events(0)
    assert len(rows) == 1
    assert rows[0]["status"] == "enviado" and rows[0]["deliveries"] == {"telegram": "enviado", "n8n": "enviado"}
    assert rows[0]["rule_key"] == "teste" and rows[0]["attempts"] == 1


@respx.mock
async def test_crossing_inside_cooldown_is_recorded_as_suppressed(http, db):
    tg = respx.post(TG_URL).respond(json={"ok": True})
    respx.post(HOOK).respond(200)
    svc = service(db, http)

    await fire(svc, db)
    await svc.run_cycle(now=1_200.0, summary=summary(5))      # rearma (abaixo de 8)
    events = await svc.run_cycle(now=1_300.0, summary=summary(12))  # 4 min depois: cooldown de 4h

    assert [e["status"] for e in events] == ["suprimido"]
    assert tg.call_count == 1                                   # o segundo não foi enviado
    assert [r["status"] for r in await db.get_alert_events(0)] == ["suprimido", "enviado"]


@respx.mock
async def test_no_configured_channel(http, db):
    events = await fire(service(db, http, telegram=False, n8n=False), db)
    assert events[0]["status"] == "sem_canal"
    assert events[0]["deliveries"] == {"telegram": "desativado", "n8n": "desativado"}
    assert len(respx.calls) == 0


@respx.mock  # sem rotas: qualquer envio real faria o teste falhar
async def test_demo_mode_records_but_never_sends(http, db):
    events = await fire(service(db, http, demo=True), db)
    assert events[0]["status"] == "demo" and events[0]["payload"]["demo"] is True
    assert events[0]["message"].startswith("[DEMO]")
    assert len(respx.calls) == 0


@respx.mock
async def test_retry_resends_only_failed_channel(http, db):
    tg = respx.post(TG_URL)
    tg.side_effect = [httpx.Response(400, json={"ok": False, "error_code": 400, "description": "Bad Request"}),
                      httpx.Response(200, json={"ok": True})]
    hook = respx.post(HOOK).respond(200)
    svc = service(db, http)

    events = await fire(svc, db)
    assert events[0]["status"] == "parcial" and events[0]["deliveries"]["telegram"].startswith("falhou")

    assert await svc.retry_failed(now=1_400.0) == 1
    row = (await db.get_alert_events(0))[0]
    assert row["status"] == "enviado" and row["attempts"] == 2
    assert hook.call_count == 1                                 # o n8n não recebe de novo


# --- estrutura dos comandos (ainda não ligada) ------------------------------------------------

async def test_command_router_answers_only_configured_chat():
    router = CommandRouter(CHAT)

    @router.command("/status")
    async def status(args, update):
        return "ok"

    def update(chat, text="/status"):
        return {"message": {"chat": {"id": chat}, "text": text}}

    assert await router.handle(update(int(CHAT))) == "ok"
    assert await router.handle(update(int(CHAT), "/status@meu_bot")) == "ok"
    assert await router.handle(update(111)) is None              # outro chat: ignorado
    assert await router.handle(update(int(CHAT), "olá")) is None
    assert await router.handle(update(int(CHAT), "/desconhecido")) is None
    assert await CommandRouter("").handle(update(int(CHAT))) is None  # sem chat configurado, nada responde
