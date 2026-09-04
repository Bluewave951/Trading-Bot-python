"""Real-time push of new signals to connected dashboard clients.

`src.scheduler` (a separate process, `python -m src.scheduler`) writes
signals to the shared SQLite database and knows nothing about WebSockets.
This module bridges the gap from the *web app's* side instead: a background
asyncio task polls `Database.get_recent_signals()` on an interval and
broadcasts anything new — no coupling between the two processes beyond the
DB file they both already read/write.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import WebSocket

from src.data.database import Database
from src.logger import get_logger

logger = get_logger(__name__)

POLL_INTERVAL_SECONDS = 5


class ConnectionManager:
    """Tracks connected dashboard WebSocket clients and fans out broadcasts."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self._connections:
            self._connections.remove(websocket)

    async def broadcast(self, message: dict) -> None:
        payload = json.dumps(message, default=str)
        stale: list[WebSocket] = []
        for connection in self._connections:
            try:
                await connection.send_text(payload)
            except Exception:
                stale.append(connection)
        for connection in stale:
            self.disconnect(connection)


def _row_to_event(row) -> dict:
    return {
        "type": "signal",
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "side": row["side"],
        "entry": row["entry"],
        "sl": row["sl"],
        "tp": row["tp"],
        "risk_reward": row["risk_reward"],
        "confidence": row["confidence"],
        "created_at": row["created_at"],
    }


def new_signal_events(rows, last_seen_id: int) -> tuple[list[dict], int]:
    """Pure filtering step, split out from `poll_and_broadcast_new_signals`
    so it's testable without driving the `while True` polling loop: given
    the latest `rows` (any order) and the highest id seen so far, returns
    (broadcast-ready events for the new ones, oldest-first, updated
    last_seen_id).
    """
    new_rows = [row for row in rows if row["id"] > last_seen_id]
    if not new_rows:
        return [], last_seen_id
    updated_last_seen_id = max(row["id"] for row in rows)
    events = [_row_to_event(row) for row in sorted(new_rows, key=lambda r: r["id"])]
    return events, updated_last_seen_id


async def poll_and_broadcast_new_signals(manager: ConnectionManager) -> None:
    """Background task (started in `app.py`'s lifespan): every
    `POLL_INTERVAL_SECONDS`, check for signals inserted since the last poll
    and broadcast each as a `{"type": "signal", ...}` event. Runs until
    cancelled at app shutdown.
    """
    db = Database()

    # Don't replay history on startup — only broadcast signals that arrive
    # from now on.
    most_recent = db.get_recent_signals(limit=1)
    last_seen_id = most_recent[0]["id"] if most_recent else 0

    while True:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        try:
            rows = db.get_recent_signals(limit=20)
        except Exception:
            logger.exception("Failed polling signals for websocket broadcast")
            continue

        events, last_seen_id = new_signal_events(rows, last_seen_id)
        for event in events:
            await manager.broadcast(event)
