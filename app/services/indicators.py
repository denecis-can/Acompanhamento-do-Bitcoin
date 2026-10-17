"""Cálculos derivados. Funções puras, sem I/O, para facilitar os testes."""

from __future__ import annotations

from typing import Iterable

# Binance, Bybit e OKX liquidam funding a cada 8h para BTCUSDT → 3 vezes por dia.
FUNDINGS_PER_DAY = 3


def coinbase_premium(coinbase_usd: float, binance_usdt: float) -> dict[str, float]:
    """Prêmio do preço Coinbase (USD) sobre a Binance (USDT).

    Observação: USDT não é exatamente USD. Um USDT negociado a 0,999 já gera ~US$ 80 de
    "prêmio" com BTC a US$ 80 mil. O indicador é útil como direção (demanda americana vs.
    offshore), não como arbitragem exata.
    """
    diff = coinbase_usd - binance_usdt
    return {"usd": diff, "pct": diff / binance_usdt * 100}


def weighted_funding(sources: Iterable[tuple[str, float, float]]) -> dict | None:
    """Funding médio ponderado pelo open interest em USD.

    `sources`: (nome, funding_rate, oi_usd). Fontes com OI <= 0 são ignoradas.
    """
    rows = [(n, f, oi) for n, f, oi in sources if oi and oi > 0]
    if not rows:
        return None
    total_oi = sum(oi for _, _, oi in rows)
    rate = sum(f * oi for _, f, oi in rows) / total_oi
    return {
        "rate": rate,
        "rate_pct": rate * 100,
        "annualized_pct": rate * FUNDINGS_PER_DAY * 365 * 100,
        "total_oi_usd": total_oi,
        "sources": [n for n, _, _ in rows],
    }


def pct_change(current: float, previous: float | None) -> float | None:
    if previous in (None, 0):
        return None
    return (current / previous - 1) * 100
