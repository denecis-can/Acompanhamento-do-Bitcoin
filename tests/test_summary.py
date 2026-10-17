"""Regra de status e montagem do /api/summary (sem rede, sem banco)."""

import pytest

from app.collectors.binance_futures import BinanceFuturesCollector
from app.collectors.binance_spot import BinanceSpotCollector
from app.collectors.bybit import BybitCollector
from app.collectors.coinbase import CoinbaseCollector
from app.collectors.deribit import DeribitCollector
from app.collectors.fear_greed import FearGreedCollector
from app.collectors.fred import FredCollector
from app.collectors.okx import OkxCollector
from app.services.status import compute_status
from app.services.summary import DISCLAIMER, build_summary

NOW = 1_000_000.0


# --- status -------------------------------------------------------------------

@pytest.mark.parametrize(
    "entry, expected",
    [
        (None, "pendente"),
        ({"ok": False, "fetched_at": None}, "erro"),
        ({"ok": True, "fetched_at": NOW - 100}, "ok"),
        ({"ok": True, "fetched_at": NOW - 500}, "atrasado"),  # vencido, ainda sem falha registrada
        ({"ok": False, "fetched_at": NOW - 100}, "atrasado"),  # falhou, mas cache ainda válido
        ({"ok": False, "fetched_at": NOW - 500}, "erro"),  # falhou e cache vencido
    ],
)
def test_compute_status(entry, expected):
    assert compute_status(entry, stale_after=300, now=NOW) == expected


# --- summary --------------------------------------------------------------------

@pytest.fixture
def collectors():
    http = None  # build_summary não faz I/O
    return [
        BinanceSpotCollector(http, 60),
        CoinbaseCollector(http, 60),
        BinanceFuturesCollector(http, 300),
        BybitCollector(http, 300),
        OkxCollector(http, 300),
        DeribitCollector(http, 900),
        FearGreedCollector(http, 3600),
        FredCollector(http, 43200, api_key=""),
    ]


def ok(data, age=10):
    return {"data": data, "fetched_at": NOW - age, "last_attempt": NOW - age, "ok": True, "error": None}


def failed(error="boom"):
    return {"data": None, "fetched_at": None, "last_attempt": NOW, "ok": False, "error": error}


@pytest.fixture
def latest():
    return {
        "binance_spot": ok({"price": 80_000.0, "change_24h_pct": -1.0, "high_24h": 1, "low_24h": 1, "volume_usd_24h": 1}),
        "coinbase": ok({"price": 80_080.0}),
        "binance_futures": ok(
            {
                "funding_rate": 0.0001, "oi_usd": 3_000.0, "oi_btc": 1.0, "oi_usd_24h_ago": 2_500.0,
                "next_funding_time": 0, "long_short_ratio": 1.5, "long_account_pct": 60.0, "short_account_pct": 40.0,
            }
        ),
        "bybit": ok({"funding_rate": 0.0004, "oi_usd": 1_000.0, "oi_btc": 1.0, "next_funding_time": 0}),
        "okx": failed("HttpError: www.okx.com: HTTP 503"),
        "deribit": ok({"dvol": 40.0, "dvol_24h_ago": 38.0, "dvol_change_24h": 2.0, "index_price": 80_000.0}),
        "fear_greed": ok({"value": 55, "classification": "Greed"}),
        "fred": failed("CollectorError: FRED_API_KEY não configurada no .env"),
    }


def test_summary_happy_path(latest, collectors):
    s = build_summary(latest, collectors, now=NOW)

    assert s["disclaimer"] == DISCLAIMER
    assert s["price"]["value"] == 80_000.0 and s["price"]["fallback"] is False
    assert s["coinbase_premium"]["usd"] == 80.0
    assert s["coinbase_premium"]["pct"] == pytest.approx(0.1)

    wf = s["derivatives"]["weighted_funding"]
    assert wf["sources"] == ["binance_futures", "bybit"]  # OKX falhou → fora da média
    assert wf["rate"] == pytest.approx(0.000175)
    assert s["derivatives"]["open_interest"]["change_24h_pct"] == pytest.approx(20.0)
    assert s["derivatives"]["long_short"]["ratio"] == 1.5


def test_summary_reports_source_status_and_errors(latest, collectors):
    s = build_summary(latest, collectors, now=NOW)
    assert s["sources"]["okx"]["status"] == "erro"
    assert s["sources"]["binance_spot"]["status"] == "ok"
    assert s["macro"]["status"] == "erro"
    assert "FRED_API_KEY" in s["macro"]["error"]
    assert s["macro"]["series"] == {}


def test_summary_price_falls_back_when_binance_down(latest, collectors):
    latest["binance_spot"] = failed()
    latest["okx"] = ok({"price": 79_990.0, "funding_rate": 0.0001, "oi_usd": 500.0})

    s = build_summary(latest, collectors, now=NOW)

    assert s["price"]["value"] == 79_990.0
    assert s["price"]["source"] == "OKX"
    assert s["price"]["fallback"] is True
    assert s["coinbase_premium"] is None  # sem preço Binance não há premium


def test_summary_uses_stale_cache_after_failure(latest, collectors):
    entry = latest["binance_spot"]
    entry.update(ok=False, error="timeout", last_attempt=NOW)  # dado de 10s atrás ainda válido

    s = build_summary(latest, collectors, now=NOW)

    assert s["price"]["status"] == "atrasado"
    assert s["price"]["value"] == 80_000.0


def test_summary_trend_uses_only_closed_candles(latest, collectors):
    day = 86_400_000
    now_ms = int(NOW * 1000)
    # 200 candles fechados a 100 e um candle aberto (hoje) a 1.000.000, que deve ser ignorado
    daily = [{"open_time": now_ms - (201 - i) * day, "close": 100.0} for i in range(200)]
    daily.append({"open_time": now_ms - day // 2, "close": 1_000_000.0})

    s = build_summary(latest, collectors, now=NOW, candles={"1d": daily, "1w": []})

    t = s["trend"]
    assert t["sma_200d"] == 100.0
    assert t["mayer_multiple"] == pytest.approx(800.0)  # preço 80.000 / 100
    assert t["realized_vol_30d"] == pytest.approx(0.0)
    assert t["iv_rv_spread"] == pytest.approx(40.0)
    assert t["sma_200w"] is None


def test_summary_without_candles_has_no_trend(latest, collectors):
    assert build_summary(latest, collectors, now=NOW)["trend"] is None


def test_summary_funding_context(latest, collectors):
    latest["binance_futures"]["data"].update(funding_30d_mean=0.00005, funding_zscore=1.5)
    ctx = build_summary(latest, collectors, now=NOW)["derivatives"]["funding_context"]
    assert ctx == {"exchange": "Binance", "current_pct": 0.01, "mean_30d_pct": 0.005, "zscore": 1.5}


def test_summary_includes_regime(latest, collectors):
    r = build_summary(latest, collectors, now=NOW)["regime"]
    # no fixture há funding e Fear & Greed; abaixo do mínimo, então sem score, mas com a lista do que falta
    assert r["available"] == 2 and r["score"] is None
    assert "MVRV" in r["missing"]
