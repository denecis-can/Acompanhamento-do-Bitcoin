"""Linha de comando dos alertas, para conferir a configuração do .env.

    python -m app.alerts teste    # mensagem de teste em cada canal configurado
    python -m app.alerts resumo   # envia o resumo diário agora (com os dados do banco local)

Não exige o servidor rodando. Usa o mesmo .env e o mesmo banco do dashboard.
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone

from app.alerts.channels import Message
from app.alerts.setup import build_alert_service
from app.collectors import build_collectors
from app.config import get_settings
from app.core.db import Database
from app.core.http import HttpClient
from app.demo import DemoHttpClient, demo_settings

USAGE = "uso: python -m app.alerts [teste|resumo]"


async def main(command: str) -> int:
    settings = get_settings()
    if settings.demo_mode:
        settings = demo_settings(settings)
        http = DemoHttpClient()
    else:
        http = HttpClient(timeout=settings.http_timeout, max_retries=1)
    db = Database(settings.database_path)
    await db.connect()
    try:
        service = build_alert_service(settings, db, build_collectors(http, settings), http)
        if service is None:
            print(f"Alertas desativados: confira {settings.alerts_file}")
            return 1
        configured = [name for name, on in service.channel_status().items() if on]
        if not configured and not settings.demo_mode:
            print("Nenhum canal configurado. Preencha TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID e/ou N8N_WEBHOOK_URL no .env.")
            return 1
        if command == "teste":
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            text = "<b>BTC Dashboard</b>\nMensagem de teste: o canal está configurado."
            payload = {"schema": "btc-dashboard.alert.v1", "type": "test", "sent_at": now, "message": "Mensagem de teste"}
            deliveries, status = await service.deliver(Message(text, payload), tuple(service.channels))
        else:
            event = await service.send_daily_summary(now=time.time())
            deliveries, status = event["deliveries"], event["status"]
        for name, result in deliveries.items():
            print(f"{name}: {result}")
        print(f"resultado: {status}")
        return 0 if status in ("enviado", "demo") else 1
    finally:
        await http.aclose()
        await db.close()


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("teste", "resumo"):
        print(USAGE)
        sys.exit(2)
    sys.exit(asyncio.run(main(sys.argv[1])))
