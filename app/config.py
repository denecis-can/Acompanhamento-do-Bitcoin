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


def _path(raw: str) -> Path:
    p = Path(raw.strip())
    return p if p.is_absolute() else BASE_DIR / p


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    return raw in ("1", "true", "yes", "sim") if raw else default


@dataclass(frozen=True)
class Settings:
    demo_mode: bool
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
    # alertas (segredos só no .env)
    telegram_bot_token: str
    telegram_chat_id: str
    n8n_webhook_url: str
    alerts_file: Path
    alerts_interval: int
    timezone: str
    dashboard_url: str
    daily_summary_time: str


@lru_cache
def get_settings() -> Settings:
    db_path = Path(os.getenv("DATABASE_PATH", "data/btc_dashboard.db"))
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path
    return Settings(
        demo_mode=_bool("DEMO_MODE"),
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
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        n8n_webhook_url=os.getenv("N8N_WEBHOOK_URL", "").strip(),
        alerts_file=_path(os.getenv("ALERTS_FILE", "config/alerts.toml")),
        alerts_interval=_int("ALERTS_INTERVAL", 60),
        timezone=os.getenv("TIMEZONE", "America/Sao_Paulo").strip() or "America/Sao_Paulo",
        dashboard_url=os.getenv("DASHBOARD_URL", "http://localhost:8000").strip().rstrip("/"),
        daily_summary_time=os.getenv("DAILY_SUMMARY_TIME", "08:00"),
    )
