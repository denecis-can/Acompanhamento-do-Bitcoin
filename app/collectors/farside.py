"""Farside Investors: fluxo diário dos ETFs de bitcoin à vista nos EUA (US$ milhões).

A Farside não tem API; lemos a tabela HTML de https://farside.co.uk/btc/. Proteções:
- User-Agent identificado e uma requisição a cada coleta (padrão: 4 h), com throttle de 5 s;
- detecção de página de desafio do Cloudflare e de respostas grandes demais;
- colunas localizadas pelo cabeçalho (ticker), não por posição;
- cada linha é validada: a soma dos ETFs precisa bater com a coluna Total, e o total
  diário precisa estar numa faixa plausível; linhas que falham são descartadas e logadas;
- o dia corrente ainda sem números ("-" ou vazio) fica marcado como pendente;
- se a estrutura mudar, o coletor falha com erro claro e o card segue com o último dado bom.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

from app.collectors.base import BaseCollector, CollectorError, CollectorResult
from app.core.http import HttpError

log = logging.getLogger("collector.farside")

URL = "https://farside.co.uk/btc/"
MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
DATE_RE = re.compile(r"^(\d{1,2}) ([A-Z][a-z]{2}) (\d{4})$")
MAX_ABS_DAILY_TOTAL = 5_000.0  # US$ mi; o recorde diário até 2026 fica abaixo de 1.400
MIN_VALID_DAYS = 5
SUM_TOLERANCE = 1.0  # US$ mi (os valores vêm arredondados em 0,1)
CHALLENGE_MARKERS = ("Just a moment...", "cf-chl", "challenge-platform", "Attention Required!")
# nomes legíveis dos emissores (o cabeçalho da Farside usa logos)
ISSUERS = {
    "IBIT": "BlackRock", "FBTC": "Fidelity", "BITB": "Bitwise", "ARKB": "Ark/21Shares", "BTCO": "Invesco",
    "EZBC": "Franklin", "BRRR": "Valkyrie/CoinShares", "HODL": "VanEck", "BTCW": "WisdomTree",
    "MSBT": "Morgan Stanley", "GBTC": "Grayscale", "BTC": "Grayscale Mini",
}


class _TableParser(HTMLParser):
    """Extrai o texto das células da primeira <table class="etf">."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._depth = 0  # aninhamento de tabelas dentro da tabela alvo
        self._done = False
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if self._done:
            return
        if tag == "table":
            if self._depth or "etf" in (dict(attrs).get("class") or "").split():
                self._depth += 1
        elif self._depth and tag == "tr":
            self._row = []
        elif self._depth and tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if not self._depth:
            return
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        elif tag == "table":
            self._depth -= 1
            self._done = self._depth == 0

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_number(text: str) -> float | None:
    """'54.8' → 54.8; '(10.9)' → -10.9; '1,119.9' → 1119.9; '-' ou '' → None."""
    t = text.strip().replace(",", "").replace("\xa0", "")
    if t in ("", "-", "–", "—"):
        return None
    neg = t.startswith("(") and t.endswith(")")
    try:
        value = float(t.strip("()"))
    except ValueError:
        return None
    return -value if neg else value


def parse_date(text: str) -> str | None:
    m = DATE_RE.match(text.strip())
    if not m or m.group(2) not in MONTHS:
        return None
    return datetime(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1))).date().isoformat()


def parse(html: str) -> dict:
    if any(marker in html for marker in CHALLENGE_MARKERS) and 'class="etf"' not in html:
        raise CollectorError("Farside: página de desafio do Cloudflare (acesso bloqueado)")
    p = _TableParser()
    p.feed(html)
    if not p.rows:
        raise CollectorError("Farside: tabela de fluxos não encontrada (layout mudou?)")

    header = next((r for r in p.rows if "IBIT" in r), None)
    total_idx = next((i for r in p.rows for i, c in enumerate(r) if c == "Total"), None)
    if header is None or total_idx is None:
        raise CollectorError("Farside: cabeçalho sem IBIT/Total (layout mudou?)")
    tickers = {i: t for i, t in enumerate(header) if t and i != total_idx and i > 0}

    days, pending, rejected = [], [], 0
    cumulative: dict | None = None
    for row in p.rows:
        if not row:
            continue
        if row[0] == "Total" and len(row) > total_idx:
            cumulative = {
                "total": parse_number(row[total_idx]),
                "by_ticker": {t: parse_number(row[i]) for i, t in tickers.items() if i < len(row)},
            }
            continue
        day = parse_date(row[0])
        if day is None or len(row) <= total_idx:
            continue
        values = {t: parse_number(row[i]) for i, t in tickers.items() if i < len(row)}
        total = parse_number(row[total_idx])
        if total is None:
            pending.append(day)  # dia ainda sem dados publicados
            continue
        known = [v for v in values.values() if v is not None]
        if abs(total) > MAX_ABS_DAILY_TOTAL or (
            len(known) == len(values) and abs(sum(known) - total) > max(SUM_TOLERANCE, abs(total) * 0.01)
        ):
            rejected += 1
            log.warning("linha descartada", extra={"date": day, "total": total, "sum": sum(known)})
            continue
        days.append({"date": day, "total": total, "by_ticker": values})

    if len(days) < MIN_VALID_DAYS:
        raise CollectorError(f"Farside: só {len(days)} dias válidos ({rejected} descartados)")
    days.sort(key=lambda d: d["date"])

    def window(n: int, ticker: str | None = None) -> float:
        rows = days[-n:]
        if ticker:
            return sum(d["by_ticker"].get(ticker) or 0 for d in rows)
        return sum(d["total"] for d in rows)

    last = days[-1]
    return {
        "unit": "US$ mi",
        "last_day": last,
        "sum_5d": window(5),
        "sum_10d": window(10),
        "by_ticker": [
            {
                "ticker": t,
                "issuer": ISSUERS.get(t, ""),
                "last_day": last["by_ticker"].get(t),
                "sum_5d": window(5, t),
                "cumulative": (cumulative or {}).get("by_ticker", {}).get(t),
            }
            for t in tickers.values()
        ],
        "cumulative_total": (cumulative or {}).get("total"),
        "days": [{"date": d["date"], "total": d["total"]} for d in days],
        "pending_dates": [d for d in pending if d > last["date"]],
        "rejected_rows": rejected,
    }


def _ts(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())


class FarsideCollector(BaseCollector):
    name = "farside"
    source = "Farside Investors"

    async def collect(self) -> CollectorResult:
        try:
            html = await self.http.get_text(URL)
        except HttpError as exc:
            if any(marker in str(exc) for marker in CHALLENGE_MARKERS):
                # Não tentamos contornar: o site bloqueia clientes automatizados de propósito.
                raise CollectorError("Farside bloqueou o acesso automatizado (desafio do Cloudflare)") from exc
            raise
        data = parse(html)
        return CollectorResult(data=data, series=[("etf.flow_total", _ts(d["date"]), d["total"]) for d in data["days"]])
