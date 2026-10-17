"""Notícias RSS (feeds reais gravados em 17/10/2026)."""

from pathlib import Path

import pytest
import respx

from app.collectors.base import CollectorError
from app.collectors.news import FEEDS, NewsCollector, merge, parse_feed

FIX = Path(__file__).parent / "fixtures"
COINTELEGRAPH = (FIX / "rss_cointelegraph.xml").read_text(encoding="utf-8")
THEBLOCK = (FIX / "rss_theblock.xml").read_text(encoding="utf-8")


def item(title, link, ts, relevant=True, source="X"):
    return {"title": title, "link": link, "source": source, "published_ts": ts, "relevant": relevant}


def test_parse_real_feed():
    items = parse_feed(COINTELEGRAPH, "Cointelegraph")
    assert len(items) == COINTELEGRAPH.count("<item>")
    assert all(i["link"].startswith("https://") for i in items)
    assert all("<" not in i["title"] for i in items)
    assert any(i["relevant"] for i in items)


def test_parse_rejects_non_http_links_and_bad_xml():
    xml = (
        "<rss><channel>"
        "<item><title>Bitcoin up</title><link>javascript:alert(1)</link><pubDate>Fri, 16 Oct 2026 10:00:00 GMT</pubDate></item>"
        "<item><title>Bitcoin &amp; ETFs</title><link>https://a.com/x</link><pubDate>Fri, 16 Oct 2026 10:00:00 GMT</pubDate></item>"
        "</channel></rss>"
    )
    items = parse_feed(xml, "A")
    assert [i["title"] for i in items] == ["Bitcoin & ETFs"]
    with pytest.raises(CollectorError, match="XML"):
        parse_feed("<rss><channel><item>", "A")


def test_merge_filters_dedupes_and_sorts():
    items = [
        item("Bitcoin ETF inflows", "https://a.com/1?utm_source=rss", 100),
        item("Bitcoin ETF inflows", "https://b.com/other", 90),  # mesmo título
        item("Other story", "https://a.com/1", 80),  # mesmo link sem query
        item("NFT drop", "https://a.com/2", 200, relevant=False),
        item("Fed holds rates", "https://a.com/3", 150),
    ]
    out = merge(items)
    assert [i["title"] for i in out] == ["Fed holds rates", "Bitcoin ETF inflows"]
    assert "relevant" not in out[0]


@respx.mock
async def test_collector_tolerates_partial_failure(http):
    respx.get(FEEDS["Cointelegraph"]).respond(text=COINTELEGRAPH)
    respx.get(FEEDS["The Block"]).respond(text=THEBLOCK)
    respx.get(FEEDS["CoinDesk"]).respond(503)
    respx.get(FEEDS["Decrypt"]).respond(text="not xml")

    result = await NewsCollector(http, 900).collect()

    assert result.data["feeds"] == {"CoinDesk": "erro", "The Block": "ok", "Cointelegraph": "ok", "Decrypt": "erro"}
    items = result.data["items"]
    assert 0 < len(items) <= 15
    assert [i["published_ts"] for i in items] == sorted((i["published_ts"] for i in items), reverse=True)


@respx.mock
async def test_collector_fails_when_all_feeds_fail(http):
    for url in FEEDS.values():
        respx.get(url).respond(503)
    with pytest.raises(CollectorError, match="nenhum feed"):
        await NewsCollector(http, 900).collect()
