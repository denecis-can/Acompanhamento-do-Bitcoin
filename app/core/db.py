"""Cache e histórico em SQLite.

- `latest`: último resultado de cada coletor. Em caso de falha, mantém o último dado
  válido e registra apenas a tentativa e o erro (o front continua exibindo o cache).
- `series`: pontos (métrica, timestamp, valor) para histórico e variações.
- `candles`: OHLCV do par principal.
- `etf_flows`: fluxo diário liquidado dos ETFs de BTC à vista (US$ mi), por ticker e
  "TOTAL". Acumula histórico além do último mês que a SoSoValue devolve.
- `alert_state`: estado de cada regra de alerta (armada ou disparada, última faixa,
  último disparo), para o antirruído sobreviver a reinícios.
- `alert_events`: cada alerta disparado (enviado, suprimido ou com falha) e cada
  resumo diário, com o payload e o resultado por canal.
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
    error         TEXT,
    fail_streak   INTEGER NOT NULL DEFAULT 0
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
CREATE TABLE IF NOT EXISTS alert_state (
    rule_key      TEXT PRIMARY KEY,
    armed         INTEGER NOT NULL,
    band          TEXT,
    last_key      TEXT,
    last_value    REAL,
    last_fired_at REAL,
    updated_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS alert_events (
    id         TEXT PRIMARY KEY,
    rule_key   TEXT NOT NULL,
    kind       TEXT NOT NULL,
    fired_at   REAL NOT NULL,
    status     TEXT NOT NULL,
    message    TEXT NOT NULL,
    payload    TEXT NOT NULL,
    deliveries TEXT NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS alert_events_fired_at ON alert_events (fired_at);
"""

# Colunas adicionadas depois da primeira versão do banco (ALTER TABLE idempotente).
MIGRATIONS = ("ALTER TABLE latest ADD COLUMN fail_streak INTEGER NOT NULL DEFAULT 0",)

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
        for sql in MIGRATIONS:
            try:
                await self._conn.execute(sql)
            except aiosqlite.OperationalError:
                pass  # coluna já existe
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
            INSERT INTO latest (collector, data, fetched_at, last_attempt, ok, error, fail_streak)
            VALUES (?, ?, ?, ?, 1, NULL, 0)
            ON CONFLICT(collector) DO UPDATE SET
                data = excluded.data, fetched_at = excluded.fetched_at,
                last_attempt = excluded.last_attempt, ok = 1, error = NULL, fail_streak = 0
            """,
            (collector, json.dumps(data), at, at),
        )
        await self.conn.commit()

    async def save_failure(self, collector: str, error: str, at: float | None = None) -> None:
        at = at or time.time()
        await self.conn.execute(
            """
            INSERT INTO latest (collector, data, fetched_at, last_attempt, ok, error, fail_streak)
            VALUES (?, NULL, NULL, ?, 0, ?, 1)
            ON CONFLICT(collector) DO UPDATE SET
                last_attempt = excluded.last_attempt, ok = 0, error = excluded.error,
                fail_streak = latest.fail_streak + 1
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
                "fail_streak": r["fail_streak"],
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

    # --- alertas ------------------------------------------------------------

    async def get_alert_states(self) -> dict[str, dict[str, Any]]:
        async with self.conn.execute("SELECT * FROM alert_state") as cur:
            rows = await cur.fetchall()
        return {r["rule_key"]: {k: r[k] for k in r.keys() if k != "rule_key"} for r in rows}

    async def save_alert_state(self, rule_key: str, state: dict[str, Any]) -> None:
        await self.conn.execute(
            """
            INSERT INTO alert_state (rule_key, armed, band, last_key, last_value, last_fired_at, updated_at)
            VALUES (:rule_key, :armed, :band, :last_key, :last_value, :last_fired_at, :updated_at)
            ON CONFLICT(rule_key) DO UPDATE SET
                armed = excluded.armed, band = excluded.band, last_key = excluded.last_key,
                last_value = excluded.last_value, last_fired_at = excluded.last_fired_at,
                updated_at = excluded.updated_at
            """,
            {
                "rule_key": rule_key,
                "armed": int(bool(state.get("armed"))),
                "band": state.get("band"),
                "last_key": state.get("last_key"),
                "last_value": state.get("last_value"),
                "last_fired_at": state.get("last_fired_at"),
                "updated_at": time.time(),
            },
        )
        await self.conn.commit()

    async def insert_alert_event(self, event: dict[str, Any]) -> None:
        await self.conn.execute(
            """INSERT INTO alert_events (id, rule_key, kind, fired_at, status, message, payload, deliveries, attempts)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event["id"], event["rule_key"], event["kind"], event["fired_at"], event["status"],
                event["message"], json.dumps(event["payload"], ensure_ascii=False),
                json.dumps(event["deliveries"]), event.get("attempts", 0),
            ),
        )
        await self.conn.commit()

    async def update_alert_delivery(self, event_id: str, status: str, deliveries: dict[str, str], attempts: int) -> None:
        await self.conn.execute(
            "UPDATE alert_events SET status = ?, deliveries = ?, attempts = ? WHERE id = ?",
            (status, json.dumps(deliveries), attempts, event_id),
        )
        await self.conn.commit()

    async def get_alert_events(self, since_ts: float, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        sql = "SELECT * FROM alert_events WHERE fired_at >= ?"
        args: list[Any] = [since_ts]
        if status:
            sql += " AND status = ?"
            args.append(status)
        sql += " ORDER BY fired_at DESC LIMIT ?"
        args.append(limit)
        async with self.conn.execute(sql, args) as cur:
            rows = await cur.fetchall()
        return [
            {**dict(r), "payload": json.loads(r["payload"]), "deliveries": json.loads(r["deliveries"])}
            for r in rows
        ]

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
