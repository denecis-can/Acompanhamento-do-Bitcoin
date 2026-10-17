"""Deribit: índice DVOL (volatilidade implícita de 30 dias) e índice de preço BTC/USD."""

from __future__ import annotations

import time

from app.collectors.base import BaseCollector, CollectorError, CollectorResult

BASE = "https://www.deribit.com/api/v2/public"
DVOL_LOOKBACK_HOURS = 7 * 24


def parse_dvol(raw: dict) -> list[tuple[int, float]]:
    """Converte as velas [ts_ms, open, high, low, close] em (ts_s, close)."""
    if "result" not in raw:
        raise CollectorError(f"Deribit DVOL: {raw.get('error')}")
    points = [(int(row[0]) // 1000, float(row[4])) for row in raw["result"]["data"]]
    if not points:
        raise CollectorError("Deribit DVOL: série vazia")
    return sorted(points)


def value_hours_ago(points: list[tuple[int, float]], hours: int) -> float | None:
    """Último valor com timestamp <= (último ponto - horas)."""
    target = points[-1][0] - hours * 3600
    older = [v for ts, v in points if ts <= target]
    return older[-1] if older else None


class DeribitCollector(BaseCollector):
    name = "deribit"
    source = "Deribit"

    async def collect(self) -> CollectorResult:
        now_ms = int(time.time() * 1000)
        dvol_raw = await self.http.get_json(
            f"{BASE}/get_volatility_index_data",
            params={
                "currency": "BTC",
                "start_timestamp": now_ms - DVOL_LOOKBACK_HOURS * 3600 * 1000,
                "end_timestamp": now_ms,
                "resolution": "3600",
            },
        )
        index_raw = await self.http.get_json(f"{BASE}/get_index_price", params={"index_name": "btc_usd"})

        points = parse_dvol(dvol_raw)
        current = points[-1][1]
        prev_24h = value_hours_ago(points, 24)
        data = {
            "dvol": current,
            "dvol_24h_ago": prev_24h,
            "dvol_change_24h": current - prev_24h if prev_24h is not None else None,
            "index_price": float(index_raw["result"]["index_price"]),
        }
        return CollectorResult(data=data, series=[("deribit.dvol", ts, v) for ts, v in points])
