"""Cliente HTTP compartilhado: timeout, retry com backoff exponencial e rate limit por host."""

from __future__ import annotations

import asyncio
import logging
import random
import ssl
import time
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

import httpx

log = logging.getLogger("http")

DEFAULT_HEADERS = {
    "User-Agent": "btc-dashboard/1.0 (+https://github.com/)",
    "Accept": "application/json",
}

# Intervalo mínimo entre requests ao mesmo host (segundos). Bem abaixo dos limites
# públicos de cada API; serve para não disparar rajadas quando vários jobs coincidem.
HOST_MIN_INTERVAL = {
    "api.binance.com": 0.1,
    "data-api.binance.vision": 0.1,
    "fapi.binance.com": 0.2,
    "api.bybit.com": 0.2,
    "www.okx.com": 0.2,
    "www.deribit.com": 0.2,
    "api.exchange.coinbase.com": 0.2,
    "api.alternative.me": 1.0,
    "api.stlouisfed.org": 0.6,  # FRED: 120 req/min
    "www.federalreserve.gov": 2.0,
    "openapi.sosovalue.com": 3.5,  # 20 req/min por chave; ~17/min para ter folga
    "mempool.space": 0.5,
    "community-api.coinmetrics.io": 1.0,  # plano gratuito: 10 req a cada 6 s
    "stablecoins.llama.fi": 1.0,
    "api.telegram.org": 1.0,  # limite do Telegram: ~1 mensagem/s por chat
}

DEFAULT_MIN_INTERVAL = 0.2

RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Teto para respostas lidas como texto (HTML/XML de terceiros).
MAX_TEXT_BYTES = 5 * 1024 * 1024

TEXT_ACCEPT = "text/html,application/xhtml+xml,application/rss+xml,application/xml;q=0.9,*/*;q=0.8"


@lru_cache
def _ssl_context() -> ssl.SSLContext:
    # Carregar o bundle de certificados custa ~1s no Windows; fazemos uma vez só.
    import certifi

    return ssl.create_default_context(cafile=certifi.where())


class HttpError(Exception):
    """Falha definitiva após esgotar as tentativas."""


class HttpClient:
    def __init__(
        self,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        throttle: bool = True,
    ) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout, headers=DEFAULT_HEADERS, follow_redirects=True, verify=_ssl_context()
        )
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.throttle = throttle
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_call: dict[str, float] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _throttle(self, host: str) -> None:
        if not self.throttle:
            return
        min_interval = HOST_MIN_INTERVAL.get(host, DEFAULT_MIN_INTERVAL)
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._last_call.get(host, 0.0) + min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call[host] = time.monotonic()

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), 60.0)
            except ValueError:
                pass
        # Exponencial com jitter: base * 2^n * [0.5, 1.5)
        return self.backoff_base * (2**attempt) * (0.5 + random.random())

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        host = urlparse(url).hostname or ""
        last_error: str = ""
        for attempt in range(self.max_retries + 1):
            await self._throttle(host)
            started = time.monotonic()
            try:
                resp = await self._client.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                retry_after = None
            else:
                elapsed_ms = round((time.monotonic() - started) * 1000)
                if resp.status_code < 400:
                    log.debug("http ok", extra={"host": host, "status": resp.status_code, "ms": elapsed_ms})
                    return resp
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                if resp.status_code not in RETRYABLE_STATUS:
                    break
                retry_after = resp.headers.get("Retry-After")

            if attempt < self.max_retries:
                delay = self._backoff(attempt, retry_after)
                log.warning(
                    "http retry",
                    extra={"host": host, "attempt": attempt + 1, "delay_s": round(delay, 2), "error": last_error},
                )
                await asyncio.sleep(delay)

        raise HttpError(f"{host}: {last_error}")

    async def get_json(self, url: str, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        resp = await self.request("GET", url, params=params, **kwargs)
        try:
            return resp.json()
        except ValueError as exc:
            raise HttpError(f"{url}: resposta não é JSON válido") from exc

    async def post_json(self, url: str, payload: Any, **kwargs: Any) -> httpx.Response:
        """POST com corpo JSON. Mesmo retry/backoff do GET (429 e 5xx)."""
        return await self.request("POST", url, json=payload, **kwargs)

    async def get_text(self, url: str, params: dict[str, Any] | None = None, **kwargs: Any) -> str:
        """GET para HTML/XML. Recusa respostas acima de MAX_TEXT_BYTES."""
        resp = await self.request("GET", url, params=params, headers={"Accept": TEXT_ACCEPT}, **kwargs)
        if len(resp.content) > MAX_TEXT_BYTES:
            raise HttpError(f"{url}: resposta grande demais ({len(resp.content)} bytes)")
        return resp.text
