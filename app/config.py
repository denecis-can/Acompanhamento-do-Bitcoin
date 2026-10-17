"""Configuração da aplicação, carregada do .env (python-dotenv)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    fred_api_key: str
    sosovalue_api_key: str
    etf_import_dir: Path
    database_path: Path
    log_level: str
    http_timeout: float
    http_max_retries: int
    interval_price: int
    interval_derivatives: int
    interval_volatility: int
    interval_sentiment: int
    interval_macro: int
    interval_etf: int
    interval_stablecoins: int
    interval_mempool: int
    interval_onchain: int
    interval_news: int


@lru_cache
def get_settings() -> Settings:
    db_path = Path(os.getenv("DATABASE_PATH", "data/btc_dashboard.db"))
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path
    return Settings(
        fred_api_key=os.getenv("FRED_API_KEY", "").strip(),
        sosovalue_api_key=os.getenv("SOSOVALUE_API_KEY", "").strip(),
        etf_import_dir=BASE_DIR / "data",  # mesmo diretório montado como volume no Docker
        database_path=db_path,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        http_timeout=float(_int("HTTP_TIMEOUT_SECONDS", 10)),
        http_max_retries=_int("HTTP_MAX_RETRIES", 3),
        interval_price=_int("INTERVAL_PRICE", 60),
        interval_derivatives=_int("INTERVAL_DERIVATIVES", 300),
        interval_volatility=_int("INTERVAL_VOLATILITY", 900),
        interval_sentiment=_int("INTERVAL_SENTIMENT", 3600),
        interval_macro=_int("INTERVAL_MACRO", 43200),
        interval_etf=_int("INTERVAL_ETF", 43200),  # fluxos diários: 2 coletas por dia
        interval_stablecoins=_int("INTERVAL_STABLECOINS", 21600),
        interval_mempool=_int("INTERVAL_MEMPOOL", 600),
        interval_onchain=_int("INTERVAL_ONCHAIN", 21600),
        interval_news=_int("INTERVAL_NEWS", 900),
    )
