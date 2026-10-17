"""OKX: preço spot BTC-USDT (fallback de preço) e funding/OI do perpétuo BTC-USDT-SWAP."""

from __future__ import annotations

from app.collectors.base import BaseCollector, CollectorError, CollectorResult

BASE = "https://www.okx.com/api/v5"


def _first(raw: dict, what: str) -> dict:
    if raw.get("code") != "0" or not raw.get("data"):
        raise CollectorError(f"OKX {what}: code={raw.get('code')} msg={raw.get('msg')}")
    return raw["data"][0]


def parse(ticker: dict, funding: dict, oi: dict) -> dict:
    t = _first(ticker, "ticker")
    f = _first(funding, "funding-rate")
    o = _first(oi, "open-interest")
    last, open_24h = float(t["last"]), float(t["open24h"])
    return {
        "price": last,
        "change_24h_pct": (last / open_24h - 1) * 100 if open_24h else None,
        "high_24h": float(t["high24h"]),
        "low_24h": float(t["low24h"]),
        "volume_usd_24h": float(t["volCcy24h"]),
        "funding_rate": float(f["fundingRate"]),
        "next_funding_time": int(f["fundingTime"]),
        "oi_btc": float(o["oiCcy"]),
        "oi_usd": float(o["oiUsd"]),
    }


class OkxCollector(BaseCollector):
    name = "okx"
    source = "OKX"

    async def collect(self) -> CollectorResult:
        g = self.http.get_json
        ticker = await g(f"{BASE}/market/ticker", params={"instId": "BTC-USDT"})
        funding = await g(f"{BASE}/public/funding-rate", params={"instId": "BTC-USDT-SWAP"})
        oi = await g(f"{BASE}/public/open-interest", params={"instType": "SWAP", "instId": "BTC-USDT-SWAP"})
        data = parse(ticker, funding, oi)
        ts = int(_first(oi, "open-interest")["ts"]) // 1000
        return CollectorResult(
            data=data,
            series=[("okx.funding_rate", ts, data["funding_rate"]), ("okx.oi_usd", ts, data["oi_usd"])],
        )
