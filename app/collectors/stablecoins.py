"""DefiLlama: oferta de stablecoins atreladas ao dólar (proxy de liquidez no mercado cripto)."""

from __future__ import annotations

from app.collectors.base import BaseCollector, CollectorError, CollectorResult

BASE = "https://stablecoins.llama.fi"
TRACKED = ("USDT", "USDC")
HISTORY_DAYS = 365
DEPEG_THRESHOLD = 0.005  # alerta se o preço se afastar mais de 0,5% de US$ 1


def _usd(field: dict | None) -> float:
    return float((field or {}).get("peggedUSD") or 0)


def _changes(asset: dict) -> dict:
    now = _usd(asset.get("circulating"))
    out = {"supply": now}
    for key, label in (("circulatingPrevDay", "1d"), ("circulatingPrevWeek", "7d"), ("circulatingPrevMonth", "30d")):
        prev = _usd(asset.get(key))
        out[f"change_{label}"] = now - prev if prev else None
        out[f"change_{label}_pct"] = (now / prev - 1) * 100 if prev else None
    return out


def parse_assets(raw: dict) -> dict:
    assets = [a for a in raw.get("peggedAssets", []) if a.get("pegType") == "peggedUSD"]
    if not assets:
        raise CollectorError("DefiLlama: lista de stablecoins vazia")

    total = {"circulating": {}, "circulatingPrevDay": {}, "circulatingPrevWeek": {}, "circulatingPrevMonth": {}}
    for key in total:
        total[key] = {"peggedUSD": sum(_usd(a.get(key)) for a in assets)}

    tracked = {}
    for a in assets:
        sym = a.get("symbol")
        if sym in TRACKED and sym not in tracked:
            price = a.get("price")
            tracked[sym] = {
                **_changes(a),
                "price": price,
                "depeg": price is not None and abs(price - 1) > DEPEG_THRESHOLD,
            }
    total_now = total["circulating"]["peggedUSD"]
    return {
        "total": _changes(total),
        "assets": tracked,
        "share_pct": {s: v["supply"] / total_now * 100 for s, v in tracked.items()} if total_now else {},
        "count": len(assets),
    }


def parse_history(raw: list) -> list[tuple[int, float]]:
    points = []
    for r in raw[-HISTORY_DAYS:]:
        value = _usd(r.get("totalCirculatingUSD")) or _usd(r.get("totalCirculating"))
        if value:
            points.append((int(r["date"]), value))
    return points


class StablecoinsCollector(BaseCollector):
    name = "stablecoins"
    source = "DefiLlama"

    async def collect(self) -> CollectorResult:
        assets = await self.http.get_json(f"{BASE}/stablecoins", params={"includePrices": "true"})
        history = await self.http.get_json(f"{BASE}/stablecoincharts/all")
        data = parse_assets(assets)
        points = parse_history(history)
        return CollectorResult(data=data, series=[("stables.total_usd", ts, v) for ts, v in points])
