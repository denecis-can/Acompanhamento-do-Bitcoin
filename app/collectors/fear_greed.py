"""Alternative.me: Crypto Fear & Greed Index (diário)."""

from __future__ import annotations

from app.collectors.base import BaseCollector, CollectorError, CollectorResult

URL = "https://api.alternative.me/fng/"


def parse(raw: dict) -> tuple[dict, list[tuple[int, float]]]:
    items = raw.get("data") or []
    if not items:
        raise CollectorError(f"Fear & Greed vazio: {raw.get('metadata')}")
    points = sorted((int(i["timestamp"]), float(i["value"])) for i in items)
    latest = items[0]  # a API devolve do mais recente para o mais antigo
    data = {
        "value": int(latest["value"]),
        "classification": latest["value_classification"],
        "timestamp": int(latest["timestamp"]),
        "previous_value": int(items[1]["value"]) if len(items) > 1 else None,
    }
    return data, points


class FearGreedCollector(BaseCollector):
    name = "fear_greed"
    source = "Alternative.me"

    async def collect(self) -> CollectorResult:
        data, points = parse(await self.http.get_json(URL, params={"limit": 30}))
        return CollectorResult(data=data, series=[("fng.value", ts, v) for ts, v in points])
