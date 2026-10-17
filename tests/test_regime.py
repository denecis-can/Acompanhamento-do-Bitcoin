"""Temperatura do mercado: normalização, faixas e transparência do cálculo."""

import pytest

from app.services.regime import COMPONENTS, MIN_COMPONENTS, compute_regime, extract_inputs, label_for, normalize


@pytest.mark.parametrize(
    "value, expected",
    [(10, 0.0), (20, 0.5), (30, 1.0), (99, 1.0), (2.5, -0.5), (-5, -1.0), (-50, -1.0)],
)
def test_normalize_increasing_anchors(value, expected):
    assert normalize(value, cold=-5, neutral=10, hot=30) == pytest.approx(expected)


def test_normalize_decreasing_anchors():
    # skew: positivo (put cara) é frio, negativo é quente
    assert normalize(8, cold=8, neutral=0, hot=-5) == -1.0
    assert normalize(4, cold=8, neutral=0, hot=-5) == pytest.approx(-0.5)
    assert normalize(-2.5, cold=8, neutral=0, hot=-5) == pytest.approx(0.5)


@pytest.mark.parametrize(
    "score, label",
    [(-100, "Muito frio"), (-60, "Muito frio"), (-59, "Frio"), (0, "Neutro"), (20, "Neutro"), (21, "Aquecido"), (61, "Muito aquecido")],
)
def test_bands(score, label):
    assert label_for(score) == label


def test_all_neutral_scores_zero_and_exposes_method():
    inputs = {c.key: c.neutral for c in COMPONENTS}
    r = compute_regime(inputs)
    assert r["score"] == pytest.approx(0.0)
    assert r["label"] == "Neutro"
    assert r["available"] == r["total"] == len(COMPONENTS)
    first = r["components"][0]
    assert set(first) >= {"label", "value", "anchors", "contribution", "why", "group"}
    assert "não é recomendação" in r["method"]


def test_score_is_mean_of_contributions():
    inputs = {c.key: None for c in COMPONENTS}
    inputs.update(fear_greed=80, mayer=2.0, mvrv=1.8, funding=10)  # +1, +1, 0, 0
    r = compute_regime(inputs)
    assert r["score"] == pytest.approx(50.0)
    assert r["label"] == "Aquecido"
    assert len(r["missing"]) == len(COMPONENTS) - 4


def test_needs_minimum_components():
    inputs = {c.key: None for c in COMPONENTS}
    inputs.update(fear_greed=80, mayer=2.0)
    assert MIN_COMPONENTS > 2
    r = compute_regime(inputs)
    assert r["score"] is None and r["label"] is None
    assert r["available"] == 2


def test_extract_inputs_from_summary_shape():
    summary = {
        "derivatives": {"weighted_funding": {"annualized_pct": 9.5}},
        "basis": {"headline": {"annualized_pct": 5.7}},
        "options": {"ref_30d": {"skew_25d": 2.5}},
        "sentiment": {"value": 55},
        "trend": {"mayer_multiple": 0.9},
        "onchain": {"valuation": {"mvrv": 1.51}},
        "etf": {"sum_5d": -228.4},
        "stablecoins": {"total": {"change_30d_pct": 0.4}},
    }
    assert extract_inputs(summary) == {
        "funding": 9.5, "basis": 5.7, "skew": 2.5, "fear_greed": 55, "mayer": 0.9,
        "mvrv": 1.51, "etf_5d": -228.4, "stables_30d": 0.4,
    }
    # seções ausentes ou nulas viram None, sem exceção
    assert set(extract_inputs({"trend": None}).values()) == {None}
