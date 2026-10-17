import pytest

from app.services.indicators import coinbase_premium, pct_change, weighted_funding


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
