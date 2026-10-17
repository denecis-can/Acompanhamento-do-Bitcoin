"""Bybit v5 (perpétuo linear BTCUSDT): segunda fonte de funding e open interest."""

from __future__ import annotations

from app.collectors.base import BaseCollector, CollectorError, CollectorResult

URL = "https://api.bybit.com/v5/market/tickers"


def parse(raw: dict) -> dict:
    if raw.get("retCode") != 0:
        raise CollectorError(f"Bybit retCode={raw.get('retCode')} {raw.get('retMsg')}")
    items = raw["result"]["list"]
    if not items:
        raise CollectorError("Bybit: lista de tickers vazia")
    t = items[0]
    return {
        "price": float(t["lastPrice"]),
        "mark_price": float(t["markPrice"]),
        "change_24h_pct": float(t["price24hPcnt"]) * 100,
        "funding_rate": float(t["fundingRate"]),
        "next_funding_time": int(t["nextFundingTime"]),
        "oi_btc": float(t["openInterest"]),
        "oi_usd": float(t["openInterestValue"]),
        "volume_usd_24h": float(t["turnover24h"]),
    }


class BybitCollector(BaseCollector):
    name = "bybit"
    source = "Bybit"

    async def collect(self) -> CollectorResult:
        raw = await self.http.get_json(URL, params={"category": "linear", "symbol": "BTCUSDT"})
        data = parse(raw)
        ts = int(raw["time"]) // 1000
        return CollectorResult(
            data=data,
            series=[("bybit.funding_rate", ts, data["funding_rate"]), ("bybit.oi_usd", ts, data["oi_usd"])],
        )
