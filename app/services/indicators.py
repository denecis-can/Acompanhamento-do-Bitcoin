"""Cálculos derivados. Funções puras, sem I/O, para facilitar os testes."""

from __future__ import annotations

import math
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


def sma(values: list[float], window: int) -> float | None:
    if window <= 0 or len(values) < window:
        return None
    return sum(values[-window:]) / window


def zscore(current: float, history: list[float]) -> float | None:
    """Quantos desvios-padrão `current` está da média de `history`."""
    if len(history) < 2:
        return None
    mean = sum(history) / len(history)
    var = sum((x - mean) ** 2 for x in history) / (len(history) - 1)
    if var <= 0:
        return None
    return (current - mean) / math.sqrt(var)


def realized_vol(closes: list[float], window: int, periods_per_year: int = 365) -> float | None:
    """Volatilidade realizada anualizada (%), pelo desvio-padrão dos log-retornos.

    BTC negocia 7 dias por semana, então a anualização usa 365 períodos diários.
    """
    if len(closes) < window + 1:
        return None
    tail = closes[-(window + 1):]
    rets = [math.log(b / a) for a, b in zip(tail, tail[1:]) if a > 0 and b > 0]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var * periods_per_year) * 100


def annualized_basis(future: float, spot: float, days: float) -> dict | None:
    """Prêmio do futuro sobre o spot, bruto e anualizado (juros simples)."""
    if spot <= 0 or days <= 0:
        return None
    raw = (future / spot - 1) * 100
    return {"basis_pct": raw, "annualized_pct": raw * 365 / days}


def trend_metrics(daily_closes: list[float], weekly_closes: list[float], price: float | None, dvol: float | None) -> dict:
    """Tendência e volatilidade a partir de candles fechados.

    - Mayer Multiple: preço / média de 200 dias. Historicamente < 0,8 em fundos e > 2,4 em euforia.
    - Média de 200 semanas: piso de ciclo frequentemente citado.
    - DVOL − vol. realizada 30d: quanto o mercado de opções cobra acima da volatilidade observada.
    """
    sma_200d = sma(daily_closes, 200)
    sma_200w = sma(weekly_closes, 200)
    rv_30d = realized_vol(daily_closes, 30)
    return {
        "sma_50d": sma(daily_closes, 50),
        "sma_200d": sma_200d,
        "sma_200w": sma_200w,
        "realized_vol_7d": realized_vol(daily_closes, 7),
        "realized_vol_30d": rv_30d,
        "mayer_multiple": price / sma_200d if price and sma_200d else None,
        "dist_200w_pct": pct_change(price, sma_200w) if price else None,
        "iv_rv_spread": dvol - rv_30d if dvol is not None and rv_30d is not None else None,
        "daily_candles": len(daily_closes),
        "weekly_candles": len(weekly_closes),
    }
