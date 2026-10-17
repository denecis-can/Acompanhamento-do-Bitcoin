"""Rotas HTTP. O front só conversa com estas rotas, nunca com APIs externas."""

from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

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
    "etf.flow_total",
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
    return await get_summary(s.db, s.collectors)


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


@router.get("/health", summary="Status de cada coletor")
async def health(request: Request) -> dict:
    s = request.app.state
    sources = build_sources(await s.db.get_latest_all(), s.collectors, time.time())
    overall = "ok" if all(v["status"] == "ok" for v in sources.values()) else "degradado"
    return {"status": overall, "sources": sources}
