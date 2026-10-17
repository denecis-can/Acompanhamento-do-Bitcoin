"""mempool.space e Coin Metrics (fixtures reais de 17/10/2026)."""

import pytest
import respx

from app.collectors.base import CollectorError
from app.collectors.coinmetrics import CoinMetricsCollector, parse_rows, summarize
from app.collectors.mempool import MempoolCollector, halving
from tests.conftest import load


def test_halving_math():
    h = halving(971_842, 600_000, now=0)
    assert h["next_height"] == 1_050_000
    assert h["blocks_remaining"] == 78_158
    assert h["estimated_ts"] == 78_158 * 600
    assert (h["subsidy_btc"], h["next_subsidy_btc"]) == (3.125, 1.5625)


@respx.mock
async def test_mempool_collector(http):
    respx.get(host="mempool.space", path="/api/v1/fees/recommended").respond(json=load("mempool_fees.json"))
    respx.get(host="mempool.space", path="/api/mempool").respond(json=load("mempool_mempool.json"))
    respx.get(host="mempool.space", path="/api/v1/difficulty-adjustment").respond(json=load("mempool_diff.json"))
    respx.get(host="mempool.space", path="/api/v1/mining/hashrate/3m").respond(json=load("mempool_hr3m.json"))
    respx.get(host="mempool.space", path="/api/blocks/tip/height").respond(text="971842")

    result = await MempoolCollector(http, 600).collect()

    d = result.data
    assert d["height"] == 971_842
    assert d["fees_sat_vb"]["fast"] == load("mempool_fees.json")["fastestFee"]
    assert d["hashrate_ehs"] == pytest.approx(load("mempool_hr3m.json")["currentHashrate"] / 1e18)
    assert 500 < d["hashrate_ehs"] < 5000  # ordem de grandeza: EH/s
    assert d["difficulty_adjustment"]["remaining_blocks"] == load("mempool_diff.json")["remainingBlocks"]
    assert len(result.series) == len(load("mempool_hr3m.json")["hashrates"])


def test_coinmetrics_derived_metrics():
    series = parse_rows(load("coinmetrics_btc.json"))
    d = summarize(series)
    last = load("coinmetrics_btc.json")["data"][-1]
    mvrv, mcap = float(last["CapMVRVCur"]), float(last["CapMrktCurUSD"])
    assert d["mvrv"] == pytest.approx(mvrv)
    assert d["realized_cap_usd"] == pytest.approx(mcap / mvrv)
    assert d["nupl"] == pytest.approx(1 - 1 / mvrv)
    assert d["mvrv_1y_min"] <= d["mvrv"] <= d["mvrv_1y_max"]
    assert d["date"] == last["time"][:10]


def test_coinmetrics_forbidden_metric_raises():
    raw = {"error": {"type": "forbidden", "message": "Requested metric 'CapRealUSD' ... not available"}}
    with pytest.raises(CollectorError, match="CapRealUSD"):
        parse_rows(raw)


@respx.mock
async def test_coinmetrics_collector(http):
    respx.get(host="community-api.coinmetrics.io").respond(json=load("coinmetrics_btc.json"))
    result = await CoinMetricsCollector(http, 21600).collect()
    assert {m for m, _, _ in result.series} == {"cm.mvrv", "cm.adr_act", "cm.tx_cnt"}
