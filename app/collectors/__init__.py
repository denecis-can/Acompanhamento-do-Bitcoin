"""Registro dos coletores ativos."""

from __future__ import annotations

from app.collectors.base import BaseCollector, CollectorResult
from app.collectors.binance_futures import BinanceFuturesCollector
from app.collectors.binance_spot import BinanceSpotCollector
from app.collectors.coinmetrics import CoinMetricsCollector
from app.collectors.bybit import BybitCollector
from app.collectors.coinbase import CoinbaseCollector
from app.collectors.deribit import DeribitCollector
from app.collectors.deribit_options import DeribitOptionsCollector
from app.collectors.farside import FarsideCollector
from app.collectors.fear_greed import FearGreedCollector
from app.collectors.fred import FredCollector
from app.collectors.mempool import MempoolCollector
from app.collectors.news import NewsCollector
from app.collectors.okx import OkxCollector
from app.collectors.stablecoins import StablecoinsCollector
from app.config import Settings
from app.core.http import HttpClient

__all__ = ["BaseCollector", "CollectorResult", "build_collectors"]


def build_collectors(http: HttpClient, s: Settings) -> list[BaseCollector]:
    return [
        BinanceSpotCollector(http, s.interval_price),
        CoinbaseCollector(http, s.interval_price),
        BinanceFuturesCollector(http, s.interval_derivatives),
        BybitCollector(http, s.interval_derivatives),
        OkxCollector(http, s.interval_derivatives),
        DeribitCollector(http, s.interval_volatility),
        DeribitOptionsCollector(http, s.interval_volatility),
        FearGreedCollector(http, s.interval_sentiment),
        FredCollector(http, s.interval_macro, api_key=s.fred_api_key),
        FarsideCollector(http, s.interval_etf),
        StablecoinsCollector(http, s.interval_stablecoins),
        MempoolCollector(http, s.interval_mempool),
        CoinMetricsCollector(http, s.interval_onchain),
        NewsCollector(http, s.interval_news),
    ]
