"""FRED (Federal Reserve Bank of St. Louis): séries macro e calendário de divulgações."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.collectors.base import BaseCollector, CollectorError, CollectorResult

BASE = "https://api.stlouisfed.org/fred"

SERIES = {
    "DGS10": "Treasury 10 anos (%)",
    "DGS2": "Treasury 2 anos (%)",
    "DTWEXBGS": "Índice do dólar amplo",
    "FEDFUNDS": "Fed Funds (%)",
    "WM2NS": "M2 (US$ bi)",
    "VIXCLS": "VIX",
    "NASDAQCOM": "Nasdaq Composite",
}

# Divulgações que costumam mexer com ativos de risco. Filtramos por nome porque é
# mais estável que IDs numéricos.
KEY_RELEASES = (
    "Consumer Price Index",
    "Employment Situation",
    "Gross Domestic Product",
    "Personal Income and Outlays",
    "Producer Price Index",
    "H.15 Selected Interest Rates",
    "H.6 Money Stock Measures",
)

HISTORY_DAYS = 400


def _to_ts(d: str) -> int:
    return int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def parse_observations(raw: dict) -> list[tuple[str, float]]:
    if "observations" not in raw:
        raise CollectorError(f"FRED: {raw.get('error_message', 'resposta inesperada')}")
    # FRED usa "." para dias sem valor (feriados etc.)
    return [(o["date"], float(o["value"])) for o in raw["observations"] if o["value"] not in (".", "")]


def summarize(series_id: str, obs: list[tuple[str, float]]) -> dict:
    last_date, last = obs[-1]
    prev = obs[-2][1] if len(obs) > 1 else None
    return {
        "label": SERIES[series_id],
        "value": last,
        "date": last_date,
        "previous": prev,
        "change": last - prev if prev is not None else None,
    }


def parse_releases(raw: dict, limit: int = 8) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    out = []
    for r in raw.get("release_dates", []):
        name = r.get("release_name", "")
        if not any(k.lower() in name.lower() for k in KEY_RELEASES):
            continue
        key = (name, r["date"])
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "date": r["date"]})
    return sorted(out, key=lambda x: x["date"])[:limit]


class FredCollector(BaseCollector):
    name = "fred"
    source = "FRED (St. Louis Fed)"

    def __init__(self, http, interval_seconds: int, api_key: str) -> None:
        super().__init__(http, interval_seconds)
        self.api_key = api_key

    async def collect(self) -> CollectorResult:
        if not self.api_key:
            raise CollectorError("FRED_API_KEY não configurada no .env")

        common = {"api_key": self.api_key, "file_type": "json"}
        start = (date.today() - timedelta(days=HISTORY_DAYS)).isoformat()
        series_out: dict[str, dict] = {}
        points: list[tuple[str, int, float]] = []

        for sid in SERIES:
            raw = await self.http.get_json(
                f"{BASE}/series/observations", params={**common, "series_id": sid, "observation_start": start}
            )
            obs = parse_observations(raw)
            if not obs:
                continue
            series_out[sid] = summarize(sid, obs)
            points.extend((f"fred.{sid}", _to_ts(d), v) for d, v in obs)

        today = date.today()
        releases_raw = await self.http.get_json(
            f"{BASE}/releases/dates",
            params={
                **common,
                "realtime_start": today.isoformat(),
                "realtime_end": (today + timedelta(days=45)).isoformat(),
                "include_release_dates_with_no_data": "true",
                "sort_order": "asc",
                "limit": 1000,
            },
        )

        data = {"series": series_out, "upcoming_releases": parse_releases(releases_raw)}
        if "DGS10" in series_out and "DGS2" in series_out:
            data["spread_2s10s"] = series_out["DGS10"]["value"] - series_out["DGS2"]["value"]
        return CollectorResult(data=data, series=points)
