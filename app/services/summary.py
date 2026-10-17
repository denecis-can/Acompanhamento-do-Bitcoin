"""Monta o snapshot consolidado servido em GET /api/summary."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from app.collectors.base import BaseCollector
from app.core.db import Database
from app.services import indicators
from app.services.status import compute_status, is_usable

DISCLAIMER = "Ferramenta informativa. Não é recomendação de investimento."

# Ordem de preferência do preço principal (todas em USDT).
PRICE_PRIORITY = ("binance_spot", "okx", "bybit")


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds") if ts else None


def build_sources(latest: dict[str, dict], collectors: list[BaseCollector], now: float) -> dict[str, dict]:
    out = {}
    for c in collectors:
        entry = latest.get(c.name)
        out[c.name] = {
            "source": c.source,
            "status": compute_status(entry, c.stale_after, now),
            "interval_seconds": c.interval_seconds,
            "updated_at": _iso(entry["fetched_at"]) if entry else None,
            "last_attempt_at": _iso(entry["last_attempt"]) if entry else None,
            "error": entry["error"] if entry and not entry["ok"] else None,
        }
    return out


def _meta(sources: dict, name: str) -> dict:
    s = sources[name]
    return {"source": s["source"], "status": s["status"], "updated_at": s["updated_at"]}


def build_summary(latest: dict[str, dict], collectors: list[BaseCollector], now: float | None = None) -> dict[str, Any]:
    now = now or time.time()
    sources = build_sources(latest, collectors, now)

    def data(name: str) -> dict | None:
        if name in sources and is_usable(sources[name]["status"]):
            return latest[name]["data"]
        return None

    # --- preço (com fallback) ---------------------------------------------
    price: dict[str, Any] = {"value": None, "status": "erro", "source": None, "updated_at": None}
    for name in PRICE_PRIORITY:
        d = data(name)
        if d:
            price = {
                **_meta(sources, name),
                "value": d["price"],
                "change_24h_pct": d.get("change_24h_pct"),
                "high_24h": d.get("high_24h"),
                "low_24h": d.get("low_24h"),
                "volume_usd_24h": d.get("volume_usd_24h"),
                "fallback": name != PRICE_PRIORITY[0],
            }
            break

    # --- Coinbase Premium ------------------------------------------------
    premium = None
    cb, bn = data("coinbase"), data("binance_spot")
    if cb and bn:
        premium = {
            **indicators.coinbase_premium(cb["price"], bn["price"]),
            "coinbase_price": cb["price"],
            "binance_price": bn["price"],
            "status": "ok" if sources["coinbase"]["status"] == sources["binance_spot"]["status"] == "ok" else "atrasado",
            "source": "Coinbase vs. Binance",
            "updated_at": sources["coinbase"]["updated_at"],
            "note": "USDT ≠ USD: pequenas variações do USDT afetam o prêmio.",
        }

    # --- derivativos -------------------------------------------------------
    per_exchange = {}
    for name in ("binance_futures", "bybit", "okx"):
        d = data(name)
        if d:
            per_exchange[name] = {
                **_meta(sources, name),
                "funding_rate_pct": d["funding_rate"] * 100,
                "next_funding_time": d.get("next_funding_time"),
                "oi_usd": d["oi_usd"],
                "oi_btc": d.get("oi_btc"),
            }

    bf = data("binance_futures")
    oi_change = None
    if bf:
        oi_change = {
            "binance_oi_usd": bf["oi_usd"],
            "binance_oi_usd_24h_ago": bf["oi_usd_24h_ago"],
            "change_24h_pct": indicators.pct_change(bf["oi_usd"], bf["oi_usd_24h_ago"]),
        }

    derivatives = {
        "exchanges": per_exchange,
        "weighted_funding": indicators.weighted_funding(
            (n, latest[n]["data"]["funding_rate"], latest[n]["data"]["oi_usd"]) for n in per_exchange
        ),
        "open_interest": oi_change,
        "long_short": (
            {
                **_meta(sources, "binance_futures"),
                "ratio": bf["long_short_ratio"],
                "long_pct": bf["long_account_pct"],
                "short_pct": bf["short_account_pct"],
                "scope": "Contas globais Binance Futures",
            }
            if bf
            else None
        ),
    }

    # --- volatilidade, sentimento, macro -----------------------------------
    dvol = data("deribit")
    volatility = {**_meta(sources, "deribit"), **(dvol or {})}

    fg = data("fear_greed")
    sentiment = {**_meta(sources, "fear_greed"), **(fg or {})}

    fred = data("fred")
    macro = {**_meta(sources, "fred"), **(fred or {"series": {}, "upcoming_releases": []})}
    if sources["fred"]["error"]:
        macro["error"] = sources["fred"]["error"]

    return {
        "generated_at": _iso(now),
        "disclaimer": DISCLAIMER,
        "price": price,
        "coinbase_premium": premium,
        "derivatives": derivatives,
        "volatility": volatility,
        "sentiment": sentiment,
        "macro": macro,
        "sources": sources,
    }


async def get_summary(db: Database, collectors: list[BaseCollector]) -> dict[str, Any]:
    return build_summary(await db.get_latest_all(), collectors)
