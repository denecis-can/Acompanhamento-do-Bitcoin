"""SoSoValue OpenAPI: fluxos diários dos ETFs de BTC à vista nos EUA.

Documentação: https://sosovalue-1.gitbook.io/sosovalue-api-doc
- autenticação pelo header x-soso-api-key; resposta sempre em {code, message, data};
- limite por chave: 20 requisições/minuto e 100.000/mês;
- histórico limitado ao último mês; datas em ordem decrescente;
- liquidação T+1: a linha do dia corrente já existe, mas com os fluxos em null.

Cada coleta faz 2 + N chamadas (resumo, lista de ETFs e histórico de cada um, N ≈ 12).
Com 2 coletas por dia, são ~850 chamadas/mês.
"""

from __future__ import annotations

import logging
from typing import Any

from app.collectors.base import CollectorError
from app.collectors.farside import summarize
from app.core.http import HttpClient, HttpError

log = logging.getLogger("collector.sosovalue")

BASE = "https://openapi.sosovalue.com/openapi/v1"
PARAMS = {"symbol": "BTC", "country_code": "US"}
MI = 1e6
SUM_TOLERANCE = 0.02  # soma por ETF vs. total: só alerta no log (arredondamentos da fonte)


def unwrap(raw: Any, what: str) -> Any:
    if not isinstance(raw, dict) or "code" not in raw:
        raise CollectorError(f"SoSoValue {what}: resposta fora do envelope esperado")
    if raw["code"] != 0:
        raise CollectorError(f"SoSoValue {what}: code={raw['code']} {raw.get('message', '')}")
    return raw.get("data")


def _mi(value: Any) -> float | None:
    return float(value) / MI if value is not None else None


def parse(summary: list[dict], tickers: list[dict], histories: dict[str, list[dict]]) -> dict:
    """Normaliza para o mesmo formato do leitor da Farside (US$ mi)."""
    # Último dia com fluxo em algum ETF. Um dia do resumo depois dele ainda não foi
    # liquidado, mesmo que o total venha preenchido (defesa contra um total provisório).
    ticker_dates = [r["date"] for rows in histories.values() for r in rows if r.get("net_inflow") is not None]
    cutoff = max(ticker_dates) if ticker_dates else None

    def is_settled(r: dict) -> bool:
        return r.get("total_net_inflow") is not None and (cutoff is None or r["date"] <= cutoff)

    settled = [r for r in summary if is_settled(r)]
    pending = sorted(r["date"] for r in summary if not is_settled(r))
    if len(settled) < 5:
        raise CollectorError(f"SoSoValue: só {len(settled)} dias liquidados no resumo")
    settled.sort(key=lambda r: r["date"])

    by_date: dict[str, dict[str, float]] = {}
    cumulative: dict[str, float | None] = {}
    for ticker, rows in histories.items():
        done = sorted((r for r in rows if r.get("net_inflow") is not None), key=lambda r: r["date"])
        for r in done:
            by_date.setdefault(r["date"], {})[ticker] = _mi(r["net_inflow"])
        cumulative[ticker] = _mi(done[-1].get("cum_inflow")) if done else None

    order = [t["ticker"] for t in tickers if t.get("ticker") in histories]
    days = []
    for r in settled:
        total = _mi(r["total_net_inflow"])
        per = by_date.get(r["date"], {})
        if per and abs(sum(per.values()) - total) > max(1.0, abs(total) * SUM_TOLERANCE):
            log.warning("soma por ETF difere do total", extra={"date": r["date"], "total": total, "sum": sum(per.values())})
        days.append({"date": r["date"], "total": total, "by_ticker": {t: per.get(t) for t in order}})

    latest = settled[-1]
    out = summarize(
        days,
        cumulative={"total": _mi(latest.get("cum_net_inflow")), "by_ticker": cumulative},
        names={t["ticker"]: t.get("name", "") for t in tickers},
    )
    out["total_net_assets"] = _mi(latest.get("total_net_assets"))
    out["pending_dates"] = [d for d in pending if d > out["last_day"]["date"]]
    return out


async def fetch(http: HttpClient, api_key: str) -> dict:
    headers = {"x-soso-api-key": api_key}
    summary = unwrap(
        await http.get_json(f"{BASE}/etfs/summary-history", params={**PARAMS, "limit": 30}, headers=headers),
        "summary-history",
    ) or []
    tickers = unwrap(await http.get_json(f"{BASE}/etfs", params=PARAMS, headers=headers), "etfs") or []
    histories: dict[str, list[dict]] = {}
    for t in tickers:
        ticker = t.get("ticker")
        if not ticker:
            continue
        try:
            histories[ticker] = unwrap(
                await http.get_json(f"{BASE}/etfs/{ticker}/history", headers=headers), f"history {ticker}"
            ) or []
        except (HttpError, CollectorError) as exc:
            # um ETF sem histórico não invalida o total do dia, que vem do resumo
            log.warning("histórico de ETF indisponível", extra={"ticker": ticker, "error": str(exc)})
    return parse(summary, tickers, histories)
