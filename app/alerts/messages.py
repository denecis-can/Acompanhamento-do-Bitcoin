"""Texto dos alertas e do resumo diário (pt-BR), em HTML do Telegram e no payload JSON."""

from __future__ import annotations

import html
import re
import uuid
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.alerts.engine import Fire, get_path

SCHEMA = "btc-dashboard.alert.v1"
TAG = re.compile(r"<[^>]+>")


def br(value: float, decimals: int = 2) -> str:
    """1234.5 → '1.234,50' (separadores do português)."""
    return f"{value:,.{decimals}f}".replace(",", "§").replace(".", ",").replace("§", ".")


def signed(value: float, decimals: int = 2) -> str:
    return ("+" if value > 0 else "−" if value < 0 else "") + br(abs(value), decimals)


def fmt_value(value: float | None, unit: str) -> str:
    if value is None:
        return "—"
    if unit == "US$ mi":
        return ("+" if value > 0 else "−" if value < 0 else "") + f"US$ {br(abs(value), 1)} mi"
    if unit == "%":
        return signed(value, 3 if abs(value) < 1 else 2) + "%"
    if unit == "pts":
        return signed(value, 1) + " pts"
    return br(value, 0) if float(value).is_integer() else br(value, 2)


def plain(text_html: str) -> str:
    return html.unescape(TAG.sub("", text_html))


def local_time(ts: float, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(ts, tz=tz).strftime("%d/%m %H:%M")


def context(summary: dict[str, Any]) -> dict[str, Any]:
    """Contexto de mercado enviado junto com cada alerta."""
    return {
        "price": get_path(summary, "price.value"),
        "price_change_24h_pct": get_path(summary, "price.change_24h_pct"),
        "regime_score": get_path(summary, "regime.score"),
        "regime_label": get_path(summary, "regime.label"),
    }


def context_line(ctx: dict[str, Any]) -> str:
    parts = []
    if ctx.get("price") is not None:
        change = ctx.get("price_change_24h_pct")
        parts.append(f"BTC US$ {br(ctx['price'], 0)}" + (f" ({signed(change, 2)}% 24h)" if change is not None else ""))
    if ctx.get("regime_score") is not None:
        parts.append(f"Temperatura {signed(ctx['regime_score'], 0)} ({ctx.get('regime_label')})")
    return " · ".join(parts)


def _detail_lines(fire: Fire) -> list[str]:
    r, d = fire.rule, fire.detail
    e = html.escape
    if r.type == "threshold":
        lines = [f"Valor: <b>{fmt_value(fire.value, r.unit)}</b> (limite {e(r.op)} {fmt_value(r.value, r.unit)})"]
        if d.get("per"):
            lines.append(f"Referência: {e(str(d['per']))}")
        return lines
    if r.type == "band_change":
        return [f"Faixa: {e(d['from_band'])} → <b>{e(d['to_band'])}</b> (score {signed(fire.value, 0)})"]
    lines = [f"Coletor: <b>{e(str(d.get('source') or d.get('collector')))}</b> — {fire.value} falhas seguidas"]
    if d.get("error"):
        lines.append(f"Último erro: {e(str(d['error'])[:200])}")
    return lines


def alert_message(fire: Fire, summary: dict[str, Any], now: float, tz: ZoneInfo, dashboard_url: str, demo: bool) -> tuple[str, dict[str, Any]]:
    """(HTML do Telegram, payload JSON) de um disparo."""
    ctx = context(summary)
    lines = [("[DEMO] " if demo else "") + f"<b>{html.escape(fire.rule.name)}</b>", *_detail_lines(fire)]
    if fire.rule.description:
        lines.append(f"<i>{html.escape(fire.rule.description)}</i>")
    if line := context_line(ctx):
        lines.append(line)
    lines.append(f"{local_time(now, tz)} · {html.escape(dashboard_url)}")
    text = "\n".join(lines)
    payload = {
        "schema": SCHEMA,
        "event_id": str(uuid.uuid4()),
        "type": "alert",
        "rule": {"id": fire.rule.id, "name": fire.rule.name, "type": fire.rule.type, "condition": fire.rule.condition_text()},
        "key": fire.key,
        "value": fire.value,
        "unit": fire.rule.unit,
        "detail": fire.detail,
        "suppressed": fire.suppressed,
        "fired_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(timespec="seconds"),
        "fired_at_local": datetime.fromtimestamp(now, tz=tz).isoformat(timespec="seconds"),
        "message": plain(text),
        "context": ctx,
        "dashboard_url": dashboard_url,
        "demo": demo,
    }
    return text, payload
