"""Phase 6 tests: the FastAPI dashboard's HTTP API + WebSocket signal push.

`fastapi.testclient.TestClient` drives the app in-process (no real network
socket), matching the isolation style used across this suite: only the
network-facing boundaries (`DataFetcher`, `Database`, notification
channels) are faked out, real internal wiring (routing, JSON
serialization, `run_backtest`) is exercised for real.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import src.web.app as web_app
import src.web.routes.backtest as backtest_route
import src.web.routes.level_alerts as level_alerts_route
import src.web.routes.signals as signals_route
from src.data.database import Database
from src.data.models import Candle, Signal, SignalSide
from src.web.websocket_manager import ConnectionManager, new_signal_events


@pytest.fixture()
def client():
    with TestClient(web_app.app) as c:
        yield c


def _signal(symbol="AAPL") -> Signal:
    return Signal(
        symbol=symbol, timeframe="1h", side=SignalSide.BUY,
        entry=100.0, sl=98.0, tp=104.0, risk_reward=2.0, confidence=1.0,
        reasons=["price_near_support"],
    )


def _cycle_candles(n: int, symbol="TEST", tf="1h") -> list[Candle]:
    """A repeating support/resistance triangle wave — same shape used in
    test_indicators.py, long enough here to clear run_backtest's default
    min_history (50) with room to actually scan some bars."""
    cycle = [90.0, 92.0, 94.0, 96.0, 98.0, 100.0, 98.0, 96.0, 94.0, 92.0]
    prices = (cycle * ((n // len(cycle)) + 1))[:n]
    base = datetime.now(timezone.utc)
    return [
        Candle(
            symbol=symbol, timeframe=tf, timestamp=base + timedelta(hours=i),
            open=p, high=p, low=p, close=p, volume=1000.0,
        )
        for i, p in enumerate(prices)
    ]


# -- /health ---------------------------------------------------------

def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# -- / (dashboard page) + static files ---------------------------------------------------------

def test_dashboard_page_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Trading Bot Dashboard" in resp.text


def test_static_assets_served(client):
    assert client.get("/static/charts.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200


# -- /api/signals ---------------------------------------------------------

def test_list_signals_empty(client, tmp_path, monkeypatch):
    db = Database(db_path=str(tmp_path / "test.db"))
    monkeypatch.setattr(signals_route, "Database", lambda: db)

    resp = client.get("/api/signals")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_signals_returns_persisted_rows(client, tmp_path, monkeypatch):
    db = Database(db_path=str(tmp_path / "test.db"))
    db.insert_signal(_signal(symbol="AAPL"))
    db.insert_signal(_signal(symbol="MSFT"))
    monkeypatch.setattr(signals_route, "Database", lambda: db)

    resp = client.get("/api/signals?limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert {row["symbol"] for row in body} == {"AAPL", "MSFT"}
    assert body[0]["reasons"] == ["price_near_support"]


def test_list_signals_limit_validated(client):
    assert client.get("/api/signals?limit=0").status_code == 422
    assert client.get("/api/signals?limit=501").status_code == 422


# -- /api/level-alerts ---------------------------------------------------------

def test_list_level_alerts_empty(client, tmp_path, monkeypatch):
    db = Database(db_path=str(tmp_path / "test.db"))
    monkeypatch.setattr(level_alerts_route, "Database", lambda: db)

    resp = client.get("/api/level-alerts")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_level_alerts_returns_persisted_rows(client, tmp_path, monkeypatch):
    db = Database(db_path=str(tmp_path / "test.db"))
    db.insert_level_alert(
        symbol="AAPL", timeframe="1h", kind="support", level=99.5,
        price=100.0, distance_pct=0.005, message="msg1",
    )
    db.insert_level_alert(
        symbol="BTC/USDT", timeframe="1h", kind="resistance", level=101.0,
        price=100.0, distance_pct=0.01, message="msg2",
    )
    monkeypatch.setattr(level_alerts_route, "Database", lambda: db)

    resp = client.get("/api/level-alerts?limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert {row["symbol"] for row in body} == {"AAPL", "BTC/USDT"}
    assert {row["kind"] for row in body} == {"support", "resistance"}


def test_list_level_alerts_limit_validated(client):
    assert client.get("/api/level-alerts?limit=0").status_code == 422
    assert client.get("/api/level-alerts?limit=2001").status_code == 422


# -- /api/test ---------------------------------------------------------

def test_send_test_alert_dispatches_via_queue(client, monkeypatch):
    class _FakeQueue:
        def dispatch(self, message):
            _FakeQueue.last_message = message
            return {"FakeChannel": True}

    monkeypatch.setattr(signals_route, "default_notification_queue", lambda: _FakeQueue())

    resp = client.post("/api/test", json={"message": "hello dashboard"})
    assert resp.status_code == 200
    assert resp.json() == {"dispatched": {"FakeChannel": True}}
    assert _FakeQueue.last_message == "hello dashboard"


def test_send_test_alert_default_message(client, monkeypatch):
    captured = {}

    class _FakeQueue:
        def dispatch(self, message):
            captured["message"] = message
            return {}

    monkeypatch.setattr(signals_route, "default_notification_queue", lambda: _FakeQueue())

    resp = client.post("/api/test", json={})
    assert resp.status_code == 200
    assert "Test alert" in captured["message"]


# -- /api/backtest ---------------------------------------------------------

def test_run_backtest_api_returns_valid_json(client, monkeypatch):
    # Regression coverage for the numpy-leak bug: support_resistance.py's
    # levels used to be np.float64, which pydantic/FastAPI can't serialize
    # (see support_resistance.py's comment) — this exercises the real
    # serialization path end-to-end, not just the fixed function directly.
    class _FakeFetcher:
        def fetch_ohlcv(self, symbol, timeframe, limit=1000):
            return _cycle_candles(limit)

    monkeypatch.setattr(backtest_route, "DataFetcher", _FakeFetcher)

    resp = client.get("/api/backtest?symbol=BTC/USDT&timeframe=1h&limit=120")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "TEST"  # from the fake candles' Candle.symbol
    assert "stats" in body and "validation" in body
    assert isinstance(body["validation"]["passed"], bool)
    for check_name, ok in body["validation"]["checks"].items():
        assert isinstance(ok, bool)  # would be np.bool_ pre-fix, which fails to even reach here
    assert isinstance(body["equity_curve"], list)
    assert isinstance(body["trades"], list)


def test_run_backtest_api_infinite_profit_factor_is_json_null(client, monkeypatch):
    import dataclasses

    class _FakeFetcher:
        def fetch_ohlcv(self, symbol, timeframe, limit=1000):
            return _cycle_candles(limit)

    monkeypatch.setattr(backtest_route, "DataFetcher", _FakeFetcher)

    original_calculate_stats = backtest_route.calculate_stats

    def _force_infinite_profit_factor(result):
        return dataclasses.replace(original_calculate_stats(result), profit_factor=float("inf"))

    monkeypatch.setattr(backtest_route, "calculate_stats", _force_infinite_profit_factor)

    resp = client.get("/api/backtest?symbol=AAPL&timeframe=1h&limit=120")
    assert resp.status_code == 200
    assert resp.json()["stats"]["profit_factor"] is None


def test_run_backtest_api_rejects_insufficient_data(client, monkeypatch):
    class _FakeFetcher:
        def fetch_ohlcv(self, symbol, timeframe, limit=1000):
            return _cycle_candles(10)  # well under the 100-candle floor

    monkeypatch.setattr(backtest_route, "DataFetcher", _FakeFetcher)

    resp = client.get("/api/backtest?symbol=AAPL&timeframe=1h&limit=1000")
    assert resp.status_code == 400


@pytest.mark.parametrize(
    "ticker,expected_source",
    [("btc/usdt", "binance"), ("aapl", "yfinance")],
)
def test_resolve_symbol_routes_by_ticker_shape(ticker, expected_source):
    symbol = backtest_route._resolve_symbol(ticker)
    assert symbol.source == expected_source
    assert symbol.ticker == ticker.upper()


# -- websocket_manager ---------------------------------------------------------

def test_new_signal_events_filters_and_advances_last_seen_id():
    rows = [
        {"id": 1, "symbol": "AAPL", "timeframe": "1h", "side": "buy", "entry": 1.0,
         "sl": 0.9, "tp": 1.1, "risk_reward": 1.0, "confidence": 1.0, "created_at": "t1"},
        {"id": 2, "symbol": "MSFT", "timeframe": "1h", "side": "buy", "entry": 2.0,
         "sl": 1.9, "tp": 2.1, "risk_reward": 1.0, "confidence": 1.0, "created_at": "t2"},
    ]
    events, last_seen_id = new_signal_events(rows, last_seen_id=1)
    assert last_seen_id == 2
    assert len(events) == 1
    assert events[0]["symbol"] == "MSFT"
    assert events[0]["type"] == "signal"


def test_new_signal_events_no_new_rows():
    rows = [{"id": 1, "symbol": "AAPL", "timeframe": "1h", "side": "buy", "entry": 1.0,
             "sl": 0.9, "tp": 1.1, "risk_reward": 1.0, "confidence": 1.0, "created_at": "t1"}]
    events, last_seen_id = new_signal_events(rows, last_seen_id=1)
    assert events == []
    assert last_seen_id == 1


@pytest.mark.asyncio
async def test_connection_manager_broadcast_reaches_connected_clients():
    manager = ConnectionManager()
    sent = []

    class _FakeSocket:
        async def accept(self):
            pass

        async def send_text(self, payload):
            sent.append(payload)

    ws = _FakeSocket()
    await manager.connect(ws)
    await manager.broadcast({"type": "signal", "symbol": "AAPL"})

    assert len(sent) == 1
    assert "AAPL" in sent[0]


@pytest.mark.asyncio
async def test_connection_manager_drops_failed_connections():
    manager = ConnectionManager()

    class _FailingSocket:
        async def accept(self):
            pass

        async def send_text(self, payload):
            raise RuntimeError("connection closed")

    ws = _FailingSocket()
    await manager.connect(ws)
    await manager.broadcast({"type": "signal"})  # should not raise

    assert ws not in manager._connections  # pruned after the failed send


def test_websocket_endpoint_accepts_connection(client):
    with client.websocket_connect("/ws") as websocket:
        # The endpoint doesn't proactively send anything; just confirm the
        # handshake succeeds and the connection can be closed cleanly.
        websocket.close()
