"""Cadeia de opções e curva de futuros da Deribit (fixtures reais de 17/10/2026)."""

import json
from datetime import datetime, timezone

import pytest
import respx

from app.collectors.base import CollectorError
from app.collectors.deribit_options import DeribitOptionsCollector, parse
from app.services.options import Option, bs_delta, max_pain, parse_expiry, skew_25d
from tests.conftest import load

NOW = datetime(2026, 10, 17, 0, 0, tzinfo=timezone.utc).timestamp()


def opt(kind, strike, oi=1.0, iv=50.0, fwd=100.0, expiry_ts=None):
    return Option("X", expiry_ts or NOW + 30 * 86400, strike, kind, oi, 0.0, iv, fwd)


def test_parse_expiry_without_locale():
    assert parse_expiry("30OCT26") == datetime(2026, 10, 30, 8, tzinfo=timezone.utc)
    assert parse_expiry("6NOV26") == datetime(2026, 11, 6, 8, tzinfo=timezone.utc)


def test_bs_delta_atm_and_bounds():
    # ATM: delta da call ligeiramente acima de 0,5 por causa do termo σ²T/2
    d = bs_delta("C", 100.0, 100.0, 50.0, 0.25)
    assert 0.5 < d < 0.56
    assert bs_delta("P", 100.0, 100.0, 50.0, 0.25) == pytest.approx(d - 1)
    assert bs_delta("C", 100.0, 100.0, 50.0, 0.0) is None


def test_max_pain_hand_computed():
    chain = [opt("C", 90, oi=10), opt("C", 110, oi=1), opt("P", 100, oi=5), opt("P", 90, oi=1)]
    # pagamento total por preço de liquidação: 90 → 50; 100 → 100; 110 → 200
    assert max_pain(chain) == 90


def test_skew_picks_25_delta_options():
    exp = NOW + 30 * 86400
    chain = [opt("C", k, iv=45.0, expiry_ts=exp) for k in (100, 105, 110, 115, 120)]
    chain += [opt("P", k, iv=55.0, expiry_ts=exp) for k in (80, 85, 90, 95, 100)]
    sk = skew_25d(chain, NOW)
    assert sk["skew"] == pytest.approx(10.0)
    assert sk["call_strike"] > 100 > sk["put_strike"]


def test_parse_real_chain():
    d = parse(load("deribit_options.json"), load("deribit_futures.json"), NOW)

    rows = load("deribit_options.json")["result"]
    calls = sum(r["open_interest"] for r in rows if r["instrument_name"].endswith("-C"))
    puts = sum(r["open_interest"] for r in rows if r["instrument_name"].endswith("-P"))
    assert d["total_oi_btc"] == pytest.approx(calls + puts)
    assert d["put_call_oi"] == pytest.approx(puts / calls)
    assert [e["code"] for e in d["expiries"]] == ["18OCT26", "30OCT26", "27NOV26", "25DEC26"]
    assert sum(e["oi_share_pct"] for e in d["expiries"]) == pytest.approx(100)
    # ~30 dias a partir de 17/10 → 27NOV26 (41 d) é o mais próximo entre os disponíveis
    assert d["ref_30d"]["code"] == "27NOV26"
    assert d["next_major_expiry"]["code"] == "30OCT26"
    for e in d["expiries"][1:]:
        assert 5 < e["atm_iv"] < 150
        assert e["max_pain"] > 0
    strikes = [s["strike"] for s in d["oi_by_strike"]]
    assert strikes == sorted(strikes)
    assert all(abs(k / d["spot"] - 1) <= 0.35 for k in strikes)


def test_futures_curve_skips_perpetual_and_short_dated():
    d = parse(load("deribit_options.json"), load("deribit_futures.json"), NOW)
    codes = [c["code"] for c in d["basis"]["curve"]]
    assert "PERPETUAL" not in codes
    assert "18OCT26" not in codes and "19OCT26" not in codes  # menos de 7 dias
    assert all(c["days"] >= 7 for c in d["basis"]["curve"])
    head = d["basis"]["headline"]
    assert head["code"] == "25DEC26"  # mais próximo de 90 dias
    assert head["annualized_pct"] == pytest.approx(head["basis_pct"] * 365 / head["days"])


def test_parse_rejects_error_payload():
    with pytest.raises(CollectorError, match="opções"):
        parse({"error": {"code": 10000}}, load("deribit_futures.json"), NOW)


@respx.mock
async def test_collector_emits_series(http, monkeypatch):
    monkeypatch.setattr("app.collectors.deribit_options._now", lambda: NOW)
    respx.get(host="www.deribit.com", params={"kind": "option"}).respond(json=load("deribit_options.json"))
    respx.get(host="www.deribit.com", params={"kind": "future"}).respond(json=load("deribit_futures.json"))

    result = await DeribitOptionsCollector(http, 900).collect()

    metrics = {m for m, _, _ in result.series}
    assert {"deribit.put_call_oi", "deribit.basis_3m"} <= metrics
    json.dumps(result.data)  # precisa caber no cache em JSON
