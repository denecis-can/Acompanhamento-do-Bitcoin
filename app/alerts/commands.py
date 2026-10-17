"""Estrutura para comandos do Telegram (/status, /silenciar). AINDA NÃO LIGADA.

Nesta fase o bot só envia. Quando os comandos forem implementados, o fluxo será:
receber updates (getUpdates ou webhook) → `CommandRouter.handle(update)` → resposta.
Regra de segurança já garantida aqui: só mensagens do TELEGRAM_CHAT_ID configurado são
tratadas; qualquer outro chat é ignorado em silêncio (sem resposta que revele o bot).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

Handler = Callable[[list[str], dict[str, Any]], Awaitable[str]]


class CommandRouter:
    def __init__(self, allowed_chat_id: str) -> None:
        self.allowed_chat_id = allowed_chat_id.strip()
        self.handlers: dict[str, Handler] = {}

    def command(self, name: str) -> Callable[[Handler], Handler]:
        def register(fn: Handler) -> Handler:
            self.handlers[name.lstrip("/").lower()] = fn
            return fn

        return register

    def is_authorized(self, update: dict[str, Any]) -> bool:
        chat = ((update.get("message") or {}).get("chat") or {}).get("id")
        return bool(self.allowed_chat_id) and chat is not None and str(chat) == self.allowed_chat_id

    async def handle(self, update: dict[str, Any]) -> str | None:
        """Resposta para o update, ou None (chat não autorizado, não é comando, comando desconhecido)."""
        if not self.is_authorized(update):
            return None
        text = ((update.get("message") or {}).get("text") or "").strip()
        if not text.startswith("/"):
            return None
        name, *args = text[1:].split()
        handler = self.handlers.get(name.split("@")[0].lower())
        return await handler(args, update) if handler else None
