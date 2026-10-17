"""Binance Spot: ticker 24h e candles BTCUSDT.

Se api.binance.com falhar, tenta o espelho público de market data
(data-api.binance.vision), que também atende regiões onde o domínio principal é bloqueado.
"""

from __future__ import annotations

import logging

from app.collectors.base import BaseCollector, CandleBatch, CollectorResult
from app.core.http import HttpError

log = logging.getLogger("collector.binance_spot")

HOSTS = ("https://api.binance.com", "https://data-api.binance.vision")
SYMBOL = "BTCUSDT"
# intervalo → (limite na carga inicial, limite nas coletas seguintes)
CANDLE_INTERVALS = {"1h": (500, 3), "1d": (400, 2)}


def parse_ticker(raw: dict) -> dict:
    return {
        "price": float(raw["lastPrice"]),
        "change_24h_pct": float(raw["priceChangePercent"]),
        "high_24h": float(raw["highPrice"]),
        "low_24h": float(raw["lowPrice"]),
        "volume_btc_24h": float(raw["volume"]),
        "volume_usd_24h": float(raw["quoteVolume"]),
    }


def parse_klines(raw: list) -> list[tuple[int, float, float, float, float, float]]:
    return [(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])) for k in raw]


class BinanceSpotCollector(BaseCollector):
    name = "binance_spot"
    source = "Binance Spot"

    def __init__(self, http, interval_seconds: int) -> None:
        super().__init__(http, interval_seconds)
        self._backfilled = False

    async def _get(self, path: str, params: dict) -> tuple[object, str]:
        last_exc: Exception | None = None
        for host in HOSTS:
            try:
                return await self.http.get_json(host + path, params=params), host
            except HttpError as exc:
                last_exc = exc
                log.warning("host falhou, tentando próximo", extra={"host": host, "error": str(exc)})
        raise last_exc  # type: ignore[misc]

    async def collect(self) -> CollectorResult:
        ticker_raw, host = await self._get("/api/v3/ticker/24hr", {"symbol": SYMBOL})
        data = parse_ticker(ticker_raw)  # type: ignore[arg-type]
        data["host"] = host.removeprefix("https://")

        batches = []
        for interval, (initial, incremental) in CANDLE_INTERVALS.items():
            limit = incremental if self._backfilled else initial
            raw, _ = await self._get("/api/v3/klines", {"symbol": SYMBOL, "interval": interval, "limit": limit})
            batches.append(CandleBatch(SYMBOL, interval, parse_klines(raw)))  # type: ignore[arg-type]
        self._backfilled = True

        return CollectorResult(data=data, candles=batches)
