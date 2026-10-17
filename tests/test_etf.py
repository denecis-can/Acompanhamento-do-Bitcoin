"""ETFs: SoSoValue como fonte principal, importação manual da Farside como reserva.

As respostas da SoSoValue em tests/fixtures/sosovalue_*.json são sintéticas: seguem o
formato da documentação oficial, mas com valores inventados. Os termos de uso da
SoSoValue não permitem republicar os dados dela, então nada real vai para o repositório.
"""

import os
import shutil
import time
from pathlib import Path

import pytest
import respx

from app.collectors.base import CollectorError
from app.collectors.etf import EtfCollector, find_import
from app.collectors.sosovalue import parse, unwrap
from app.core.db import Database
from app.scheduler import run_collector
from tests.conftest import load

FIX = Path(__file__).parent / "fixtures"
TICKERS = ("IBIT", "FBTC", "GBTC")
KEY = "chave-de-teste-123"


def mock_sosovalue():
    respx.get(host="openapi.sosovalue.com", path="/openapi/v1/etfs/summary-history").respond(json=load("sosovalue_summary_history.json"))
    respx.get(host="openapi.sosovalue.com", path="/openapi/v1/etfs").respond(json=load("sosovalue_etfs.json"))
    for t in TICKERS:
        respx.get(host="openapi.sosovalue.com", path=f"/openapi/v1/etfs/{t}/history").respond(json=load(f"sosovalue_history_{t}.json"))


# --- SoSoValue -------------------------------------------------------------------

def test_unwrap_envelope():
    assert unwrap({"code": 0, "message": "success", "data": [1]}, "x") == [1]
    with pytest.raises(CollectorError, match="400101"):
        unwrap({"code": 400101, "message": "Invalid API Key"}, "x")
    with pytest.raises(CollectorError, match="envelope"):
        unwrap([1, 2], "x")


def test_parse_skips_unsettled_t1_row_and_converts_to_millions():
    histories = {t: unwrap(load(f"sosovalue_history_{t}.json"), t) for t in TICKERS}
    d = parse(unwrap(load("sosovalue_summary_history.json"), "s"), unwrap(load("sosovalue_etfs.json"), "e"), histories)

    assert d["unit"] == "US$ mi"
    assert d["last_day"]["date"] == "2026-10-16"  # 19/10 ainda não liquidado (T+1)
    assert d["pending_dates"] == ["2026-10-19"]
    assert d["last_day"]["total"] == pytest.approx(-82.4)
    assert d["last_day"]["by_ticker"]["IBIT"] == pytest.approx(-71.8)
    assert d["sum_5d"] == pytest.approx(-225.6)
    assert [x["date"] for x in d["days"]] == sorted(x["date"] for x in d["days"])
    ibit = next(t for t in d["by_ticker"] if t["ticker"] == "IBIT")
    assert ibit["issuer"] == "BlackRock" and ibit["cumulative"] > 0
    assert d["cumulative_total"] is not None and d["total_net_assets"] == pytest.approx(100_000)


def test_parse_needs_settled_days():
    summary = [{"date": "2026-10-19", "total_net_inflow": None}]
    with pytest.raises(CollectorError, match="liquidados"):
        parse(summary, [], {})


# --- coletor: escolha da fonte -----------------------------------------------------

@respx.mock
async def test_uses_sosovalue_when_key_is_set(http, tmp_path):
    mock_sosovalue()
    result = await EtfCollector(http, 43200, api_key=KEY, import_dir=tmp_path).collect()

    assert result.data["source"] == "SoSoValue" and result.data["fallback"] is False
    totals = [r for r in result.etf_flows if r[1] == "TOTAL"]
    assert len(totals) == 10 and all(r[3] == "SoSoValue" for r in totals)
    assert {r[1] for r in result.etf_flows} == {"TOTAL", *TICKERS}
    assert not any(r[0] == "2026-10-19" for r in result.etf_flows)  # T+1 pendente fica fora
    assert "by_day" not in result.data  # detalhe por ETF vai para o SQLite, não para o cache
    sent = respx.calls[0].request
    assert sent.headers["x-soso-api-key"] == KEY


