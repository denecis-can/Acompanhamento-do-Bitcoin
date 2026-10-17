"""Rotas HTTP. O front só conversa com estas rotas, nunca com APIs externas."""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from app.config import get_settings
from app.demo import BANNER
from app.services.summary import build_sources, get_summary

router = APIRouter(prefix="/api")

# Métricas de série expostas ao front (evita consultas arbitrárias no banco).
ALLOWED_SERIES = {
    "deribit.dvol",
    "deribit.put_call_oi",
    "deribit.atm_iv_30d",
    "deribit.skew_25d_30d",
    "deribit.basis_3m",
    "fng.value",
    "binance.funding_rate",
    "binance.oi_usd",
    "binance.long_short_ratio",
    "bybit.funding_rate",
    "bybit.oi_usd",
    "okx.funding_rate",
    "okx.oi_usd",
    "stables.total_usd",
    "mempool.hashrate_ehs",
    "cm.mvrv",
    "cm.adr_act",
    "cm.tx_cnt",
    "fred.DGS10",
    "fred.DGS2",
    "fred.DTWEXBGS",
    "fred.FEDFUNDS",
    "fred.WM2NS",
    "fred.VIXCLS",
    "fred.NASDAQCOM",
}


@router.get("/summary", summary="Snapshot completo do dashboard")
async def summary(request: Request) -> dict:
    s = request.app.state
    out = await get_summary(s.db, s.collectors)
    out["demo"] = {"enabled": True, "banner": BANNER} if s.demo else {"enabled": False}
    return out


@router.get("/candles", summary="Candles OHLCV do BTCUSDT (Binance)")
async def candles(
    request: Request,
    interval: Literal["1h", "1d"] = "1h",
    limit: int = Query(500, ge=1, le=1000),
) -> dict:
    rows = await request.app.state.db.get_candles("BTCUSDT", interval, limit)
    return {"symbol": "BTCUSDT", "interval": interval, "source": "Binance Spot", "candles": rows}


@router.get("/series/{metric}", summary="Série histórica de uma métrica")
async def series(request: Request, metric: str, days: int = Query(30, ge=1, le=730)) -> dict:
    if metric not in ALLOWED_SERIES:
        raise HTTPException(404, f"Métrica desconhecida: {metric}")
    since = int(time.time()) - days * 86400
    points = await request.app.state.db.get_series(metric, since_ts=since)
    return {"metric": metric, "points": [{"ts": ts, "value": v} for ts, v in points]}


@router.get("/etf-flows", summary="Histórico local do fluxo diário dos ETFs de BTC à vista (US$ mi)")
async def etf_flows(request: Request, days: int = Query(90, ge=1, le=3650), ticker: str = Query("TOTAL", max_length=10)) -> dict:
    since = (date.today() - timedelta(days=days)).isoformat()
    rows = await request.app.state.db.get_etf_flows(since, ticker.upper())
    return {"ticker": ticker.upper(), "unit": "US$ mi", "points": rows}


@router.get("/alerts", summary="Regras de alerta, estado e canais configurados")
async def alerts(request: Request) -> dict:
    svc = getattr(request.app.state, "alerts", None)
    if svc is None:
        return {"enabled": False, "rules": [], "channels": {}}
    states = await request.app.state.db.get_alert_states()
    settings = get_settings()
    rules = []
    for r in svc.rules:
        keys = [k for k in states if k == r.id or k.startswith(f"{r.id}:")]
        rules.append({
            "id": r.id, "name": r.name, "type": r.type, "condition": r.condition_text(),
            "cooldown_seconds": r.cooldown, "channels": list(r.channels),
            "states": {k: states[k] for k in keys},
        })
    return {
        "enabled": True,
        "demo": svc.demo,
        "channels": svc.channel_status(),  # só se está configurado; nunca o valor
        "daily_summary_time": settings.daily_summary_time or None,
        "timezone": settings.timezone,
        "rules": rules,
    }


@router.get("/alerts/events", summary="Alertas disparados e resumos diários recentes")
async def alert_events(request: Request, hours: int = Query(24, ge=1, le=24 * 30)) -> dict:
    rows = await request.app.state.db.get_alert_events(time.time() - hours * 3600)
    return {
        "events": [
            {
                "id": r["id"], "kind": r["kind"], "rule_key": r["rule_key"], "status": r["status"],
                "fired_at": r["fired_at"], "deliveries": r["deliveries"], "attempts": r["attempts"],
                "name": (r["payload"].get("rule") or {}).get("name") or "Resumo diário",
                "headline": r["payload"].get("headline"),
            }
            for r in rows
        ]
    }


@router.get("/health", summary="Status de cada coletor")
async def health(request: Request) -> dict:
    s = request.app.state
    sources = build_sources(await s.db.get_latest_all(), s.collectors, time.time())
    overall = "ok" if all(v["status"] == "ok" for v in sources.values()) else "degradado"
    return {"status": overall, "demo": s.demo, "sources": sources}
