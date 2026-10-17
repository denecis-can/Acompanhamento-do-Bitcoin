"""Fluxos dos ETFs de BTC à vista nos EUA: SoSoValue (principal) e importação manual da Farside (reserva).

Ordem de decisão a cada coleta:
1. com SOSOVALUE_API_KEY, busca na API da SoSoValue;
2. sem chave, ou se a API falhar, lê data/farside_import.html|.htm|.csv, se existir
   (o mais recente, se houver mais de um);
3. sem nenhum dos dois, falha com um erro que diz o que configurar.

O card mostra a fonte em uso e a data do último dado. Os fluxos são diários: duas
coletas por dia bastam, e o scheduler não repete a coleta num reinício se o cache
ainda estiver dentro do intervalo (ver `cache_on_restart`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from app.collectors import farside, sosovalue
from app.collectors.base import BaseCollector, CollectorError, CollectorResult
from app.core.http import HttpError

log = logging.getLogger("collector.etf")

IMPORT_STEM = "farside_import"
IMPORT_SUFFIXES = (".html", ".htm", ".csv")
MAX_IMPORT_BYTES = 5 * 1024 * 1024


def find_import(directory: Path) -> Path | None:
    files = [p for p in directory.glob(f"{IMPORT_STEM}.*") if p.suffix.lower() in IMPORT_SUFFIXES and p.is_file()]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def read_import(path: Path) -> dict:
    if path.stat().st_size > MAX_IMPORT_BYTES:
        raise CollectorError(f"{path.name}: arquivo grande demais")
    text = path.read_text(encoding="utf-8", errors="replace")
    data = farside.parse_csv(text) if path.suffix.lower() == ".csv" else farside.parse(text)
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    data["import_file"] = path.name
    data["imported_at"] = modified.isoformat(timespec="seconds")
    return data


def _ts(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())


class EtfCollector(BaseCollector):
    name = "etf"
    source = "SoSoValue / Farside (importação)"
    cache_on_restart = True

    def __init__(self, http, interval_seconds: int, api_key: str, import_dir: Path) -> None:
        super().__init__(http, interval_seconds)
        self.api_key = api_key
        self.import_dir = import_dir

    async def collect(self) -> CollectorResult:
        primary_error = None
        if self.api_key:
            try:
                data = await sosovalue.fetch(self.http, self.api_key)
                data.update(source="SoSoValue", source_kind="api", fallback=False)
                return self._result(data)
            except (HttpError, CollectorError) as exc:
                primary_error = str(exc)
                log.warning("SoSoValue falhou, tentando importação manual", extra={"error": primary_error})
        else:
            primary_error = "SOSOVALUE_API_KEY não configurada no .env"

        path = find_import(self.import_dir)
        if path is None:
            raise CollectorError(
                f"{primary_error}; e não há importação manual em data/{IMPORT_STEM}.html ou .csv"
            )
        data = read_import(path)
        data.update(source="Farside (importação manual)", source_kind="import", fallback=True, primary_error=primary_error)
        return self._result(data)

    def cache_is_reusable(self, data: dict) -> bool:
        # cache vindo da importação manual não segura a coleta: a chave pode ter acabado de ser configurada
        return not data.get("fallback")

    @staticmethod
    def _result(data: dict) -> CollectorResult:
        return CollectorResult(data=data, series=[("etf.flow_total", _ts(d["date"]), d["total"]) for d in data["days"]])
