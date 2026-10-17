"""Regras de alerta: disparo no cruzamento, histerese, cooldown e leitura do TOML."""

from pathlib import Path

import pytest

from app.alerts.engine import accept_band, band_index, evaluate
from app.alerts.rules import Rule, RuleError, load_rules, parse_duration, parse_rules

CONFIG = Path(__file__).parent.parent / "config"
H = 3600


def rule(**kw):
    base = {"id": "r", "name": "R", "type": "threshold", "path": "x", "op": ">", "value": 10.0, "hysteresis": 2.0, "cooldown": 4 * H}
    return Rule(**{**base, **kw})


class Runner:
    """Avalia uma sequência de leituras carregando o estado entre elas."""

    def __init__(self, *rules):
        self.rules = list(rules)
        self.states = {}

    def step(self, summary, now):
        fires, new = evaluate(self.rules, summary, self.states, now)
        self.states.update(new)
        return fires


# --- disparo, histerese e cooldown ------------------------------------------------------

def test_fires_only_on_crossing_not_on_every_reading():
    r = Runner(rule())
    assert r.step({"x": 5}, 0) == []            # primeira leitura: só estado
    fires = r.step({"x": 12}, 60)               # cruzou
    assert len(fires) == 1 and not fires[0].suppressed and fires[0].value == 12
    assert r.step({"x": 15}, 120) == []         # continua acima: não repete
    assert r.step({"x": 11}, 180) == []


def test_first_reading_beyond_limit_is_silent():
    r = Runner(rule())
    assert r.step({"x": 50}, 0) == []
    assert r.step({"x": 50}, 60) == []


def test_hysteresis_blocks_oscillation_around_limit():
    r = Runner(rule(cooldown=0))
    r.step({"x": 5}, 0)
    assert len(r.step({"x": 10.5}, 60)) == 1
    # oscila logo abaixo e acima do limite, sem passar da folga (rearma só abaixo de 8)
    for i, x in enumerate([9.5, 10.5, 8.5, 10.2, 9.0, 11.0]):
        assert r.step({"x": x}, 120 + i * 60) == [], x
    assert r.step({"x": 7.9}, 600) == []        # rearmou
    assert len(r.step({"x": 10.1}, 660)) == 1   # novo cruzamento dispara


def test_cooldown_suppresses_second_crossing():
    r = Runner(rule(hysteresis=0))
    r.step({"x": 5}, 0)
    assert not r.step({"x": 11}, 60)[0].suppressed
    r.step({"x": 5}, 120)
    second = r.step({"x": 11}, 2 * H)           # dentro das 4h
    assert len(second) == 1 and second[0].suppressed
    r.step({"x": 5}, 3 * H)
    third = r.step({"x": 11}, 5 * H)            # 5h depois do 1º envio
    assert len(third) == 1 and not third[0].suppressed


def test_suppressed_fire_does_not_restart_cooldown():
    r = Runner(rule(hysteresis=0))
    r.step({"x": 5}, 0)
    r.step({"x": 11}, 0)                        # enviado em t=0
    r.step({"x": 5}, H)
    assert r.step({"x": 11}, 3 * H)[0].suppressed
    r.step({"x": 5}, 3.5 * H)
    assert not r.step({"x": 11}, 4.1 * H)[0].suppressed  # cooldown conta do último ENVIO


@pytest.mark.parametrize("op, below, above", [("<", 15, 5), ("<=", 15, 10)])
def test_lower_bound_rules(op, below, above):
    r = Runner(rule(op=op, value=10, hysteresis=2, cooldown=0))
    r.step({"x": below}, 0)
    assert len(r.step({"x": above}, 60)) == 1
    assert r.step({"x": 11}, 120) == []          # 11 < 12: ainda não rearmou
    r.step({"x": 12.5}, 180)
    assert len(r.step({"x": above}, 240)) == 1


def test_missing_value_keeps_state():
    r = Runner(rule())
    r.step({"x": 5}, 0)
    assert r.step({"x": None}, 60) == [] and r.step({}, 120) == []
    assert len(r.step({"x": 12}, 180)) == 1


# --- leitura diária (per) ---------------------------------------------------------------

def test_per_key_evaluates_each_new_day_once():
    etf = rule(op="<=", value=-300, hysteresis=0, cooldown=0, path="etf.total", per="etf.date")
    r = Runner(etf)
    r.step({"etf": {"total": -100, "date": "2026-10-13"}}, 0)
    assert len(r.step({"etf": {"total": -400, "date": "2026-10-14"}}, 60)) == 1
    assert r.step({"etf": {"total": -400, "date": "2026-10-14"}}, 120) == []   # mesmo dia de novo
    fires = r.step({"etf": {"total": -350, "date": "2026-10-15"}}, 180)       # dia seguinte também ruim
    assert len(fires) == 1 and fires[0].detail["per"] == "2026-10-15"


