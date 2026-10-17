import math

import pytest

from app.services.indicators import (
    annualized_basis,
    coinbase_premium,
    pct_change,
    realized_vol,
    sma,
    trend_metrics,
    weighted_funding,
    zscore,
)


def test_coinbase_premium():
    p = coinbase_premium(81_000.0, 80_000.0)
    assert p["usd"] == 1_000.0
    assert p["pct"] == pytest.approx(1.25)


def test_coinbase_premium_negative():
    assert coinbase_premium(79_920.0, 80_000.0)["pct"] == pytest.approx(-0.1)


def test_weighted_funding_weights_by_oi():
    wf = weighted_funding([("a", 0.0001, 3_000.0), ("b", 0.0004, 1_000.0)])
    # (0.0001*3000 + 0.0004*1000) / 4000 = 0.000175
    assert wf["rate"] == pytest.approx(0.000175)
    assert wf["rate_pct"] == pytest.approx(0.0175)
    assert wf["annualized_pct"] == pytest.approx(0.000175 * 3 * 365 * 100)
    assert wf["total_oi_usd"] == 4_000.0
    assert wf["sources"] == ["a", "b"]


def test_weighted_funding_ignores_zero_oi_and_handles_empty():
    assert weighted_funding([("a", 0.01, 0.0)]) is None
    assert weighted_funding([]) is None
    assert weighted_funding([("a", 0.01, 0), ("b", 0.0002, 10.0)])["rate"] == pytest.approx(0.0002)


def test_pct_change():
    assert pct_change(110.0, 100.0) == pytest.approx(10.0)
    assert pct_change(1.0, None) is None
    assert pct_change(1.0, 0) is None


def test_sma_needs_full_window():
    assert sma([1.0, 2.0, 3.0, 4.0], 2) == 3.5
    assert sma([1.0], 2) is None


def test_zscore():
    assert zscore(3.0, [1.0, 2.0, 3.0]) == pytest.approx(1.0)
    assert zscore(1.0, [1.0, 1.0]) is None  # desvio zero
    assert zscore(1.0, [1.0]) is None


def test_realized_vol_constant_growth_is_zero():
    closes = [100 * 1.01**i for i in range(40)]
    assert realized_vol(closes, 30) == pytest.approx(0.0, abs=1e-9)


def test_realized_vol_alternating_returns():
    # log-retornos alternando +r e -r: desvio amostral conhecido
    r = 0.02
    closes = [100.0]
    for i in range(30):
        closes.append(closes[-1] * math.exp(r if i % 2 == 0 else -r))
    expected = math.sqrt(sum((x - 0) ** 2 for x in [r, -r] * 15) / 29 * 365) * 100
    assert realized_vol(closes, 30) == pytest.approx(expected)
    assert realized_vol(closes[:10], 30) is None


def test_annualized_basis():
    b = annualized_basis(101.0, 100.0, 73)
    assert b["basis_pct"] == pytest.approx(1.0)
    assert b["annualized_pct"] == pytest.approx(5.0)
    assert annualized_basis(1.0, 0.0, 10) is None


def test_trend_metrics():
    daily = [100.0] * 199 + [120.0]
    weekly = [50.0] * 200
    t = trend_metrics(daily, weekly, price=110.0, dvol=40.0)
    assert t["sma_200d"] == pytest.approx(100.1)
    assert t["mayer_multiple"] == pytest.approx(110.0 / 100.1)
    assert t["sma_200w"] == 50.0
    assert t["dist_200w_pct"] == pytest.approx(120.0)
    assert t["iv_rv_spread"] == pytest.approx(40.0 - t["realized_vol_30d"])


def test_trend_metrics_with_short_history():
    t = trend_metrics([100.0] * 20, [], price=100.0, dvol=None)
    assert t["sma_200d"] is None and t["mayer_multiple"] is None
    assert t["sma_200w"] is None and t["dist_200w_pct"] is None
    assert t["iv_rv_spread"] is None
