"""Modo demonstração: tudo sai das fixtures, nada vai para a rede."""

from datetime import date

import pytest
import respx

from app.collectors import build_collectors
from app.config import get_settings
from app.core.db import Database
from app.core.http import HttpError
from app.demo import DemoHttpClient, demo_settings, shift_deribit_codes, shift_json, shift_weeks
from app.scheduler import run_collector
from app.services.summary import get_summary


def test_shift_weeks_keeps_weekday():
    assert shift_weeks(date(2026, 10, 17)) == 0
    assert shift_weeks(date(2026, 10, 30)) == 7
    assert shift_weeks(date(2027, 3, 1)) % 7 == 0


def test_shift_json_moves_only_time_fields():
    raw = {"date": "2026-10-16", "time": "2026-10-17T01:13:44Z", "timestamp": "1792195200",
           "fundingTime": 1792224000000, "price": "81573.79", "lastId": 6770176415, "data": [{"date": 1792195200}]}
    out = shift_json(raw, 7)
    assert out["date"] == "2026-10-23"
    assert out["time"] == "2026-10-24T01:13:44Z"
    assert out["timestamp"] == str(1792195200 + 7 * 86400)
    assert out["fundingTime"] == 1792224000000 + 7 * 86400 * 1000
    assert out["price"] == "81573.79" and out["lastId"] == 6770176415  # não são instantes
    assert out["data"][0]["date"] == 1792195200 + 7 * 86400


def test_shift_deribit_codes():
    raw = {"result": [{"instrument_name": "BTC-30OCT26-80000-C", "underlying_index": "BTC-30OCT26"},
                      {"instrument_name": "BTC-PERPETUAL"}, {"instrument_name": "BTC-25DEC26"}]}
    out = shift_deribit_codes(raw, 63)
    assert out["result"][0] == {"instrument_name": "BTC-1JAN27-80000-C", "underlying_index": "BTC-1JAN27"}
    assert out["result"][1]["instrument_name"] == "BTC-PERPETUAL"
    assert out["result"][2]["instrument_name"] == "BTC-26FEB27"


async def test_unknown_url_fails_closed():
    with pytest.raises(HttpError, match="demonstração"):
        await DemoHttpClient().get_json("https://example.com/qualquer")


@respx.mock  # sem rotas: qualquer requisição real faria o teste falhar
async def test_every_collector_runs_offline_and_summary_is_complete():
    settings = demo_settings(get_settings())
    db = Database(":memory:")
    await db.connect()
    collectors = build_collectors(DemoHttpClient(), settings)

    for c in collectors:
        assert await run_collector(c, db), c.name

    s = await get_summary(db, collectors)
    await db.close()

    assert len(respx.calls) == 0
    assert {k: v["status"] for k, v in s["sources"].items()} == {c.name: "ok" for c in collectors}
    assert s["price"]["value"] > 0
    assert s["options"]["expiries"] and s["options"]["next_major_expiry"]  # vencimentos deslocados para o futuro
    assert s["etf"]["source"] == "SoSoValue" and s["etf"]["last_day"]
    assert s["trend"]["sma_200d"] and s["trend"]["sma_200w"]  # candles sintéticos suficientes
    assert s["macro"]["series"]["DGS10"]["value"] == 5.10
    assert s["regime"]["available"] == s["regime"]["total"] and s["regime"]["score"] is not None


def test_demo_settings_use_separate_database():
    s = demo_settings(get_settings())
    assert s.database_path.name.endswith("_demo.db")
    assert s.fred_api_key == "demo" and s.sosovalue_api_key == "demo"


@respx.mock
async def test_alert_rule_paths_exist_in_summary():
    """Cada `path` do config/alerts.toml aponta para um número real do /api/summary."""
    from app.alerts.engine import get_path
    from app.alerts.rules import load_rules
    from app.config import BASE_DIR

    settings = demo_settings(get_settings())
    db = Database(":memory:")
    await db.connect()
    collectors = build_collectors(DemoHttpClient(), settings)
    for c in collectors:
        await run_collector(c, db)
    s = await get_summary(db, collectors)
    await db.close()

    for name in ("alerts.toml", "alerts.example.toml"):
        for r in load_rules(BASE_DIR / "config" / name):
            if r.type in ("threshold", "band_change"):
                assert isinstance(get_path(s, r.path), (int, float)), (name, r.id, r.path)
            if r.per:
                assert get_path(s, r.per), (name, r.id, r.per)
