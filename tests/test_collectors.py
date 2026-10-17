"""Coletores com respostas reais gravadas em tests/fixtures (capturadas em 16/10/2026)."""

from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from app.collectors.base import CollectorError
from app.collectors.binance_futures import BinanceFuturesCollector
from app.collectors.binance_spot import BinanceSpotCollector
from app.collectors.bybit import BybitCollector
from app.collectors.coinbase import CoinbaseCollector
from app.collectors.deribit import DeribitCollector, value_hours_ago
from app.collectors.fear_greed import FearGreedCollector
from app.collectors.fred import SERIES, FredCollector, parse_fomc, parse_releases, upcoming
from app.collectors.okx import OkxCollector
from app.core.http import HttpError
from tests.conftest import load

FOMC_HTML = (Path(__file__).parent / "fixtures" / "fomc_calendar.html").read_text(encoding="utf-8")


@respx.mock
async def test_binance_spot(http):
    respx.get(host="api.binance.com", path="/api/v3/ticker/24hr").respond(json=load("binance_ticker24h.json"))
    respx.get(host="api.binance.com", path="/api/v3/klines").respond(json=load("binance_klines_1h.json"))

    result = await BinanceSpotCollector(http, 60).collect()

    raw = load("binance_ticker24h.json")
    assert result.data["price"] == float(raw["lastPrice"])
    assert result.data["host"] == "api.binance.com"
    assert [b.interval for b in result.candles] == ["1h", "1d", "1w"]
    first = result.candles[0].rows[0]
    assert isinstance(first[0], int) and len(first) == 6


@respx.mock
async def test_binance_spot_falls_back_to_mirror_host(http):
    respx.get(host="api.binance.com").respond(451, text="restricted location")
    respx.get(host="data-api.binance.vision", path="/api/v3/ticker/24hr").respond(json=load("binance_ticker24h.json"))
    respx.get(host="data-api.binance.vision", path="/api/v3/klines").respond(json=load("binance_klines_1h.json"))

    result = await BinanceSpotCollector(http, 60).collect()

    assert result.data["host"] == "data-api.binance.vision"


@respx.mock
async def test_coinbase(http):
    respx.get(host="api.exchange.coinbase.com").respond(json=load("coinbase_ticker.json"))
    result = await CoinbaseCollector(http, 60).collect()
    assert result.data["price"] == float(load("coinbase_ticker.json")["price"])


@respx.mock
async def test_binance_futures(http):
    respx.get(host="fapi.binance.com", path="/fapi/v1/premiumIndex").respond(json=load("binance_premium_index.json"))
    respx.get(host="fapi.binance.com", path="/fapi/v1/openInterest").respond(json=load("binance_open_interest.json"))
    respx.get(host="fapi.binance.com", path="/futures/data/openInterestHist").respond(json=load("binance_oi_hist.json"))
    respx.get(host="fapi.binance.com", path="/futures/data/globalLongShortAccountRatio").respond(
        json=load("binance_long_short.json")
    )
    respx.get(host="fapi.binance.com", path="/fapi/v1/fundingRate").respond(json=load("binance_funding_history.json"))

    result = await BinanceFuturesCollector(http, 300).collect()

    d = result.data
    premium = load("binance_premium_index.json")
    oi = load("binance_open_interest.json")
    assert d["funding_rate"] == float(premium["lastFundingRate"])
    assert d["oi_usd"] == pytest.approx(float(oi["openInterest"]) * float(premium["markPrice"]))
    assert d["oi_usd_24h_ago"] == float(load("binance_oi_hist.json")[0]["sumOpenInterestValue"])
    assert d["long_account_pct"] + d["short_account_pct"] == pytest.approx(100)
    hist = [float(h["fundingRate"]) for h in load("binance_funding_history.json")]
    assert d["funding_30d_mean"] == pytest.approx(sum(hist) / len(hist))
    assert d["funding_zscore"] is not None
    assert {m for m, _, _ in result.series} == {
        "binance.funding_rate",
        "binance.oi_usd",
        "binance.long_short_ratio",
    }


@respx.mock
async def test_bybit(http):
    respx.get(host="api.bybit.com").respond(json=load("bybit_tickers.json"))
    result = await BybitCollector(http, 300).collect()
    t = load("bybit_tickers.json")["result"]["list"][0]
    assert result.data["funding_rate"] == float(t["fundingRate"])
    assert result.data["oi_usd"] == float(t["openInterestValue"])
    assert result.data["change_24h_pct"] == pytest.approx(float(t["price24hPcnt"]) * 100)


@respx.mock
async def test_bybit_error_code_raises(http):
    respx.get(host="api.bybit.com").respond(json={"retCode": 10001, "retMsg": "params error", "result": {}})
    with pytest.raises(CollectorError, match="10001"):
        await BybitCollector(http, 300).collect()


