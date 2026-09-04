"""FastAPI main app with WebSocket (KEY).

Mounts routes.health/signals/backtest, serves the dashboard at `/`, and
pushes new signals to connected clients over `/ws` (see
`websocket_manager.py`). Run with:

    uvicorn src.web.app:app --reload --port 8000

(or `python -m src.web.app` for a plain run without `--reload`).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.config import settings
from src.logger import get_logger, setup_logging
from src.web.routes import backtest as backtest_routes
from src.web.routes import health as health_routes
from src.web.routes import level_alerts as level_alerts_routes
from src.web.routes import signals as signals_routes
from src.web.websocket_manager import ConnectionManager, poll_and_broadcast_new_signals

logger = get_logger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"

manager = ConnectionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    poll_task = asyncio.create_task(poll_and_broadcast_new_signals(manager))
    logger.info("Web dashboard started; polling data/trading.db for new signals")
    try:
        yield
    finally:
        poll_task.cancel()


app = FastAPI(title="Trading Bot Dashboard", lifespan=lifespan)

app.include_router(health_routes.router)
app.include_router(signals_routes.router)
app.include_router(backtest_routes.router)
app.include_router(level_alerts_routes.router)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    try:
        while True:
            # The client sends nothing meaningful; this just keeps the
            # connection open and detects disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.api_host, port=settings.api_port)
