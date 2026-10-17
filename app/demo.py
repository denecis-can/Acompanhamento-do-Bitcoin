"""Modo demonstração (DEMO_MODE=true): nenhuma chamada externa, dados sintéticos realistas.

O `DemoHttpClient` substitui o cliente HTTP e responde a cada endpoint com as respostas
gravadas em tests/fixtures. Os coletores reais rodam sem mudança, então a demo exercita o
mesmo código de produção. Regras:

- **nada vai para a rede**: uma URL sem dado de demonstração gera erro, nunca uma requisição;
- **datas deslocadas para hoje**: timestamps e datas das fixtures avançam em semanas inteiras
  desde a captura (17/10/2026), preservando o dia da semana (vencimentos às sextas,
  pregões em dias úteis), para a demo não "envelhecer";
- **séries curtas viram sintéticas**: candles, DVOL e séries do FRED são gerados com semente
  fixa, ancorados nos valores das fixtures, para os gráficos terem histórico;
- **notícias**: manchetes reais gravadas, com a fonte e o link originais. Não inventamos
  manchetes atribuídas a veículos reais.
"""

from __future__ import annotations

import copy
import json
import math
import random
import re
import time
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.config import BASE_DIR, Settings
from app.core.http import HttpError

FIXTURES = BASE_DIR / "tests" / "fixtures"
CAPTURE_DATE = date(2026, 10, 17)
BANNER = "Dados de demonstração — não refletem o mercado"

# chaves cujo valor é um instante (ms, s ou data ISO) e precisa andar junto com o calendário
TIME_KEYS = {
    "time", "timestamp", "ts", "date", "fundingTime", "nextFundingTime", "prevFundingTime", "openTime",
    "closeTime", "estimatedRetargetDate", "previousTime", "creation_timestamp",
}
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
DERIBIT_CODE = re.compile(r"(?<=-)(\d{1,2})([A-Z]{3})(\d{2})(?=-|$)")
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")

# âncoras das séries do FRED (últimos valores da captura) e passo diário típico
FRED_ANCHORS = {
    "DGS10": (5.10, 0.04, "d"), "DGS2": (4.77, 0.04, "d"), "DTWEXBGS": (121.60, 0.25, "d"),
    "FEDFUNDS": (3.75, 0.0, "m"), "WM2NS": (23305.5, 40.0, "m"), "VIXCLS": (16.46, 0.9, "d"),
    "NASDAQCOM": (26713.03, 220.0, "d"),
}
KLINE_MS = {"1h": 3_600_000, "1d": 86_400_000, "1w": 7 * 86_400_000}
KLINE_VOL = {"1h": 0.0035, "1d": 0.021, "1w": 0.06}  # ~40% ao ano, perto do BTC

EMPTY_RSS = '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>demo</title></channel></rss>'


def demo_settings(s: Settings) -> Settings:
    """Chaves fictícias (as chamadas nunca saem daqui) e banco separado do real."""
    db = s.database_path
    return replace(
        s,
        fred_api_key="demo",
        sosovalue_api_key="demo",
        database_path=db.with_name(f"{db.stem}_demo{db.suffix}"),
    )


def shift_weeks(today: date | None = None) -> int:
    """Dias inteiros (múltiplo de 7) entre a captura das fixtures e hoje."""
    return ((today or date.today()) - CAPTURE_DATE).days // 7 * 7


def _shift_value(value: Any, days: int) -> Any:
    secs = days * 86400
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value >= 1e12:
            return type(value)(value + secs * 1000)
        if value >= 1e9:
            return type(value)(value + secs)
        return value
    if isinstance(value, str):
        if value.isdigit():
            return str(int(_shift_value(int(value), days)))
        if ISO_DATE.match(value):
            d = date.fromisoformat(value[:10]) + timedelta(days=days)
            return d.isoformat() + value[10:]
    return value


