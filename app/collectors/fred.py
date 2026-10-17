"""FRED (Federal Reserve Bank of St. Louis): séries macro e calendário de divulgações.

As datas das reuniões do FOMC não estão no FRED (o release "FOMC Press Release" é
marcado todo dia); vêm do calendário oficial em federalreserve.gov. Se essa página
falhar, o card segue com as demais divulgações.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone

from app.collectors.base import BaseCollector, CollectorError, CollectorResult
from app.core.http import HttpError

log = logging.getLogger("collector.fred")

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

# Divulgações que costumam mexer com ativos de risco, pelo release_id do FRED. Filtrar
# por nome pegava variantes regionais ("Gross Domestic Product by State") e a H.15, que
# é diária e lotava a lista.
KEY_RELEASES = {
    10: "CPI (inflação ao consumidor)",
    46: "PPI (inflação ao produtor)",
    50: "Payroll (relatório de emprego)",
    53: "PIB",
    54: "PCE (renda e gastos pessoais)",
    192: "JOLTS (vagas de emprego)",
    9: "Vendas no varejo",
}

FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FOMC_LABEL = "Decisão do FOMC"
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}

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


def parse_releases(raw: dict) -> list[dict]:
    seen: set[tuple[int, str]] = set()
    out = []
    for r in raw.get("release_dates", []):
        rid = int(r.get("release_id", 0))
        if rid not in KEY_RELEASES or (rid, r["date"]) in seen:
            continue
        seen.add((rid, r["date"]))
        out.append({"name": KEY_RELEASES[rid], "date": r["date"]})
    return sorted(out, key=lambda x: x["date"])


def parse_fomc(html: str) -> list[dict]:
    """Reuniões do FOMC (dia da decisão) a partir de fomccalendars.htm.

    Cada ano é um painel "YYYY FOMC Meetings" com pares mês/dias ("April/May" e "30-1"
    em reuniões que viram o mês). "*" marca reunião com projeções econômicas (SEP).
    """
    years = list(re.finditer(r"(\d{4}) FOMC Meetings", html))
    if not years:
        raise CollectorError("calendário do FOMC não encontrado (layout mudou?)")
    out = []
    for i, m in enumerate(years):
        segment = html[m.end(): years[i + 1].start() if i + 1 < len(years) else len(html)]
        months = re.findall(r"fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>", segment)
        days = re.findall(r"fomc-meeting__date[^>]*>([^<]+)<", segment)
        for month, day in zip(months, days):
            dm = re.fullmatch(r"\s*(\d{1,2})(?:-(\d{1,2}))?(\*?)\s*", day)
            mon = MONTHS.get(month.strip().split("/")[-1][:3].lower())
            if not dm or not mon:
                continue  # notação de voto, reunião extraordinária etc.
            last_day = int(dm.group(2) or dm.group(1))
            out.append({"name": FOMC_LABEL, "date": date(int(m.group(1)), mon, last_day).isoformat(), "sep": bool(dm.group(3))})
    return sorted(out, key=lambda x: x["date"])


def upcoming(events: list[dict], today: date, limit: int = 10) -> list[dict]:
    return [e for e in sorted(events, key=lambda x: x["date"]) if e["date"] >= today.isoformat()][:limit]


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

        events = parse_releases(releases_raw)
        data = {"series": series_out}
        try:
            fomc = parse_fomc(await self.http.get_text(FOMC_URL))
            events += [e for e in fomc if e["date"] <= (today + timedelta(days=45)).isoformat()]
        except (HttpError, CollectorError) as exc:
            data["fomc_error"] = str(exc)
            log.warning("calendário do FOMC indisponível", extra={"error": str(exc)})
        data["upcoming_releases"] = upcoming(events, today)
        if "DGS10" in series_out and "DGS2" in series_out:
            data["spread_2s10s"] = series_out["DGS10"]["value"] - series_out["DGS2"]["value"]
        return CollectorResult(data=data, series=points)
