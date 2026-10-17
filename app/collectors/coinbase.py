"""Coinbase Exchange: preço BTC-USD (referência do mercado americano)."""

from __future__ import annotations

from app.collectors.base import BaseCollector, CollectorResult

URL = "https://api.exchange.coinbase.com/products/BTC-USD/ticker"


def parse_ticker(raw: dict) -> dict:
    return {
        "price": float(raw["price"]),
        "bid": float(raw["bid"]),
        "ask": float(raw["ask"]),
        "volume_btc_24h": float(raw["volume"]),
        "exchange_time": raw.get("time"),
    }


class CoinbaseCollector(BaseCollector):
    name = "coinbase"
    source = "Coinbase Exchange"

    async def collect(self) -> CollectorResult:
        return CollectorResult(data=parse_ticker(await self.http.get_json(URL)))
