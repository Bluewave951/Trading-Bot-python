"""GET /api/level-alerts — history for the dashboard's "Level Watcher
Alerts" section (stats + chart + table). Reads the same `level_alerts`
table `src.level_watcher` writes to, same shared-DB pattern as
`routes/signals.py`.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from src.data.database import Database

router = APIRouter()


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "kind": row["kind"],
        "level": row["level"],
        "price": row["price"],
        "distance_pct": row["distance_pct"],
        "message": row["message"],
        "created_at": row["created_at"],
    }


@router.get("/api/level-alerts")
def list_level_alerts(limit: int = Query(200, ge=1, le=2000)) -> list[dict]:
    """Most recent level-proximity alerts, newest first. The frontend
    computes stats (counts by day/symbol) client-side from this list rather
    than the API pre-aggregating — keeps this route a thin DB read, same as
    `routes/signals.py`."""
    db = Database()
    return [_row_to_dict(row) for row in db.get_recent_level_alerts(limit=limit)]
