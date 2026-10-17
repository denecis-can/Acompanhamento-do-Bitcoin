"""Binance USDⓈ-M Futures: funding, open interest (atual e 24h atrás) e long/short global."""

from __future__ import annotations

from app.collectors.base import BaseCollector, CollectorError, CollectorResult

BASE = "https://fapi.binance.com"
SYMBOL = "BTCUSDT"


def parse(premium: dict, oi: dict, oi_hist: list, long_short: list) -> dict:
    if not oi_hist or not long_short:
        raise CollectorError("histórico de OI ou long/short vazio")
    mark = float(premium["markPrice"])
    oi_btc = float(oi["openInterest"])
    oi_24h_ago = oi_hist[0]  # limit=25 com period=1h → primeiro ponto ≈ 24h atrás
    ls = long_short[-1]
    return {
        "funding_rate": float(premium["lastFundingRate"]),
        "next_funding_time": int(premium["nextFundingTime"]),
        "mark_price": mark,
        "index_price": float(premium["indexPrice"]),
        "oi_btc": oi_btc,
        "oi_usd": oi_btc * mark,
        "oi_usd_24h_ago": float(oi_24h_ago["sumOpenInterestValue"]),
        "oi_24h_ago_ts": int(oi_24h_ago["timestamp"]),
        "long_short_ratio": float(ls["longShortRatio"]),
        "long_account_pct": float(ls["longAccount"]) * 100,
        "short_account_pct": float(ls["shortAccount"]) * 100,
    }


class BinanceFuturesCollector(BaseCollector):
    name = "binance_futures"
    source = "Binance Futures"

    async def collect(self) -> CollectorResult:
        g = self.http.get_json
        premium = await g(f"{BASE}/fapi/v1/premiumIndex", params={"symbol": SYMBOL})
        oi = await g(f"{BASE}/fapi/v1/openInterest", params={"symbol": SYMBOL})
        oi_hist = await g(f"{BASE}/futures/data/openInterestHist", params={"symbol": SYMBOL, "period": "1h", "limit": 25})
        long_short = await g(
            f"{BASE}/futures/data/globalLongShortAccountRatio", params={"symbol": SYMBOL, "period": "1h", "limit": 1}
        )
        data = parse(premium, oi, oi_hist, long_short)
        ts = int(oi["time"]) // 1000
        return CollectorResult(
            data=data,
            series=[
                ("binance.funding_rate", ts, data["funding_rate"]),
                ("binance.oi_usd", ts, data["oi_usd"]),
                ("binance.long_short_ratio", ts, data["long_short_ratio"]),
            ],
        )