# --- faixa do score -------------------------------------------------------------------------

def test_band_index_and_hysteresis():
    assert band_index(-61) == 0 and band_index(0) == 2 and band_index(20) == 2 and band_index(21) == 3
    assert accept_band(22, 2, 5) == 2      # Aquecido por pouco: não muda
    assert accept_band(26, 2, 5) == 3
    assert accept_band(18, 3, 5) == 3      # voltou para Neutro por pouco
    assert accept_band(15, 3, 5) == 2


def test_band_change_fires_once_with_hysteresis():
    band = Rule(id="b", name="B", type="band_change", path="regime.score", hysteresis=5, cooldown=0)
    r = Runner(band)
    r.step({"regime": {"score": 10}}, 0)
    assert r.step({"regime": {"score": 23}}, 60) == []                   # dentro da folga
    fires = r.step({"regime": {"score": 27}}, 120)
    assert [(f.detail["from_band"], f.detail["to_band"]) for f in fires] == [("Neutro", "Aquecido")]
    assert r.step({"regime": {"score": 18}}, 180) == []                  # oscila na fronteira
    assert r.step({"regime": {"score": 22}}, 240) == []
    assert r.step({"regime": {"score": 14}}, 300)[0].detail["to_band"] == "Neutro"


# --- saúde ----------------------------------------------------------------------------------

def test_health_fires_per_collector_after_n_failures_and_rearms():
    health = Rule(id="saude", name="S", type="health", cycles=3, cooldown=0)
    r = Runner(health)

    def src(streak):
        return {"sources": {"okx": {"fail_streak": streak, "source": "OKX", "error": "HTTP 503"}, "bybit": {"fail_streak": 0}}}

    r.step(src(0), 0)
    assert r.step(src(1), 60) == [] and r.step(src(2), 120) == []
    fires = r.step(src(3), 180)
    assert len(fires) == 1 and fires[0].key == "saude:okx" and fires[0].detail["error"] == "HTTP 503"
    assert r.step(src(4), 240) == []
    r.step(src(0), 300)                                                  # voltou
    assert len(r.step(src(3), 360)) == 1


# --- arquivo de regras ----------------------------------------------------------------------

def test_parse_duration():
    assert parse_duration("4h") == 4 * H and parse_duration("30m") == 1800 and parse_duration("1d") == 86400
    assert parse_duration(90) == 90
    with pytest.raises(RuleError):
        parse_duration("quatro horas")


def test_defaults_and_overrides():
    rules = parse_rules("""
[defaults]
cooldown = "2h"
channels = ["telegram"]

[[rules]]
id = "a"
path = "x"
op = ">"
value = 1

[[rules]]
id = "b"
path = "x"
op = "<"
value = 0
cooldown = "10m"
channels = ["n8n"]

[[rules]]
id = "c"
path = "x"
op = ">"
value = 1
enabled = false
""")
    assert [r.id for r in rules] == ["a", "b"]
    assert rules[0].cooldown == 7200 and rules[0].channels == ("telegram",)
    assert rules[1].cooldown == 600 and rules[1].channels == ("n8n",)


@pytest.mark.parametrize(
    "toml, message",
    [
        ('[[rules]]\nid = "a"\nop = ">"\nvalue = 1', "path"),
        ('[[rules]]\nid = "a"\npath = "x"\nop = "!="\nvalue = 1', "op"),
        ('[[rules]]\nid = "A b"\npath = "x"\nop = ">"\nvalue = 1', "id"),
        ('[[rules]]\nid = "a"\ntype = "magic"', "type"),
        ('[[rules]]\nid = "a"\npath = "x"\nop = ">"\nvalue = 1\nchannels = ["email"]', "canal"),
        ('[[rules]]\nid = "a"\npath = "x"\nop = ">"\nvalue = 1\n[[rules]]\nid = "a"\npath = "x"\nop = ">"\nvalue = 1', "repetidos"),
        ('[[rules]\nid = "a"', "TOML"),
    ],
)
def test_invalid_rules_explain_the_problem(toml, message):
    with pytest.raises(RuleError, match=message):
        parse_rules(toml)


def test_shipped_rule_files_match():
    active = load_rules(CONFIG / "alerts.toml")
    example = load_rules(CONFIG / "alerts.example.toml")
    assert [r.id for r in active] == [r.id for r in example]
    assert {r.id: (r.path, r.op, r.value) for r in active} == {r.id: (r.path, r.op, r.value) for r in example}
    funding = next(r for r in active if r.id == "funding-alto")
    assert (funding.op, funding.value, funding.cooldown) == (">", 0.05, 4 * H)
