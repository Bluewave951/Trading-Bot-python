"""SQLite schema & queries.

Uses the stdlib `sqlite3` module directly (no ORM) to keep the data layer
lightweight and easy to reason about. All access goes through `Database`.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from src.config import settings
from src.data.models import Candle, Signal, SignalSide
from src.logger import get_logger

logger = get_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    UNIQUE(symbol, timeframe, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_candles_lookup
    ON candles (symbol, timeframe, timestamp);

CREATE TABLE IF NOT EXISTS sr_levels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    support_levels TEXT NOT NULL,      -- JSON array
    resistance_levels TEXT NOT NULL,   -- JSON array
    fib_levels TEXT NOT NULL,          -- JSON object
    computed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sr_lookup
    ON sr_levels (symbol, timeframe, computed_at);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    side TEXT NOT NULL,
    entry REAL NOT NULL,
    sl REAL NOT NULL,
    tp REAL NOT NULL,
    risk_reward REAL NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    reasons TEXT NOT NULL DEFAULT '[]',  -- JSON array
    status TEXT NOT NULL DEFAULT 'open', -- open | hit_tp | hit_sl | expired
    created_at TEXT NOT NULL,
    closed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_lookup
    ON signals (symbol, timeframe, created_at);

-- One row per individual S/R proximity hit dispatched by
-- src.level_watcher (a symbol near both support and resistance in the same
-- pass produces two rows, one combined notification message).
CREATE TABLE IF NOT EXISTS level_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    kind TEXT NOT NULL,          -- support | resistance
    level REAL NOT NULL,
    price REAL NOT NULL,
    distance_pct REAL NOT NULL,
    message TEXT NOT NULL,       -- the full notification text sent
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_level_alerts_lookup
    ON level_alerts (created_at);
"""


class Database:
    """Thin wrapper around a SQLite connection for the trading bot schema."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or settings.database_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
        logger.info("Database schema ready at %s", self.db_path)

    # -- Candles ----------------------------------------------------------
    def insert_candles(self, candles: list[Candle]) -> int:
        """Bulk insert candles, ignoring duplicates on (symbol, tf, timestamp)."""
        if not candles:
            return 0
        with self._connect() as conn:
            cur = conn.executemany(
                """
                INSERT OR IGNORE INTO candles
                    (symbol, timeframe, timestamp, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        c.symbol,
                        c.timeframe,
                        c.timestamp.isoformat(),
                        c.open,
                        c.high,
                        c.low,
                        c.close,
                        c.volume,
                    )
                    for c in candles
                ],
            )
            return cur.rowcount

    def get_candles(
        self, symbol: str, timeframe: str, limit: int = 200
    ) -> list[Candle]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM candles
                WHERE symbol = ? AND timeframe = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (symbol, timeframe, limit),
            ).fetchall()
        candles = [
            Candle(
                symbol=r["symbol"],
                timeframe=r["timeframe"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
                open=r["open"],
                high=r["high"],
                low=r["low"],
                close=r["close"],
                volume=r["volume"],
            )
            for r in rows
        ]
        return list(reversed(candles))  # chronological order

    # -- Signals ------------------------------------------------------------
    def insert_signal(self, signal: Signal) -> int:
        import json

        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO signals
                    (symbol, timeframe, side, entry, sl, tp, risk_reward,
                     confidence, reasons, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
                """,
                (
                    signal.symbol,
                    signal.timeframe,
                    signal.side.value,
                    signal.entry,
                    signal.sl,
                    signal.tp,
                    signal.risk_reward,
                    signal.confidence,
                    json.dumps(signal.reasons),
                    signal.created_at.isoformat(),
                ),
            )
            return cur.lastrowid

    def get_recent_signals(self, limit: int = 50) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM signals ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()

    # -- Level watcher alerts ------------------------------------------------

    def insert_level_alert(
        self,
        symbol: str,
        timeframe: str,
        kind: str,
        level: float,
        price: float,
        distance_pct: float,
        message: str,
        created_at: datetime | None = None,
    ) -> int:
        from datetime import timezone

        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO level_alerts
                    (symbol, timeframe, kind, level, price, distance_pct, message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol, timeframe, kind, level, price, distance_pct, message,
                    (created_at or datetime.now(timezone.utc)).isoformat(),
                ),
            )
            return cur.lastrowid

    def get_recent_level_alerts(self, limit: int = 200) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM level_alerts ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
