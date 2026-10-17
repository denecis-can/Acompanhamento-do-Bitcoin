"""DefiLlama stablecoins (fixtures reais de 17/10/2026)."""

import pytest
import respx

from app.collectors.base import CollectorError
from app.collectors.stablecoins import StablecoinsCollector, parse_assets, parse_history
from tests.conftest import load


def test_parse_assets_sums_only_usd_pegged():
    raw = load("llama_stablecoins.json")
    d = parse_assets(raw)

    usd = [a for a in raw["peggedAssets"] if a["pegType"] == "peggedUSD"]
    assert d["count"] == len(usd)  # a stablecoin em euro da fixture fica de fora
    assert d["total"]["supply"] == pytest.approx(sum(a["circulating"]["peggedUSD"] for a in usd))
    usdt = next(a for a in usd if a["symbol"] == "USDT")
    prev_week = usdt["circulatingPrevWeek"]["peggedUSD"]
    assert d["assets"]["USDT"]["change_7d"] == pytest.approx(usdt["circulating"]["peggedUSD"] - prev_week)
    assert d["assets"]["USDT"]["depeg"] is False
    assert set(d["share_pct"]) == {"USDT", "USDC"}


def test_parse_assets_flags_depeg_and_empty():
    raw = {"peggedAssets": [{"symbol": "USDT", "pegType": "peggedUSD", "price": 0.98, "circulating": {"peggedUSD": 10}}]}
    d = parse_assets(raw)
    assert d["assets"]["USDT"]["depeg"] is True
    assert d["assets"]["USDT"]["change_7d"] is None  # sem valor anterior
    with pytest.raises(CollectorError):
        parse_assets({"peggedAssets": []})


def test_parse_history():
    raw = load("llama_stablecoincharts.json")
    points = parse_history(raw)
    assert len(points) == 365
    assert points[-1] == (int(raw[-1]["date"]), raw[-1]["totalCirculatingUSD"]["peggedUSD"])


@respx.mock
async def test_collector(http):
    respx.get(host="stablecoins.llama.fi", path="/stablecoins").respond(json=load("llama_stablecoins.json"))
    respx.get(host="stablecoins.llama.fi", path="/stablecoincharts/all").respond(json=load("llama_stablecoincharts.json"))
    result = await StablecoinsCollector(http, 21600).collect()
    assert result.data["total"]["supply"] > 1e11
    assert {m for m, _, _ in result.series} == {"stables.total_usd"}