@respx.mock
async def test_okx(http):
    respx.get(host="www.okx.com", path="/api/v5/market/ticker").respond(json=load("okx_spot_ticker.json"))
    respx.get(host="www.okx.com", path="/api/v5/public/funding-rate").respond(json=load("okx_funding.json"))
    respx.get(host="www.okx.com", path="/api/v5/public/open-interest").respond(json=load("okx_open_interest.json"))

    result = await OkxCollector(http, 300).collect()

    assert result.data["price"] == float(load("okx_spot_ticker.json")["data"][0]["last"])
    assert result.data["oi_usd"] == float(load("okx_open_interest.json")["data"][0]["oiUsd"])


@respx.mock
async def test_deribit(http):
    respx.get(host="www.deribit.com", path="/api/v2/public/get_volatility_index_data").respond(
        json=load("deribit_dvol.json")
    )
    respx.get(host="www.deribit.com", path="/api/v2/public/get_index_price").respond(json=load("deribit_index.json"))

    result = await DeribitCollector(http, 900).collect()

    candles = load("deribit_dvol.json")["result"]["data"]
    assert result.data["dvol"] == candles[-1][4]
    assert result.data["dvol_change_24h"] == pytest.approx(result.data["dvol"] - result.data["dvol_24h_ago"])
    assert len(result.series) == len(candles)


def test_value_hours_ago():
    points = [(0, 10.0), (3600, 11.0), (7200, 12.0)]
    assert value_hours_ago(points, 1) == 11.0
    assert value_hours_ago(points, 2) == 10.0
    assert value_hours_ago(points, 3) is None


@respx.mock
async def test_fear_greed(http):
    respx.get(host="api.alternative.me").respond(json=load("fng.json"))
    result = await FearGreedCollector(http, 3600).collect()
    raw = load("fng.json")["data"]
    assert result.data["value"] == int(raw[0]["value"])
    assert result.data["previous_value"] == int(raw[1]["value"])
    # série ordenada do mais antigo para o mais recente
    assert result.series[0][1] < result.series[-1][1]


async def test_fred_without_key_fails_clearly(http):
    with pytest.raises(CollectorError, match="FRED_API_KEY"):
        await FredCollector(http, 43200, api_key="").collect()


@respx.mock
async def test_fred(http):
    respx.get(host="api.stlouisfed.org", path="/fred/series/observations").respond(json=load("fred_dgs10.json"))
    respx.get(host="api.stlouisfed.org", path="/fred/releases/dates").respond(json=load("fred_releases_dates.json"))
    respx.get(host="www.federalreserve.gov").respond(text=FOMC_HTML)

    result = await FredCollector(http, 43200, api_key="test").collect()

    dgs10 = result.data["series"]["DGS10"]
    # "." (sem valor) é descartado: anterior a 5.10 é 5.08, não o feriado
    assert dgs10["value"] == 5.10 and dgs10["previous"] == 5.08
    assert dgs10["change"] == pytest.approx(0.02)
    assert set(result.data["series"]) == set(SERIES)
    assert result.data["spread_2s10s"] == 0  # o mock devolve a mesma série para 2a e 10a
    assert all(":" not in m for m, _, _ in result.series)


def test_fred_releases_filter_by_id_and_dedupe():
    out = parse_releases(load("fred_releases_dates.json"))
    # H.15 (diária) e o release 999 ficam de fora; nomes em português
    assert out == [
        {"name": "Payroll (relatório de emprego)", "date": "2026-11-06"},
        {"name": "CPI (inflação ao consumidor)", "date": "2026-11-12"},
    ]


def test_parse_fomc_official_calendar():
    meetings = parse_fomc(FOMC_HTML)
    dates = [m["date"] for m in meetings]
    assert len(meetings) == 16  # 8 reuniões por ano, 2026 e 2027
    assert "2026-10-28" in dates and "2026-12-09" in dates  # dia da decisão (2º dia)
    assert next(m for m in meetings if m["date"] == "2026-12-09")["sep"] is True
    assert dates == sorted(dates)


def test_parse_fomc_layout_change():
    with pytest.raises(CollectorError, match="FOMC"):
        parse_fomc("<html>nada aqui</html>")


def test_upcoming_drops_past_events():
    events = [{"name": "a", "date": "2026-10-01"}, {"name": "b", "date": "2026-10-17"}, {"name": "c", "date": "2026-11-01"}]
    assert [e["name"] for e in upcoming(events, date(2026, 10, 17))] == ["b", "c"]


@respx.mock
async def test_fred_survives_fomc_page_failure(http):
    respx.get(host="api.stlouisfed.org", path="/fred/series/observations").respond(json=load("fred_dgs10.json"))
    respx.get(host="api.stlouisfed.org", path="/fred/releases/dates").respond(json=load("fred_releases_dates.json"))
    respx.get(host="www.federalreserve.gov").respond(503)

    result = await FredCollector(http, 43200, api_key="test").collect()

    assert "503" in result.data["fomc_error"]
    assert result.data["series"]


@respx.mock
async def test_collector_surfaces_http_errors(http):
    respx.get(host="api.exchange.coinbase.com").mock(side_effect=httpx.ConnectTimeout("timeout"))
    with pytest.raises(HttpError, match="ConnectTimeout"):
        await CoinbaseCollector(http, 60).collect()
