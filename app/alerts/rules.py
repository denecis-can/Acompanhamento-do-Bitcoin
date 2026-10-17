"""Regras de alerta lidas de um arquivo TOML editado à mão (config/alerts.toml).

Tipos de regra:
- `threshold`: compara o valor em `path` (caminho no /api/summary) com `value` usando `op`
  (>, >=, <, <=). Dispara só ao cruzar o limite e só rearma depois que o valor volta além
  da `hysteresis`. Com `per` (outro caminho, ex.: a data do último dia liquidado), cada
  novo valor de `per` é uma leitura nova, avaliada uma vez.
- `band_change`: dispara quando o score de temperatura muda de faixa; a nova faixa só vale
  se o score entrar nela pelo menos `hysteresis` pontos além da fronteira.
- `health`: por coletor, dispara quando ele falha `cycles` ciclos seguidos; rearma quando
  volta a funcionar.

Todas têm `cooldown` (tempo mínimo entre disparos da mesma regra; padrão 4h) e `channels`.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OPS = (">", ">=", "<", "<=")
TYPES = ("threshold", "band_change", "health")
CHANNELS = ("telegram", "n8n")
DEFAULT_COOLDOWN = 4 * 3600
DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd])\s*$")
UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class RuleError(ValueError):
    """Arquivo de regras inválido; a mensagem diz qual regra e qual campo."""


def parse_duration(value: Any) -> int:
    """'4h', '30m', '1d', '90s' ou número de segundos."""
    if isinstance(value, (int, float)) and value >= 0:
        return int(value)
    m = DURATION.match(str(value))
    if not m:
        raise RuleError(f"duração inválida: {value!r} (use, por exemplo, '30m', '4h' ou '1d')")
    return int(float(m.group(1)) * UNIT_SECONDS[m.group(2)])


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    type: str
    path: str = ""
    op: str = ">"
    value: float = 0.0
    hysteresis: float = 0.0
    per: str | None = None
    cycles: int = 3
    unit: str = ""
    cooldown: int = DEFAULT_COOLDOWN
    channels: tuple[str, ...] = CHANNELS
    enabled: bool = True
    description: str = ""

    def condition_text(self) -> str:
        if self.type == "threshold":
            return f"{self.op} {self.value:g}{(' ' + self.unit) if self.unit else ''}"
        if self.type == "band_change":
            return f"mudança de faixa (histerese {self.hysteresis:g} pts)"
        return f"{self.cycles} falhas seguidas"


def _rule(raw: dict[str, Any], defaults: dict[str, Any], index: int) -> Rule:
    where = f"regra #{index + 1}" + (f" ({raw['id']})" if "id" in raw else "")
    try:
        rid = str(raw["id"]).strip()
        rtype = str(raw.get("type", "threshold"))
    except KeyError as exc:
        raise RuleError(f"{where}: campo obrigatório ausente: {exc.args[0]}") from None
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", rid):
        raise RuleError(f"{where}: id deve usar só letras minúsculas, números, '-' e '_'")
    if rtype not in TYPES:
        raise RuleError(f"{where}: type deve ser um de {', '.join(TYPES)}")

    merged = {**defaults, **raw}
    channels = tuple(merged.get("channels", CHANNELS))
    unknown = [c for c in channels if c not in CHANNELS]
    if unknown:
        raise RuleError(f"{where}: canal desconhecido {unknown}; use {', '.join(CHANNELS)}")

    kwargs: dict[str, Any] = {
        "id": rid,
        "name": str(merged.get("name", rid)),
        "type": rtype,
        "unit": str(merged.get("unit", "")),
        "cooldown": parse_duration(merged.get("cooldown", DEFAULT_COOLDOWN)),
        "channels": channels,
        "enabled": bool(merged.get("enabled", True)),
        "description": str(merged.get("description", "")),
    }
    try:
        if rtype == "threshold":
            op = str(merged["op"])
            if op not in OPS:
                raise RuleError(f"{where}: op deve ser um de {' '.join(OPS)}")
            kwargs.update(path=str(merged["path"]), op=op, value=float(merged["value"]),
                          hysteresis=float(merged.get("hysteresis", 0)), per=merged.get("per"))
        elif rtype == "band_change":
            kwargs.update(path=str(merged.get("path", "regime.score")), hysteresis=float(merged.get("hysteresis", 5)))
        else:
            kwargs.update(cycles=int(merged.get("cycles", 3)))
            if kwargs["cycles"] < 1:
                raise RuleError(f"{where}: cycles deve ser >= 1")
    except RuleError:
        raise
    except KeyError as exc:
        raise RuleError(f"{where}: campo obrigatório ausente: {exc.args[0]}") from None
    except (TypeError, ValueError) as exc:
        raise RuleError(f"{where}: valor inválido ({exc})") from None
    if kwargs.get("hysteresis", 0) < 0:
        raise RuleError(f"{where}: hysteresis não pode ser negativa")
    return Rule(**kwargs)


def parse_rules(text: str) -> list[Rule]:
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise RuleError(f"TOML inválido: {exc}") from None
    defaults = doc.get("defaults", {})
    rules = [_rule(r, defaults, i) for i, r in enumerate(doc.get("rules", []))]
    ids = [r.id for r in rules]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise RuleError(f"ids repetidos: {', '.join(sorted(dup))}")
    return [r for r in rules if r.enabled]


def load_rules(path: Path) -> list[Rule]:
    return parse_rules(path.read_text(encoding="utf-8"))
