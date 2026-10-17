"""Deribit: cadeia completa de opções BTC e curva de futuros com vencimento.

Uma chamada por tipo (get_book_summary_by_currency) traz todos os instrumentos; delta,
max pain e skew são calculados localmente em app/services/options.py, sem centenas de
chamadas por instrumento.
"""

from __future__ import annotations

import time

from app.collectors.base import BaseCollector, CollectorError, CollectorResult
from app.services.options import analyze_options, futures_curve, parse_options

BASE = "https://www.deribit.com/api/v2/public"

_now = time.time  # substituível nos testes: as fixtures têm vencimentos fixos


def _result(raw: dict, what: str) -> list[dict]:
    if not isinstance(raw, dict) or "result" not in raw:
        raise CollectorError(f"Deribit {what}: {raw.get('error') if isinstance(raw, dict) else 'resposta inesperada'}")
    return raw["result"]


def parse(options_raw: dict, futures_raw: dict, now: float) -> dict:
    rows = _result(options_raw, "opções")
    options = parse_options(rows)
    if not options:
        raise CollectorError("Deribit opções: cadeia vazia")
    spot = next((float(r["estimated_delivery_price"]) for r in rows if r.get("estimated_delivery_price")), None)
    if not spot:
        raise CollectorError("Deribit opções: sem preço do índice")
    return {
        **analyze_options(options, spot, now),
        "basis": futures_curve(_result(futures_raw, "futuros"), now),
    }


class DeribitOptionsCollector(BaseCollector):
    name = "deribit_options"
    source = "Deribit Options"

    async def collect(self) -> CollectorResult:
        g = self.http.get_json
        options_raw = await g(f"{BASE}/get_book_summary_by_currency", params={"currency": "BTC", "kind": "option"})
        futures_raw = await g(f"{BASE}/get_book_summary_by_currency", params={"currency": "BTC", "kind": "future"})
        now = _now()
        data = parse(options_raw, futures_raw, now)

        ts = int(now) // 60 * 60
        series = [("deribit.put_call_oi", ts, data["put_call_oi"])]
        ref = data["ref_30d"]
        if ref:
            series.append(("deribit.atm_iv_30d", ts, ref["atm_iv"]))
            if ref["skew_25d"] is not None:
                series.append(("deribit.skew_25d_30d", ts, ref["skew_25d"]))
        if data["basis"]["headline"]:
            series.append(("deribit.basis_3m", ts, data["basis"]["headline"]["annualized_pct"]))
        return CollectorResult(data=data, series=[s for s in series if s[2] is not None])
