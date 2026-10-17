"""Binance USDⓈ-M Futures: funding (atual e 30 dias), open interest (atual e 24h atrás) e long/short global."""

from __future__ import annotations

from app.collectors.base import BaseCollector, CollectorError, CollectorResult
from app.services.indicators import zscore

BASE = "https://fapi.binance.com"
SYMBOL = "BTCUSDT"
FUNDING_HISTORY = 90  # 3 por dia → 30 dias


def funding_stats(current: float, history: list) -> dict:
    """Média e z-score do funding atual contra os últimos 30 dias de funding liquidado."""
    rates = [float(h["fundingRate"]) for h in history]
    if not rates:
        return {"funding_30d_mean": None, "funding_zscore": None}
    return {"funding_30d_mean": sum(rates) / len(rates), "funding_zscore": zscore(current, rates)}


def parse(premium: dict, oi: dict, oi_hist: list, long_short: list, funding_hist: list | None = None) -> dict:
    if not oi_hist or not long_short:
        raise CollectorError("histórico de OI ou long/short vazio")
    mark = float(premium["markPrice"])
    oi_btc = float(oi["openInterest"])
    oi_24h_ago = oi_hist[0]  # limit=25 com period=1h → primeiro ponto ≈ 24h atrás
    ls = long_short[-1]
    funding = float(premium["lastFundingRate"])
    return {
        "funding_rate": funding,
        **funding_stats(funding, funding_hist or []),
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
        funding_hist = await g(f"{BASE}/fapi/v1/fundingRate", params={"symbol": SYMBOL, "limit": FUNDING_HISTORY})
        data = parse(premium, oi, oi_hist, long_short, funding_hist)
        ts = int(oi["time"]) // 1000
        return CollectorResult(
            data=data,
            series=[
                ("binance.funding_rate", ts, data["funding_rate"]),
                ("binance.oi_usd", ts, data["oi_usd"]),
                ("binance.long_short_ratio", ts, data["long_short_ratio"]),
            ],
        )
