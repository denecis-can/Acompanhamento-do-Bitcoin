"""Análise da cadeia de opções e da curva de futuros da Deribit. Funções puras, sem I/O.

Convenções da Deribit:
- instrumento de opção: BTC-30OCT26-80000-C (vencimento, strike, C/P)
- open_interest e volume de opções em BTC (1 contrato = 1 BTC)
- mark_iv em % ao ano; underlying_price é o futuro/índice do vencimento (forward)
- vencimentos às 08:00 UTC
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from app.services.indicators import annualized_basis

MONTHS = {m: i for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}
SECONDS_PER_YEAR = 365 * 86400
STRIKE_RANGE = 0.35  # OI por strike: só strikes até ±35% do spot
MIN_DAYS_FOR_IV = 1.0  # vencimentos com menos de 1 dia distorcem IV e delta


def parse_expiry(code: str) -> datetime:
    """'30OCT26' ou '6NOV26' → datetime UTC às 08:00 (sem depender do locale)."""
    day, mon, year = int(code[:-5]), code[-5:-2], int(code[-2:])
    return datetime(2000 + year, MONTHS[mon], day, 8, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Option:
    expiry: str
    expiry_ts: float
    strike: float
    kind: str  # "C" ou "P"
    oi: float
    volume: float
    mark_iv: float | None
    forward: float | None


def parse_options(raw: list[dict]) -> list[Option]:
    out = []
    for r in raw:
        parts = r.get("instrument_name", "").split("-")
        if len(parts) != 4 or parts[3] not in ("C", "P"):
            continue
        try:
            exp = parse_expiry(parts[1])
        except (KeyError, ValueError):
            continue
        out.append(
            Option(
                expiry=parts[1],
                expiry_ts=exp.timestamp(),
                strike=float(parts[2]),
                kind=parts[3],
                oi=float(r.get("open_interest") or 0),
                volume=float(r.get("volume") or 0),
                mark_iv=float(r["mark_iv"]) if r.get("mark_iv") else None,
                forward=float(r["underlying_price"]) if r.get("underlying_price") else None,
            )
        )
    return out


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_delta(kind: str, forward: float, strike: float, iv_pct: float, years: float) -> float | None:
    """Delta de Black-76 (opção sobre o futuro, juros zero, como a Deribit precifica)."""
    if forward <= 0 or strike <= 0 or iv_pct <= 0 or years <= 0:
        return None
    sigma = iv_pct / 100
    d1 = (math.log(forward / strike) + 0.5 * sigma**2 * years) / (sigma * math.sqrt(years))
    return _norm_cdf(d1) if kind == "C" else _norm_cdf(d1) - 1


def max_pain(chain: list[Option]) -> float | None:
    """Strike em que o valor intrínseco total pago aos compradores no vencimento é mínimo."""
    strikes = sorted({o.strike for o in chain})
    if not strikes:
        return None

    def payout(settle: float) -> float:
        return sum(
            o.oi * (max(0.0, settle - o.strike) if o.kind == "C" else max(0.0, o.strike - settle)) for o in chain
        )

    return min(strikes, key=payout)


def atm_iv(chain: list[Option]) -> float | None:
    """IV média de call e put no strike mais próximo do forward."""
    fwd = next((o.forward for o in chain if o.forward), None)
    priced = [o for o in chain if o.mark_iv]
    if not fwd or not priced:
        return None
    k = min({o.strike for o in priced}, key=lambda s: abs(s - fwd))
    ivs = [o.mark_iv for o in priced if o.strike == k]
    return sum(ivs) / len(ivs)


def skew_25d(chain: list[Option], now: float) -> dict | None:
    """IV da put de 25 delta menos IV da call de 25 delta. Positivo = proteção contra queda mais cara."""
    best: dict[str, tuple[float, Option]] = {}
    for o in chain:
        if not (o.mark_iv and o.forward):
            continue
        years = (o.expiry_ts - now) / SECONDS_PER_YEAR
        delta = bs_delta(o.kind, o.forward, o.strike, o.mark_iv, years)
        if delta is None:
            continue
        target = 0.25 if o.kind == "C" else -0.25
        dist = abs(delta - target)
        if o.kind not in best or dist < best[o.kind][0]:
            best[o.kind] = (dist, o)
    if "C" not in best or "P" not in best:
        return None
    call, put = best["C"][1], best["P"][1]
    return {
        "skew": put.mark_iv - call.mark_iv,
        "put_strike": put.strike,
        "put_iv": put.mark_iv,
        "call_strike": call.strike,
        "call_iv": call.mark_iv,
    }


def _ratio(num: float, den: float) -> float | None:
    return num / den if den > 0 else None


def analyze_options(options: list[Option], spot: float, now: float) -> dict:
    """Resumo da cadeia: totais, put/call, OI por strike e métricas por vencimento."""
    live = [o for o in options if o.expiry_ts > now]
    by_expiry: dict[str, list[Option]] = defaultdict(list)
    for o in live:
        by_expiry[o.expiry].append(o)

    calls_oi = sum(o.oi for o in live if o.kind == "C")
    puts_oi = sum(o.oi for o in live if o.kind == "P")
    calls_vol = sum(o.volume for o in live if o.kind == "C")
    puts_vol = sum(o.volume for o in live if o.kind == "P")
    total_oi = calls_oi + puts_oi

    expiries = []
    for code, chain in sorted(by_expiry.items(), key=lambda kv: kv[1][0].expiry_ts):
        days = (chain[0].expiry_ts - now) / 86400
        c_oi = sum(o.oi for o in chain if o.kind == "C")
        p_oi = sum(o.oi for o in chain if o.kind == "P")
        usable = days >= MIN_DAYS_FOR_IV
        sk = skew_25d(chain, now) if usable else None
        expiries.append(
            {
                "code": code,
                "date": datetime.fromtimestamp(chain[0].expiry_ts, tz=timezone.utc).date().isoformat(),
                "days": days,
                "oi_btc": c_oi + p_oi,
                "oi_usd": (c_oi + p_oi) * spot,
                "oi_share_pct": (c_oi + p_oi) / total_oi * 100 if total_oi else None,
                "put_call_oi": _ratio(p_oi, c_oi),
                "max_pain": max_pain(chain),
                "atm_iv": atm_iv(chain) if usable else None,
                "skew_25d": sk["skew"] if sk else None,
            }
        )

    # OI agregado por strike, perto do dinheiro
    strikes: dict[float, dict[str, float]] = defaultdict(lambda: {"calls": 0.0, "puts": 0.0})
    for o in live:
        if abs(o.strike / spot - 1) <= STRIKE_RANGE and o.oi > 0:
            strikes[o.strike]["calls" if o.kind == "C" else "puts"] += o.oi
    oi_by_strike = [{"strike": k, **v} for k, v in sorted(strikes.items())]

    # vencimento de referência ~30 dias (skew e IV ATM do card)
    with_iv = [e for e in expiries if e["atm_iv"] is not None]
    ref_30d = min(with_iv, key=lambda e: abs(e["days"] - 30)) if with_iv else None
    # próximo vencimento relevante: o mais próximo com pelo menos 10% do OI total
    major = next((e for e in expiries if (e["oi_share_pct"] or 0) >= 10), None)

    return {
        "spot": spot,
        "total_oi_btc": total_oi,
        "total_oi_usd": total_oi * spot,
        "put_call_oi": _ratio(puts_oi, calls_oi),
        "put_call_volume": _ratio(puts_vol, calls_vol),
        "volume_24h_btc": calls_vol + puts_vol,
        "ref_30d": ref_30d,
        "next_major_expiry": major,
        "expiries": expiries,
        "oi_by_strike": oi_by_strike,
    }


def futures_curve(raw: list[dict], now: float, min_days: float = 7) -> dict:
    """Basis anualizado de cada futuro com vencimento da Deribit contra o índice."""
    curve = []
    for r in raw:
        parts = r.get("instrument_name", "").split("-")
        if len(parts) != 2 or parts[1] == "PERPETUAL":
            continue
        try:
            exp = parse_expiry(parts[1])
        except (KeyError, ValueError):
            continue
        days = (exp.timestamp() - now) / 86400
        mark, index = r.get("mark_price"), r.get("estimated_delivery_price")
        if days < min_days or not mark or not index:
            continue
        curve.append(
            {
                "code": parts[1],
                "date": exp.date().isoformat(),
                "days": days,
                "mark_price": mark,
                "index_price": index,
                **annualized_basis(mark, index, days),
                "oi_usd": r.get("open_interest"),  # futuros Deribit: OI em USD
            }
        )
    curve.sort(key=lambda c: c["days"])
    # referência: o vencimento mais próximo de 3 meses
    headline = min(curve, key=lambda c: abs(c["days"] - 90)) if curve else None
    return {"curve": curve, "headline": headline}
