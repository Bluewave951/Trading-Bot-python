"""Phase 7-ish tests: the scheduler's scan-and-alert pipeline (no network).

`DataFetcher.fetch_ohlcv`, `generate_buy_signal`, and `load_watchlist` are
monkeypatched so these exercise the wiring (fetch -> indicators -> signal ->
persist -> notify) deterministically, without needing synthetic OHLC that
happens to satisfy the full BUY confluence or a live network/API call.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import src.scheduler as scheduler
from src.data.database import Database
from src.data.models import AssetClass, Candle, Signal, SignalSide, Symbol
from src.notifications.notification_queue import NotificationQueue


def _candles(n: int, symbol="AAPL", tf="1h", price=100.0) -> list[Candle]:
    base = datetime.now(timezone.utc)
    return [
        Candle(
            symbol=symbol, timeframe=tf, timestamp=base + timedelta(hours=i),
            open=price, high=price + 1, low=price - 1, close=price, volume=1000.0,
        )
        for i in range(n)
    ]


class _FakeFetcher:
    """Stands in for `DataFetcher`: returns canned candles, no network."""

    def __init__(self, candles_by_symbol: dict[str, list[Candle]] | None = None, default_n=300):
        self.candles_by_symbol = candles_by_symbol or {}
        self.default_n = default_n
        self.calls: list[tuple[str, str]] = []

    def fetch_ohlcv(self, symbol: Symbol, timeframe: str, limit: int = 250) -> list[Candle]:
        self.calls.append((symbol.ticker, timeframe))
        if symbol.ticker in self.candles_by_symbol:
            return self.candles_by_symbol[symbol.ticker]
        return _candles(self.default_n, symbol=symbol.ticker, tf=timeframe)


class _RecordingQueue:
    """Stands in for `NotificationQueue`: records what would have been sent."""

    def __init__(self):
        self.dispatched: list[str] = []

    def dispatch(self, message: str) -> dict[str, bool]:
        self.dispatched.append(message)
        return {}


def _signal(symbol="AAPL", tf="1h") -> Signal:
    return Signal(
        symbol=symbol, timeframe=tf, side=SignalSide.BUY,
        entry=100.0, sl=98.0, tp=104.0, risk_reward=2.0, confidence=1.0,
        reasons=["test"],
    )


# -- scan_symbol_timeframe ---------------------------------------------------------

def test_scan_symbol_timeframe_skips_when_not_enough_candles(tmp_path, monkeypatch):
    def _fail_if_called(snapshot):
        raise AssertionError("should not compute a signal on insufficient data")

    monkeypatch.setattr(scheduler, "generate_buy_signal", _fail_if_called)
    fetcher = _FakeFetcher(candles_by_symbol={"AAPL": _candles(5)})  # far below sr_lookback_candles
    db = Database(db_path=str(tmp_path / "test.db"))
    queue = _RecordingQueue()
    symbol = Symbol(ticker="AAPL", asset_class=AssetClass.STOCK, source="yfinance")

    result = scheduler.scan_symbol_timeframe(fetcher, db, queue, symbol, "1h")

    assert result is None
    assert queue.dispatched == []


def test_scan_symbol_timeframe_persists_and_notifies_on_signal(tmp_path, monkeypatch):
    signal = _signal()
    monkeypatch.setattr(scheduler, "generate_buy_signal", lambda snap: signal)

    fetcher = _FakeFetcher(default_n=300)
    db = Database(db_path=str(tmp_path / "test.db"))
    queue = _RecordingQueue()
    symbol = Symbol(ticker="AAPL", asset_class=AssetClass.STOCK, source="yfinance")

    result = scheduler.scan_symbol_timeframe(fetcher, db, queue, symbol, "1h")

    assert result is signal
    assert len(queue.dispatched) == 1
    assert "AAPL" in queue.dispatched[0]

    rows = db.get_recent_signals(limit=5)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAPL"


def test_scan_symbol_timeframe_returns_none_when_no_signal(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler, "generate_buy_signal", lambda snap: None)

    fetcher = _FakeFetcher(default_n=300)
    db = Database(db_path=str(tmp_path / "test.db"))
    queue = _RecordingQueue()
    symbol = Symbol(ticker="AAPL", asset_class=AssetClass.STOCK, source="yfinance")

    result = scheduler.scan_symbol_timeframe(fetcher, db, queue, symbol, "1h")

    assert result is None
    assert queue.dispatched == []
    assert db.get_recent_signals(limit=5) == []


# -- scan_watchlist ---------------------------------------------------------

def test_scan_watchlist_aggregates_signals_across_symbols(tmp_path, monkeypatch):
    watchlist = [
        Symbol(ticker="AAPL", asset_class=AssetClass.STOCK, source="yfinance"),
        Symbol(ticker="MSFT", asset_class=AssetClass.STOCK, source="yfinance"),
        Symbol(ticker="TSLA", asset_class=AssetClass.STOCK, source="yfinance"),
    ]
    monkeypatch.setattr(scheduler, "load_watchlist", lambda: watchlist)

    def fake_scan(fetcher, db, queue, symbol, timeframe):
        return _signal(symbol=symbol.ticker, tf=timeframe) if symbol.ticker != "MSFT" else None

    monkeypatch.setattr(scheduler, "scan_symbol_timeframe", fake_scan)

    fetcher = _FakeFetcher()
    db = Database(db_path=str(tmp_path / "test.db"))
    queue = _RecordingQueue()

    signals = scheduler.scan_watchlist(fetcher, db, queue, "1h")

    assert {s.symbol for s in signals} == {"AAPL", "TSLA"}


def test_scan_watchlist_continues_after_one_symbol_errors(tmp_path, monkeypatch):
    watchlist = [
        Symbol(ticker="BAD", asset_class=AssetClass.STOCK, source="yfinance"),
        Symbol(ticker="AAPL", asset_class=AssetClass.STOCK, source="yfinance"),
    ]
    monkeypatch.setattr(scheduler, "load_watchlist", lambda: watchlist)

    def fake_scan(fetcher, db, queue, symbol, timeframe):
        if symbol.ticker == "BAD":
            raise RuntimeError("simulated API failure")
        return _signal(symbol=symbol.ticker, tf=timeframe)

    monkeypatch.setattr(scheduler, "scan_symbol_timeframe", fake_scan)

    fetcher = _FakeFetcher()
    db = Database(db_path=str(tmp_path / "test.db"))
    queue = _RecordingQueue()

    signals = scheduler.scan_watchlist(fetcher, db, queue, "1h")

    assert len(signals) == 1
    assert signals[0].symbol == "AAPL"


# -- build_scheduler ---------------------------------------------------------

def test_build_scheduler_registers_three_jobs(tmp_path):
    fetcher = _FakeFetcher()
    db = Database(db_path=str(tmp_path / "test.db"))
    queue = NotificationQueue()  # no channels registered -> dispatch is a safe no-op

    # build_scheduler() only assembles jobs; it's never started here, so
    # there's no background thread to shut down.
    sched = scheduler.build_scheduler(fetcher=fetcher, db=db, notification_queue=queue)
    job_ids = {job.id for job in sched.get_jobs()}
    assert job_ids == {"scan_1h", "scan_4h", "scan_1d"}
