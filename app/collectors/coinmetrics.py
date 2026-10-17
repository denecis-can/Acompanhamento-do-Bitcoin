"""Coin Metrics Community API: valuation (MVRV) e atividade da rede, diário.

O plano gratuito não expõe o valor realizado diretamente, mas expõe o MVRV e o valor de
mercado; realizado = mercado / MVRV e NUPL = 1 − 1/MVRV.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.collectors.base import BaseCollector, CollectorError, CollectorResult

URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
METRICS = ("CapMVRVCur", "CapMrktCurUSD", "AdrActCnt", "TxCnt", "FeeTotNtv", "SplyCur")
SERIES = {"CapMVRVCur": "cm.mvrv", "AdrActCnt": "cm.adr_act", "TxCnt": "cm.tx_cnt"}
HISTORY_DAYS = 400


def _ts(day: str) -> int:
    return int(datetime.strptime(day[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def parse_rows(raw: dict) -> dict[str, list[tuple[int, float]]]:
    if "data" not in raw:
        raise CollectorError(f"Coin Metrics: {raw.get('error', {}).get('message', 'resposta inesperada')}")
    out: dict[str, list[tuple[int, float]]] = {m: [] for m in METRICS}
    for row in raw["data"]:
        ts = _ts(row["time"])
        for m in METRICS:
            if row.get(m) not in (None, ""):
                out[m].append((ts, float(row[m])))
    for m in METRICS:
        out[m].sort()
    if not out["CapMVRVCur"]:
        raise CollectorError("Coin Metrics: sem dados de MVRV")
    return out


def _avg(points: list[tuple[int, float]], n: int) -> float | None:
    tail = [v for _, v in points[-n:]]
    return sum(tail) / len(tail) if len(tail) == n else None


def summarize(series: dict[str, list[tuple[int, float]]]) -> dict:
    mvrv_ts, mvrv = series["CapMVRVCur"][-1]
    mcap = series["CapMrktCurUSD"][-1][1] if series["CapMrktCurUSD"] else None
    mvrv_hist = [v for _, v in series["CapMVRVCur"][-365:]]
    act7, act30 = _avg(series["AdrActCnt"], 7), _avg(series["AdrActCnt"], 30)
    return {
        "date": datetime.fromtimestamp(mvrv_ts, tz=timezone.utc).date().isoformat(),
        "mvrv": mvrv,
        "mvrv_1y_min": min(mvrv_hist),
        "mvrv_1y_max": max(mvrv_hist),
        "nupl": 1 - 1 / mvrv if mvrv else None,
        "market_cap_usd": mcap,
        "realized_cap_usd": mcap / mvrv if mcap and mvrv else None,
        "active_addresses": series["AdrActCnt"][-1][1] if series["AdrActCnt"] else None,
        "active_addresses_7d": act7,
        "active_addresses_30d": act30,
        "active_addresses_trend_pct": (act7 / act30 - 1) * 100 if act7 and act30 else None,
        "tx_count_7d": _avg(series["TxCnt"], 7),
        "fees_btc_7d": _avg(series["FeeTotNtv"], 7),
        "supply_btc": series["SplyCur"][-1][1] if series["SplyCur"] else None,
    }


class CoinMetricsCollector(BaseCollector):
    name = "coinmetrics"
    source = "Coin Metrics"

    async def collect(self) -> CollectorResult:
        raw = await self.http.get_json(
            URL,
            params={
                "assets": "btc",
                "metrics": ",".join(METRICS),
                "frequency": "1d",
                "start_time": (date.today() - timedelta(days=HISTORY_DAYS)).isoformat(),
                "page_size": 1000,
            },
        )
        series = parse_rows(raw)
        points = [(name, ts, v) for m, name in SERIES.items() for ts, v in series[m]]
        return CollectorResult(data=summarize(series), series=points)
