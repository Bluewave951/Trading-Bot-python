"""Web Dashboard — Phase 6.

See TRADING_BOT_PLAN.md section "7. Web Dashboard". `app.py` is the FastAPI
entry point (`uvicorn src.web.app:app`); it serves `static/index.html` (a
hand-rolled vanilla-JS dashboard — no React/Vue build step), the
`routes/*` JSON API, and a `/ws` WebSocket for live signal push (see
`websocket_manager.py`).
"""
