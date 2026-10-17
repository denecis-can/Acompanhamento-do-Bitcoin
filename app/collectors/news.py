"""Notícias via RSS (CoinDesk, The Block, Cointelegraph, Decrypt), filtradas por BTC, ETF e macro.

RSS 2.0 é simples o bastante para a biblioteca padrão; dispensa o feedparser.
Um feed fora do ar não derruba os outros: o coletor só falha se todos falharem.
"""

from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit

from app.collectors.base import BaseCollector, CollectorError, CollectorResult
from app.core.http import HttpError

log = logging.getLogger("collector.news")

FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "The Block": "https://www.theblock.co/rss.xml",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
}
MAX_ITEMS = 15

# O título precisa citar BTC, ETFs ou o contexto macro que mexe com BTC. Só o título:
# resumos citam bitcoin de passagem em matérias sobre outros assuntos.
TOPIC = re.compile(
    r"\b(bitcoin|btc|etfs?|ibit|blackrock|fed|fomc|powell|inflation|cpi|pce|treasur(y|ies)|yields?|"
    r"rate (cut|hike)s?|interest rates?|dollar|dxy|tariffs?|recession|payrolls?|jobs report|"
    r"halving|hashrate|miners?|saylor|microstrategy|sec)\b",
    re.IGNORECASE,
)
TAG = re.compile(r"<[^>]+>")


def _clean(text: str | None) -> str:
    return " ".join(html.unescape(TAG.sub(" ", text or "")).split())


def _norm_link(link: str) -> str:
    """Remove query string e fragmento (utm_* etc.) para deduplicar."""
    parts = urlsplit(link.strip())
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def parse_feed(xml_text: str, source: str) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise CollectorError(f"{source}: XML inválido ({exc})") from exc
    items = []
    for item in root.iter("item"):
        title = _clean(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        # só links http(s): o front usa o link como href
        if not title or urlsplit(link).scheme not in ("http", "https"):
            continue
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            continue
        if published.tzinfo is None:
            continue
        items.append(
            {
                "title": title,
                "link": link,
                "source": source,
                "published_ts": int(published.timestamp()),
                "relevant": bool(TOPIC.search(title)),
            }
        )
    return items


def merge(items: list[dict], limit: int = MAX_ITEMS) -> list[dict]:
    """Filtra os relevantes, remove duplicatas (link ou título) e ordena do mais recente."""
    seen_links: set[str] = set()
    seen_titles: set[str] = set()
    out = []
    for it in sorted(items, key=lambda i: i["published_ts"], reverse=True):
        if not it["relevant"]:
            continue
        link, title = _norm_link(it["link"]), it["title"].lower()
        if link in seen_links or title in seen_titles:
            continue
        seen_links.add(link)
        seen_titles.add(title)
        out.append({k: v for k, v in it.items() if k != "relevant"})
        if len(out) == limit:
            break
    return out


class NewsCollector(BaseCollector):
    name = "news"
    source = "RSS (CoinDesk, The Block, Cointelegraph, Decrypt)"

    async def collect(self) -> CollectorResult:
        items: list[dict] = []
        feeds: dict[str, str] = {}
        for source, url in FEEDS.items():
            try:
                items.extend(parse_feed(await self.http.get_text(url), source))
                feeds[source] = "ok"
            except (HttpError, CollectorError) as exc:
                feeds[source] = "erro"
                log.warning("feed falhou", extra={"feed": source, "error": str(exc)})
        if not any(v == "ok" for v in feeds.values()):
            raise CollectorError("nenhum feed RSS respondeu")
        return CollectorResult(data={"items": merge(items), "feeds": feeds})
