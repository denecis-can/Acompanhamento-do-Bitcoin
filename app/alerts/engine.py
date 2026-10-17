"""Avaliação das regras contra o /api/summary. Funções puras: estado entra e sai como dict.

Antirruído (obrigatório em todas as regras):
1. **cruzamento**: dispara na transição "dentro do limite → além do limite", nunca a cada leitura;
2. **histerese**: depois de disparar, a regra só rearma quando o valor volta além do limite
   com folga (`hysteresis`), então um valor oscilando em cima do limite não gera rajadas;
3. **cooldown**: um novo cruzamento dentro do cooldown da regra é registrado como suprimido;
4. **primeira leitura silenciosa**: uma regra nova (sem estado salvo) só registra o estado
   atual; se o valor já estiver além do limite, não dispara (não houve cruzamento).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.alerts.rules import Rule
from app.services.regime import BANDS

LIMITS = [limit for limit, _ in BANDS]
BAND_NAMES = [name for _, name in BANDS]


@dataclass
class Fire:
    rule: Rule
    key: str
    value: Any
    suppressed: bool = False
    detail: dict[str, Any] = field(default_factory=dict)


def get_path(summary: dict[str, Any], path: str) -> Any:
    node: Any = summary
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or math.isnan(value):
        return None
    return float(value)


def breached(x: float, op: str, limit: float) -> bool:
    return {">": x > limit, ">=": x >= limit, "<": x < limit, "<=": x <= limit}[op]


def rearmed(x: float, op: str, limit: float, hysteresis: float) -> bool:
    """Valor voltou para dentro do limite com a folga da histerese."""
    if op in (">", ">="):
        return x < limit - hysteresis
    return x > limit + hysteresis


def band_index(score: float) -> int:
    return next(i for i, limit in enumerate(LIMITS) if score <= limit)


def accept_band(score: float, current: int, hysteresis: float) -> int:
    """Nova faixa só vale se o score entrou nela pelo menos `hysteresis` além da fronteira."""
    candidate = band_index(score)
    if candidate > current and score > LIMITS[candidate - 1] + hysteresis:
        return candidate
    if candidate < current and score <= LIMITS[candidate] - hysteresis:
        return candidate
    return current


def _cooldown_active(rule: Rule, state: dict, now: float) -> bool:
    last = state.get("last_fired_at")
    return last is not None and now - last < rule.cooldown


def _fire(rule: Rule, key: str, value: Any, state: dict, now: float, **detail: Any) -> Fire:
    suppressed = _cooldown_active(rule, state, now)
    if not suppressed:
        state["last_fired_at"] = now
    return Fire(rule, key, value, suppressed, detail)


def _threshold(rule: Rule, key: str, x: float, per: str | None, prev: dict | None, now: float) -> tuple[Fire | None, dict]:
    if prev is None:  # primeira leitura: só registra
        return None, {"armed": not breached(x, rule.op, rule.value), "last_key": per, "last_value": x, "last_fired_at": None}
    state = {**prev, "last_value": x}
    armed = bool(prev.get("armed"))
    if rule.per and per != prev.get("last_key"):
        armed = True  # leitura nova (ex.: outro dia liquidado)
        state["last_key"] = per
    fire = None
    if armed and breached(x, rule.op, rule.value):
        fire = _fire(rule, key, x, state, now)
        armed = False
    elif not armed and rearmed(x, rule.op, rule.value, rule.hysteresis):
        armed = True
    state["armed"] = armed
    return fire, state


def evaluate(rules: list[Rule], summary: dict[str, Any], states: dict[str, dict], now: float) -> tuple[list[Fire], dict[str, dict]]:
    """Avalia todas as regras. Retorna os disparos e o novo estado de cada chave avaliada."""
    fires: list[Fire] = []
    new_states: dict[str, dict] = {}

    for rule in rules:
        if rule.type == "threshold":
            x = _number(get_path(summary, rule.path))
            per = get_path(summary, rule.per) if rule.per else None
            if x is None or (rule.per and per is None):
                continue  # sem dado agora: estado intacto
            fire, new_states[rule.id] = _threshold(rule, rule.id, x, str(per) if per is not None else None, states.get(rule.id), now)
            if fire:
                if rule.per:
                    fire.detail["per"] = str(per)
                fires.append(fire)

        elif rule.type == "band_change":
            score = _number(get_path(summary, rule.path))
            if score is None:
                continue
            prev = states.get(rule.id)
            if prev is None or prev.get("band") not in BAND_NAMES:
                new_states[rule.id] = {"armed": True, "band": BAND_NAMES[band_index(score)], "last_value": score, "last_fired_at": None}
                continue
            state = {**prev, "last_value": score}
            current = BAND_NAMES.index(prev["band"])
            accepted = accept_band(score, current, rule.hysteresis)
            if accepted != current:
                state["band"] = BAND_NAMES[accepted]
                fires.append(_fire(rule, rule.id, score, state, now, from_band=BAND_NAMES[current], to_band=BAND_NAMES[accepted]))
            new_states[rule.id] = state

        elif rule.type == "health":
            for name, src in (summary.get("sources") or {}).items():
                key = f"{rule.id}:{name}"
                streak = float(src.get("fail_streak") or 0)
                prev = states.get(key)
                if prev is None:
                    new_states[key] = {"armed": streak < rule.cycles, "last_value": streak, "last_fired_at": None}
                    continue
                state = {**prev, "last_value": streak}
                armed = bool(prev.get("armed"))
                if armed and streak >= rule.cycles:
                    fires.append(_fire(rule, key, int(streak), state, now, collector=name,
                                       source=src.get("source"), error=src.get("error")))
                    armed = False
                elif not armed and streak == 0:
                    armed = True
                state["armed"] = armed
                new_states[key] = state

    return fires, new_states
