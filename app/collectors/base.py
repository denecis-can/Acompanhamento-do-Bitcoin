"""Interface comum dos coletores."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from app.core.http import HttpClient


class CollectorError(Exception):
    """Resposta recebida, mas inválida ou inesperada."""


@dataclass
class CandleBatch:
    symbol: str
    interval: str
    # (open_time_ms, open, high, low, close, volume)
    rows: list[tuple[int, float, float, float, float, float]]


@dataclass
class CollectorResult:
    data: dict[str, Any]
    # (métrica, timestamp em segundos, valor)
    series: list[tuple[str, int, float]] = field(default_factory=list)
    candles: list[CandleBatch] = field(default_factory=list)
    # (data ISO, ticker ou "TOTAL", fluxo em US$ mi, fonte) — só dias liquidados
    etf_flows: list[tuple[str, str, float, str]] = field(default_factory=list)


class BaseCollector(ABC):
    #: identificador único (chave no cache)
    name: ClassVar[str]
    #: nome legível da fonte, exibido nos cards
    source: ClassVar[str]
    #: no startup, reaproveita o cache se ainda estiver dentro do intervalo (fontes com cota)
    cache_on_restart: ClassVar[bool] = False

    def __init__(self, http: HttpClient, interval_seconds: int) -> None:
        self.http = http
        self.interval_seconds = interval_seconds
        self.ran_once = False

    def cache_is_reusable(self, data: dict) -> bool:
        """Se um dado em cache pode substituir a primeira coleta após um reinício."""
        return True

    @property
    def stale_after(self) -> int:
        """Idade a partir da qual o dado é considerado atrasado (3 ciclos perdidos)."""
        return self.interval_seconds * 3

    @abstractmethod
    async def collect(self) -> CollectorResult:
        """Busca e normaliza os dados. Deve levantar exceção em caso de falha."""