def shift_json(obj: Any, days: int) -> Any:
    """Avança instantes e datas (só nas chaves de TIME_KEYS) em `days` dias."""
    if isinstance(obj, dict):
        return {k: (_shift_value(v, days) if k in TIME_KEYS and not isinstance(v, (dict, list)) else shift_json(v, days)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [shift_json(v, days) for v in obj]
    return obj


def shift_deribit_codes(raw: dict, days: int) -> dict:
    """BTC-30OCT26-80000-C → mesmo prazo relativo, com o vencimento deslocado."""

    def repl(m: re.Match) -> str:
        d = date(2000 + int(m.group(3)), MONTHS.index(m.group(2)) + 1, int(m.group(1))) + timedelta(days=days)
        return f"{d.day}{MONTHS[d.month - 1]}{d.strftime('%y')}"

    out = copy.deepcopy(raw)
    for row in out.get("result", []):
        for key in ("instrument_name", "underlying_index"):
            if isinstance(row.get(key), str):
                row[key] = DERIBIT_CODE.sub(repl, row[key])
    return shift_json(out, days)


def _walk(n: int, end: float, step: float, seed: str, floor: float = 0.0) -> list[float]:
    """Passeio aleatório com semente fixa que termina exatamente em `end` (gerado de trás para frente)."""
    rng = random.Random(seed)
    values = [end]
    for _ in range(n - 1):
        values.append(max(floor, values[-1] - rng.gauss(0, step)))
    return values[::-1]


def synthetic_klines(interval: str, limit: int, anchor: float, now_ms: int) -> list[list]:
    span = KLINE_MS[interval]
    last_open = now_ms // span * span
    rng = random.Random(f"klines-{interval}")
    closes = [anchor]
    for _ in range(limit - 1):  # retornos log-normais de trás para frente
        closes.append(closes[-1] / math.exp(rng.gauss(0, KLINE_VOL[interval])))
    closes.reverse()
    rows = []
    for i, close in enumerate(closes):
        open_ = closes[i - 1] if i else close * (1 - rng.gauss(0, KLINE_VOL[interval]) / 2)
        wick = abs(rng.gauss(0, KLINE_VOL[interval] / 2))
        high, low = max(open_, close) * (1 + wick), min(open_, close) * (1 - wick)
        volume = abs(rng.gauss(400, 120)) * (span / 3_600_000) ** 0.6
        t = last_open - (limit - 1 - i) * span
        rows.append([t, f"{open_:.2f}", f"{high:.2f}", f"{low:.2f}", f"{close:.2f}", f"{volume:.5f}",
                     t + span - 1, f"{volume * close:.2f}", 0, "0", "0", "0"])
    return rows


def synthetic_dvol(now_ms: int, end: float = 37.4) -> dict:
    hour = 3_600_000
    last = now_ms // hour * hour
    values = _walk(7 * 24, end, 0.35, "dvol", floor=20)
    data = []
    for i, close in enumerate(values):
        open_ = values[i - 1] if i else close
        data.append([last - (len(values) - 1 - i) * hour, open_, max(open_, close) + 0.1, min(open_, close) - 0.1, close])
    return {"jsonrpc": "2.0", "result": {"data": data, "continuation": None}}


def synthetic_fred(series_id: str, today: date) -> dict:
    end, step, freq = FRED_ANCHORS[series_id]
    if freq == "m":
        dates = [(today.replace(day=1) - timedelta(days=31 * i)).replace(day=1) for i in range(1, 14)][::-1]
    else:
        dates = [today - timedelta(days=i) for i in range(400, 0, -1)]
        dates = [d for d in dates if d.weekday() < 5]
    values = _walk(len(dates), end, step, f"fred-{series_id}") if step else [end] * len(dates)
    return {"observations": [{"date": d.isoformat(), "value": f"{v:.2f}"} for d, v in zip(dates, values)]}


class DemoHttpClient:
    """Mesma interface do HttpClient (get_json, get_text, aclose), servida das fixtures."""

    def __init__(self, fixtures: Path = FIXTURES, today: date | None = None) -> None:
        self.fixtures = fixtures
        self.today = today or date.today()
        self.days = shift_weeks(self.today)

    async def aclose(self) -> None:
        return None

    def _load(self, name: str) -> Any:
        return json.loads((self.fixtures / name).read_text(encoding="utf-8"))

    def _text(self, name: str) -> str:
        return (self.fixtures / name).read_text(encoding="utf-8")

    def _fixture(self, name: str) -> Any:
        return shift_json(self._load(name), self.days)

    async def get_json(self, url: str, params: dict[str, Any] | None = None, **_: Any) -> Any:
        return self._route(url, params or {})

    async def get_text(self, url: str, params: dict[str, Any] | None = None, **_: Any) -> str:
        return self._route(url, params or {})

    def _route(self, url: str, params: dict[str, Any]) -> Any:
        u = urlparse(url)
        host, path = u.hostname or "", u.path
        now_ms = int(time.time() * 1000)
        f = self._fixture

        if host in ("api.binance.com", "data-api.binance.vision"):
            if path == "/api/v3/ticker/24hr":
                return f("binance_ticker24h.json")
            if path == "/api/v3/klines":
                anchor = float(self._load("binance_ticker24h.json")["lastPrice"])
                return synthetic_klines(params.get("interval", "1h"), int(params.get("limit", 500)), anchor, now_ms)
        if host == "api.exchange.coinbase.com":
            return f("coinbase_ticker.json")
        if host == "fapi.binance.com":
            names = {
                "/fapi/v1/premiumIndex": "binance_premium_index.json",
                "/fapi/v1/openInterest": "binance_open_interest.json",
                "/futures/data/openInterestHist": "binance_oi_hist.json",
                "/futures/data/globalLongShortAccountRatio": "binance_long_short.json",
                "/fapi/v1/fundingRate": "binance_funding_history.json",
            }
            if path in names:
                return f(names[path])
        if host == "api.bybit.com":
            return f("bybit_tickers.json")
        if host == "www.okx.com":
            names = {
                "/api/v5/market/ticker": "okx_spot_ticker.json",
                "/api/v5/public/funding-rate": "okx_funding.json",
                "/api/v5/public/open-interest": "okx_open_interest.json",
            }
            if path in names:
                return f(names[path])
        if host == "www.deribit.com":
            if path.endswith("/get_volatility_index_data"):
                return synthetic_dvol(now_ms)
            if path.endswith("/get_index_price"):
                return f("deribit_index.json")
            if path.endswith("/get_book_summary_by_currency"):
                name = "deribit_options.json" if params.get("kind") == "option" else "deribit_futures.json"
                return shift_deribit_codes(self._load(name), self.days)
        if host == "api.alternative.me":
            return f("fng.json")
        if host == "api.stlouisfed.org":
            if path == "/fred/series/observations" and params.get("series_id") in FRED_ANCHORS:
                return synthetic_fred(params["series_id"], self.today)
            if path == "/fred/releases/dates":
                return f("fred_releases_dates.json")
        if host == "www.federalreserve.gov":
            return self._text("fomc_calendar.html")
        if host == "stablecoins.llama.fi":
            return f("llama_stablecoins.json" if path == "/stablecoins" else "llama_stablecoincharts.json")
        if host == "mempool.space":
            names = {
                "/api/v1/fees/recommended": "mempool_fees.json",
                "/api/mempool": "mempool_mempool.json",
                "/api/v1/difficulty-adjustment": "mempool_diff.json",
                "/api/v1/mining/hashrate/3m": "mempool_hr3m.json",
            }
            if path in names:
                return f(names[path])
            if path == "/api/blocks/tip/height":
                return 971_842 + self.days * 144  # ~144 blocos por dia
        if host == "community-api.coinmetrics.io":
            return f("coinmetrics_btc.json")
        if host == "openapi.sosovalue.com":
            if path.endswith("/etfs/summary-history"):
                return f("sosovalue_summary_history.json")
            if path.endswith("/etfs"):
                return f("sosovalue_etfs.json")
            m = re.search(r"/etfs/([A-Z]+)/history$", path)
            if m and (self.fixtures / f"sosovalue_history_{m.group(1)}.json").exists():
                return f(f"sosovalue_history_{m.group(1)}.json")
        if host == "cointelegraph.com":
            return self._text("rss_cointelegraph.xml")
        if host == "www.theblock.co":
            return self._text("rss_theblock.xml")
        if host in ("www.coindesk.com", "decrypt.co"):
            return EMPTY_RSS  # sem gravação desses feeds: lista vazia, sem manchetes inventadas
        raise HttpError(f"modo demonstração: sem dado sintético para {host}{path}")