@respx.mock
async def test_falls_back_to_manual_import_when_api_fails(http, tmp_path):
    respx.get(host="openapi.sosovalue.com").respond(401, json={"code": 400101, "message": "Invalid API Key"})
    shutil.copy(FIX / "farside_btc.html", tmp_path / "farside_import.html")

    result = await EtfCollector(http, 43200, api_key=KEY, import_dir=tmp_path).collect()

    d = result.data
    assert d["source"] == "Farside (importação manual)" and d["fallback"] is True
    assert d["import_file"] == "farside_import.html" and d["imported_at"]
    assert "401" in d["primary_error"]
    assert KEY not in d["primary_error"]  # a chave nunca vai para o cache nem para o front
    assert d["last_day"]["date"] == "2026-10-16"


async def test_without_key_reads_csv_import(http, tmp_path):
    rows = [
        "Date;IBIT;FBTC;Total",
        "12 Oct 2026;(65.4);0.0;(65.4)",
        "13 Oct 2026;65.9;103.1;169.0",
        "14 Oct 2026;(110.9);(152.7);(263.6)",
        "15 Oct 2026;16.8;0.0;16.8",
        "16 Oct 2026;(71.8);(10.6);(82.4)",
    ]
    (tmp_path / "farside_import.csv").write_text("\n".join(rows), encoding="utf-8")

    result = await EtfCollector(http, 43200, api_key="", import_dir=tmp_path).collect()

    assert result.data["source_kind"] == "import"
    assert "SOSOVALUE_API_KEY" in result.data["primary_error"]
    assert result.data["sum_5d"] == pytest.approx(-225.6)


async def test_without_key_and_without_import_explains_what_to_configure(http, tmp_path):
    with pytest.raises(CollectorError, match="SOSOVALUE_API_KEY.*farside_import"):
        await EtfCollector(http, 43200, api_key="", import_dir=tmp_path).collect()


def test_find_import_ignores_other_files_and_picks_newest(tmp_path):
    assert find_import(tmp_path) is None
    (tmp_path / "farside_import.txt").write_text("x")
    (tmp_path / "btc_dashboard.db").write_text("x")
    assert find_import(tmp_path) is None
    old, new = tmp_path / "farside_import.html", tmp_path / "farside_import.csv"
    old.write_text("x")
    new.write_text("x")
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    assert find_import(tmp_path) == new


# --- cache no reinício (respeita a cota da API) ------------------------------------------

@pytest.fixture
async def db():
    d = Database(":memory:")
    await d.connect()
    yield d
    await d.close()


@respx.mock
async def test_restart_reuses_fresh_api_cache(http, db, tmp_path):
    await db.save_success("etf", {"source": "SoSoValue", "fallback": False, "days": []})

    collector = EtfCollector(http, 43200, api_key=KEY, import_dir=tmp_path)
    assert await run_collector(collector, db) is True
    assert len(respx.calls) == 0  # nenhuma chamada: o cache tem menos de 12 h

    mock_sosovalue()
    assert await run_collector(collector, db) is True  # execuções seguintes coletam normalmente
    assert (await db.get_latest("etf"))["data"]["source"] == "SoSoValue"


@respx.mock
async def test_restart_does_not_reuse_import_or_stale_cache(http, db, tmp_path):
    mock_sosovalue()
    await db.save_success("etf", {"source": "Farside (importação manual)", "fallback": True, "days": []})
    collector = EtfCollector(http, 43200, api_key=KEY, import_dir=tmp_path)
    await run_collector(collector, db)
    assert (await db.get_latest("etf"))["data"]["source"] == "SoSoValue"

    await db.save_success("etf", {"source": "SoSoValue", "fallback": False, "days": []}, at=time.time() - 50_000)
    fresh = EtfCollector(http, 43200, api_key=KEY, import_dir=tmp_path)
    await run_collector(fresh, db)
    assert (await db.get_latest("etf"))["data"]["days"]  # cache vencido → coletou de novo


