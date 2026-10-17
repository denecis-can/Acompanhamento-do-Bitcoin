"""Canais de envio: Telegram (Bot API oficial) e webhook do n8n.

Os segredos (token, chat_id, URL do webhook) vêm só do .env e nunca aparecem em logs,
mensagens de erro ou no SQLite: erros passam por `redact` antes de sair daqui.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

from app.core.http import HttpClient, HttpError

log = logging.getLogger("alerts.channels")

TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_MAX_CHARS = 4096
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}


class DeliveryError(Exception):
    """Falha de envio, já sem segredos na mensagem."""


@dataclass(frozen=True)
class Message:
    html: str  # para o Telegram (parse_mode=HTML)
    payload: dict[str, Any]  # JSON para o n8n


class Channel(Protocol):
    name: str

    @property
    def enabled(self) -> bool: ...

    async def send(self, message: Message) -> None: ...


def redact(text: str, *secrets: str) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


class TelegramChannel:
    """Só envia. A estrutura para comandos (/status, /silenciar) está em `commands.py` e,
    quando for ligada, só aceitará mensagens do TELEGRAM_CHAT_ID (`CommandRouter.is_authorized`)."""

    name = "telegram"

    def __init__(self, http: HttpClient, token: str, chat_id: str) -> None:
        self.http = http
        self.token = token.strip()
        self.chat_id = chat_id.strip()

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    async def send(self, message: Message) -> None:
        text = message.html
        if len(text) > TELEGRAM_MAX_CHARS:
            text = text[: TELEGRAM_MAX_CHARS - 1] + "…"
        try:
            resp = await self.http.post_json(
                f"{TELEGRAM_API}/bot{self.token}/sendMessage",
                {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            )
            body = resp.json()
        except (HttpError, ValueError) as exc:
            raise DeliveryError(redact(str(exc), self.token, self.chat_id)) from None
        except Exception as exc:  # noqa: BLE001 — erro de transporte pode carregar a URL com o token
            raise DeliveryError(redact(f"{type(exc).__name__}: {exc}", self.token, self.chat_id)) from None
        if not body.get("ok"):
            raise DeliveryError(redact(f"Telegram: {body.get('error_code')} {body.get('description')}", self.token))


class N8nWebhookChannel:
    """POST do mesmo payload do alerta, em JSON, para N8N_WEBHOOK_URL (vazio = desativado)."""

    name = "n8n"

    def __init__(self, http: HttpClient, url: str) -> None:
        self.http = http
        self.url = url.strip()
        if self.url:
            parsed = urlparse(self.url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                log.warning("N8N_WEBHOOK_URL inválida; webhook desativado")
                self.url = ""
            elif parsed.hostname not in LOCAL_HOSTS:
                log.warning("N8N_WEBHOOK_URL não aponta para esta máquina", extra={"host": parsed.hostname})

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    async def send(self, message: Message) -> None:
        try:
            await self.http.post_json(self.url, message.payload)
        except Exception as exc:  # noqa: BLE001 — a URL do webhook também é segredo
            raise DeliveryError(redact(f"{type(exc).__name__}: {exc}", self.url)) from None


class DemoChannel:
    """Em DEMO_MODE: registra, nunca envia."""

    def __init__(self, name: str) -> None:
        self.name = name

    @property
    def enabled(self) -> bool:
        return False

    async def send(self, message: Message) -> None:  # pragma: no cover — nunca chamado
        raise DeliveryError("modo demonstração: envio desativado")
