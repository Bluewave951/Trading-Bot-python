"""GET /api/signals, POST /api/test.

Reads the same SQLite database `src.scheduler` (a separate process) writes
to — the web app never talks to the scheduler directly, only through the
shared `Database`. See `src.web.websocket_manager` for how new rows reach
connected dashboard clients.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Query
from pydantic import BaseModel

from src.data.database import Database
from src.scheduler import default_notification_queue

router = APIRouter()


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "side": row["side"],
        "entry": row["entry"],
        "sl": row["sl"],
        "tp": row["tp"],
        "risk_reward": row["risk_reward"],
        "confidence": row["confidence"],
        "reasons": json.loads(row["reasons"]),
        "status": row["status"],
        "created_at": row["created_at"],
        "closed_at": row["closed_at"],
    }


@router.get("/api/signals")
def list_signals(limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    """Most recent signals, newest first."""
    db = Database()
    return [_row_to_dict(row) for row in db.get_recent_signals(limit=limit)]


class TestAlertRequest(BaseModel):
    message: str = "Test alert from the dashboard"


@router.post("/api/test")
def send_test_alert(body: TestAlertRequest) -> dict:
    """Dispatch `body.message` to every currently *enabled* notification
    channel — a manual "does delivery actually work" check from the UI,
    independent of whether any real signal has fired."""
    queue = default_notification_queue()
    results = queue.dispatch(body.message)
    return {"dispatched": results}
