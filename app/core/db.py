"""Cache e histórico em SQLite.

- `latest`: último resultado de cada coletor. Em caso de falha, mantém o último dado
  válido e registra apenas a tentativa e o erro (o front continua exibindo o cache).
- `series`: pontos (métrica, timestamp, valor) para histórico e variações.
- `candles`: OHLCV do par principal.
- `etf_flows`: fluxo diário liquidado dos ETFs de BTC à vista (US$ mi), por ticker e
  "TOTAL". Acumula histórico além do último mês que a SoSoValue devolve.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS latest (
    collector     TEXT PRIMARY KEY,
    data          TEXT,
    fetched_at    REAL,
    last_attempt  REAL NOT NULL,
    ok            INTEGER NOT NULL,
    error         TEXT
);
CREATE TABLE IF NOT EXISTS series (
    metric TEXT    NOT NULL,
    ts     INTEGER NOT NULL,
    value  REAL    NOT NULL,
    PRIMARY KEY (metric, ts)
);
CREATE TABLE IF NOT EXISTS candles (
    symbol    TEXT    NOT NULL,
    interval  TEXT    NOT NULL,
    open_time INTEGER NOT NULL,
    open      REAL NOT NULL,
    high      REAL NOT NULL,
    low       REAL NOT NULL,
    close     REAL NOT NULL,
    volume    REAL NOT NULL,
    PRIMARY KEY (symbol, interval, open_time)
);
CREATE TABLE IF NOT EXISTS etf_flows (
    date       TEXT NOT NULL,
    ticker     TEXT NOT NULL,
    flow_usd_mi REAL NOT NULL,
    source     TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (date, ticker)
);
"""

# Fonte de maior precedência no histórico de ETFs: um dia vindo dela nunca é
# sobrescrito por uma importação manual.
ETF_PRIMARY_SOURCE = "SoSoValue"


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database não conectado")
        return self._conn

    # --- latest -----------------------------------------------------------

    async def save_success(self, collector: str, data: dict[str, Any], at: float | None = None) -> None:
        at = at or time.time()
        await self.conn.execute(
            """
            INSERT INTO latest (collector, data, fetched_at, last_attempt, ok, error)
            VALUES (?, ?, ?, ?, 1, NULL)
            ON CONFLICT(collector) DO UPDATE SET
                data = excluded.data, fetched_at = excluded.fetched_at,
                last_attempt = excluded.last_attempt, ok = 1, error = NULL
            """,
            (collector, json.dumps(data), at, at),
        )
        await self.conn.commit()

    async def save_failure(self, collector: str, error: str, at: float | None = None) -> None:
        at = at or time.time()
        await self.conn.execute(
            """
            INSERT INTO latest (collector, data, fetched_at, last_attempt, ok, error)
            VALUES (?, NULL, NULL, ?, 0, ?)
            ON CONFLICT(collector) DO UPDATE SET
                last_attempt = excluded.last_attempt, ok = 0, error = excluded.error
            """,
            (collector, at, error[:500]),
        )
        await self.conn.commit()

    async def get_latest(self, collector: str) -> dict[str, Any] | None:
        return (await self.get_latest_all()).get(collector)

    async def get_latest_all(self) -> dict[str, dict[str, Any]]:
        async with self.conn.execute("SELECT * FROM latest") as cur:
            rows = await cur.fetchall()
        return {
            r["collector"]: {
                "data": json.loads(r["data"]) if r["data"] else None,
                "fetched_at": r["fetched_at"],
                "last_attempt": r["last_attempt"],
                "ok": bool(r["ok"]),
                "error": r["error"],
            }
            for r in rows
        }

    # --- series -----------------------------------------------------------

    async def upsert_series(self, points: Iterable[tuple[str, int, float]]) -> None:
        await self.conn.executemany(
            "INSERT OR REPLACE INTO series (metric, ts, value) VALUES (?, ?, ?)", list(points)
        )
        await self.conn.commit()

    async def get_series(self, metric: str, since_ts: int = 0, limit: int = 5000) -> list[tuple[int, float]]:
        async with self.conn.execute(
            "SELECT ts, value FROM series WHERE metric = ? AND ts >= ? ORDER BY ts DESC LIMIT ?",
            (metric, since_ts, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [(r["ts"], r["value"]) for r in reversed(rows)]

    # --- candles ----------------------------------------------------------

    async def upsert_candles(self, symbol: str, interval: str, candles: Iterable[tuple]) -> None:
        await self.conn.executemany(
            """INSERT OR REPLACE INTO candles
               (symbol, interval, open_time, open, high, low, close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [(symbol, interval, *c) for c in candles],
        )
        await self.conn.commit()

    # --- etf_flows --------------------------------------------------------

    async def upsert_etf_flows(self, rows: Iterable[tuple[str, str, float, str]]) -> None:
        """Grava dias liquidados sem duplicar: (data, ticker) é a chave; a fonte principal tem precedência."""
        now = time.time()
        await self.conn.executemany(
            """
            INSERT INTO etf_flows (date, ticker, flow_usd_mi, source, updated_at) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(date, ticker) DO UPDATE SET
                flow_usd_mi = excluded.flow_usd_mi, source = excluded.source, updated_at = excluded.updated_at
            WHERE excluded.source = ? OR etf_flows.source != ?
            """,
            [(*r, now, ETF_PRIMARY_SOURCE, ETF_PRIMARY_SOURCE) for r in rows],
        )
        await self.conn.commit()

    async def get_etf_flows(self, since_date: str, ticker: str = "TOTAL") -> list[dict[str, Any]]:
        async with self.conn.execute(
            "SELECT date, flow_usd_mi, source FROM etf_flows WHERE ticker = ? AND date >= ? ORDER BY date",
            (ticker, since_date),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_candles(self, symbol: str, interval: str, limit: int = 500) -> list[dict[str, Any]]:
        async with self.conn.execute(
            """SELECT open_time, open, high, low, close, volume FROM candles
               WHERE symbol = ? AND interval = ? ORDER BY open_time DESC LIMIT ?""",
            (symbol, interval, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in reversed(rows)]