# --- dia corrente (T+1) ---------------------------------------------------------------

def _inputs():
    summary = unwrap(load("sosovalue_summary_history.json"), "s")
    tickers = unwrap(load("sosovalue_etfs.json"), "e")
    histories = {t: unwrap(load(f"sosovalue_history_{t}.json"), t) for t in TICKERS}
    return summary, tickers, histories


def test_current_day_with_provisional_total_is_ignored():
    # o resumo traz um total para 19/10, mas nenhum ETF tem fluxo nesse dia: ainda não liquidou
    summary, tickers, histories = _inputs()
    summary[0]["total_net_inflow"] = 123_000_000
    d = parse(summary, tickers, histories)
    assert d["last_day"]["date"] == "2026-10-16"
    assert d["pending_dates"] == ["2026-10-19"]
    assert d["sum_5d"] == pytest.approx(-225.6)


def test_card_and_score_use_last_settled_day():
    from app.services.regime import extract_inputs
    from app.services.summary import build_summary

    summary, tickers, histories = _inputs()
    data = parse(summary, tickers, histories)
    data.pop("by_day")
    now = time.time()
    latest = {"etf": {"data": data, "fetched_at": now, "last_attempt": now, "ok": True, "error": None}}
    s = build_summary(latest, [EtfCollector(None, 43200, api_key="", import_dir=Path("."))], now=now)

    assert s["etf"]["last_day"]["date"] == "2026-10-16"
    assert s["etf"]["pending_dates"] == ["2026-10-19"]
    assert extract_inputs(s)["etf_5d"] == pytest.approx(-225.6)  # 12 a 16/10, sem o dia 19


# --- histórico local no SQLite -----------------------------------------------------------

async def test_etf_history_upsert_does_not_duplicate_and_prefers_sosovalue(db):
    await db.upsert_etf_flows([("2026-10-16", "TOTAL", -82.4, "SoSoValue")])
    await db.upsert_etf_flows([("2026-10-16", "TOTAL", -82.4, "SoSoValue"), ("2026-10-15", "TOTAL", 16.8, "SoSoValue")])
    # importação manual não sobrescreve um dia que já veio da SoSoValue, mas preenche dias novos
    await db.upsert_etf_flows([("2026-10-16", "TOTAL", -80.0, "Farside"), ("2026-10-14", "TOTAL", -263.6, "Farside")])

    rows = await db.get_etf_flows("2026-01-01")
    assert rows == [
        {"date": "2026-10-14", "flow_usd_mi": -263.6, "source": "Farside"},
        {"date": "2026-10-15", "flow_usd_mi": 16.8, "source": "SoSoValue"},
        {"date": "2026-10-16", "flow_usd_mi": -82.4, "source": "SoSoValue"},
    ]
    # e a SoSoValue corrige um dia que veio da importação
    await db.upsert_etf_flows([("2026-10-14", "TOTAL", -263.0, "SoSoValue")])
    assert (await db.get_etf_flows("2026-10-14"))[0] == {"date": "2026-10-14", "flow_usd_mi": -263.0, "source": "SoSoValue"}


@respx.mock
async def test_scheduler_persists_settled_days(http, db, tmp_path):
    mock_sosovalue()
    collector = EtfCollector(http, 43200, api_key=KEY, import_dir=tmp_path)
    await run_collector(collector, db)
    await run_collector(collector, db)  # 2ª coleta com os mesmos dias

    totals = await db.get_etf_flows("2026-01-01")
    assert len(totals) == 10  # sem duplicar
    assert totals[-1]["date"] == "2026-10-16"
    assert len(await db.get_etf_flows("2026-01-01", "IBIT")) == 10
