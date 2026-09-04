"""Tests for the standalone S/R-proximity watcher (src/level_watcher.py).

Same isolation style as test_scheduler.py: fake fetcher/queue/db, monkeypatch
`load_level_watchlist` and `calculate_all_indicators`, no network.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import src.level_watcher as level_watcher
from src.data.database import Database
from src.data.models import (
    AssetClass,
    Candle,
    IndicatorSnapshot,
    Symbol,
    SupportResistanceLevels,
)


def _candles(n: int, symbol="TEST", tf="1h", price=100.0) -> list[Candle]:
    base = datetime.now(timezone.utc)
    return [
        Candle(
            symbol=symbol, timeframe=tf, timestamp=base + timedelta(hours=i),
            open=price, high=price + 1, low=price - 1, close=price, volume=1000.0,
        )
        for i in range(n)
    ]


def _snapshot(close: float, support: list[float], resistance: list[float]) -> IndicatorSnapshot:
    sr = SupportResistanceLevels(symbol="TEST", timeframe="1h", support_levels=support, resistance_levels=resistance)
    return IndicatorSnapshot(
        symbol="TEST", timeframe="1h", close=close, rsi=50.0,
        ema_20=close, ema_50=close, ema_200=close,
        macd=0.0, macd_signal=0.0, macd_histogram=0.0,
        volume=1000.0, volume_avg_20=1000.0, sr_levels=sr,
    )


class _FakeFetcher:
    def __init__(self, n=300):
        self.n = n

    def fetch_ohlcv(self, symbol, timeframe, limit=250):
        return _candles(self.n, symbol=symbol.ticker, tf=timeframe)


class _RecordingQueue:
    def __init__(self):
        self.dispatched: list[str] = []

    def dispatch(self, message):
        self.dispatched.append(message)
        return {}


# -- _is_near ---------------------------------------------------------

def test_is_near_true_within_tolerance():
    assert level_watcher._is_near(100.5, 100.0, proximity_pct=0.01) is True


def test_is_near_false_outside_tolerance():
    assert level_watcher._is_near(105.0, 100.0, proximity_pct=0.01) is False


# -- check_symbol ---------------------------------------------------------

def test_check_symbol_returns_none_with_insufficient_data(monkeypatch):
    fetcher = _FakeFetcher(n=5)
    symbol = Symbol(ticker="AAPL", asset_class=AssetClass.STOCK, source="yfinance")
    result = level_watcher.check_symbol(fetcher, symbol, "1h", proximity_pct=0.01)
    assert result is None


def test_check_symbol_returns_none_when_not_near_any_level(monkeypatch):
    monkeypatch.setattr(
        level_watcher, "calculate_all_indicators",
        lambda candles: _snapshot(close=100.0, support=[50.0], resistance=[200.0]),
    )
    fetcher = _FakeFetcher(n=300)
    symbol = Symbol(ticker="AAPL", asset_class=AssetClass.STOCK, source="yfinance")
    result = level_watcher.check_symbol(fetcher, symbol, "1h", proximity_pct=0.01)
    assert result is None


def test_check_symbol_alerts_when_near_support(monkeypatch):
    monkeypatch.setattr(
        level_watcher, "calculate_all_indicators",
        lambda candles: _snapshot(close=100.0, support=[99.7], resistance=[200.0]),
    )
    fetcher = _FakeFetcher(n=300)
    symbol = Symbol(ticker="AAPL", asset_class=AssetClass.STOCK, source="yfinance")
    result = level_watcher.check_symbol(fetcher, symbol, "1h", proximity_pct=0.01)
    assert result is not None
    assert result.price == pytest.approx(100.0)
    assert len(result.hits) == 1
    assert result.hits[0].kind == "support"
    assert result.hits[0].level == pytest.approx(99.7)
    assert "AAPL" in result.message
    assert "แนวรับ" in result.message
    assert "99.7" in result.message


def test_check_symbol_alerts_when_near_resistance(monkeypatch):
    monkeypatch.setattr(
        level_watcher, "calculate_all_indicators",
        lambda candles: _snapshot(close=100.0, support=[10.0], resistance=[100.5]),
    )
    fetcher = _FakeFetcher(n=300)
    symbol = Symbol(ticker="BTC/USDT", asset_class=AssetClass.CRYPTO, source="binance")
    result = level_watcher.check_symbol(fetcher, symbol, "1h", proximity_pct=0.01)
    assert result is not None
    assert result.hits[0].kind == "resistance"
    assert "แนวต้าน" in result.message


def test_check_symbol_reports_multiple_hits(monkeypatch):
    # Price sandwiched close to both a support and a resistance at once.
    monkeypatch.setattr(
        level_watcher, "calculate_all_indicators",
        lambda candles: _snapshot(close=100.0, support=[99.6], resistance=[100.4]),
    )
    fetcher = _FakeFetcher(n=300)
    symbol = Symbol(ticker="AAPL", asset_class=AssetClass.STOCK, source="yfinance")
    result = level_watcher.check_symbol(fetcher, symbol, "1h", proximity_pct=0.01)
    assert len(result.hits) == 2
    assert {h.kind for h in result.hits} == {"support", "resistance"}
    assert result.message.count("ใกล้แนวรับ") == 1
    assert result.message.count("ใกล้แนวต้าน") == 1


# -- run_check ---------------------------------------------------------

def test_run_check_dispatches_and_persists_only_for_hits(tmp_path, monkeypatch):
    watchlist = [
        Symbol(ticker="NEAR_LEVEL", asset_class=AssetClass.STOCK, source="yfinance"),
        Symbol(ticker="FAR_FROM_LEVEL", asset_class=AssetClass.STOCK, source="yfinance"),
    ]
    monkeypatch.setattr(level_watcher, "load_level_watchlist", lambda: watchlist)
    monkeypatch.setattr(level_watcher, "DataFetcher", lambda: _FakeFetcher(n=300))

    queue = _RecordingQueue()
    monkeypatch.setattr(level_watcher, "default_notification_queue", lambda: queue)

    db = Database(db_path=str(tmp_path / "test.db"))
    monkeypatch.setattr(level_watcher, "Database", lambda: db)

    def fake_check(fetcher, symbol, timeframe, proximity_pct):
        if symbol.ticker != "NEAR_LEVEL":
            return None
        return level_watcher.LevelCheckResult(
            price=100.0,
            hits=[level_watcher.LevelHit(kind="support", level=99.5, distance_pct=0.005)],
            message="alert for NEAR_LEVEL",
        )

    monkeypatch.setattr(level_watcher, "check_symbol", fake_check)

    alerts = level_watcher.run_check()

    assert alerts == ["alert for NEAR_LEVEL"]
    assert queue.dispatched == ["alert for NEAR_LEVEL"]

    rows = db.get_recent_level_alerts(limit=10)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "NEAR_LEVEL"
    assert rows[0]["kind"] == "support"
    assert rows[0]["level"] == pytest.approx(99.5)


def test_run_check_persists_one_row_per_hit(tmp_path, monkeypatch):
    watchlist = [Symbol(ticker="AAPL", asset_class=AssetClass.STOCK, source="yfinance")]
    monkeypatch.setattr(level_watcher, "load_level_watchlist", lambda: watchlist)
    monkeypatch.setattr(level_watcher, "DataFetcher", lambda: _FakeFetcher(n=300))
    monkeypatch.setattr(level_watcher, "default_notification_queue", lambda: _RecordingQueue())

    db = Database(db_path=str(tmp_path / "test.db"))
    monkeypatch.setattr(level_watcher, "Database", lambda: db)

    def fake_check(fetcher, symbol, timeframe, proximity_pct):
        return level_watcher.LevelCheckResult(
            price=100.0,
            hits=[
                level_watcher.LevelHit(kind="support", level=99.5, distance_pct=0.005),
                level_watcher.LevelHit(kind="resistance", level=100.5, distance_pct=0.005),
            ],
            message="combined message",
        )

    monkeypatch.setattr(level_watcher, "check_symbol", fake_check)

    level_watcher.run_check()

    rows = db.get_recent_level_alerts(limit=10)
    assert len(rows) == 2
    assert {r["kind"] for r in rows} == {"support", "resistance"}


def test_run_check_continues_after_one_symbol_errors(tmp_path, monkeypatch):
    watchlist = [
        Symbol(ticker="BAD", asset_class=AssetClass.STOCK, source="yfinance"),
        Symbol(ticker="OK", asset_class=AssetClass.STOCK, source="yfinance"),
    ]
    monkeypatch.setattr(level_watcher, "load_level_watchlist", lambda: watchlist)
    monkeypatch.setattr(level_watcher, "DataFetcher", lambda: _FakeFetcher(n=300))
    monkeypatch.setattr(level_watcher, "default_notification_queue", lambda: _RecordingQueue())
    monkeypatch.setattr(level_watcher, "Database", lambda: Database(db_path=str(tmp_path / "test.db")))

    def fake_check(fetcher, symbol, timeframe, proximity_pct):
        if symbol.ticker == "BAD":
            raise RuntimeError("simulated failure")
        return level_watcher.LevelCheckResult(
            price=100.0, hits=[level_watcher.LevelHit(kind="support", level=99.5, distance_pct=0.005)],
            message="alert for OK",
        )

    monkeypatch.setattr(level_watcher, "check_symbol", fake_check)

    alerts = level_watcher.run_check()
    assert alerts == ["alert for OK"]


def test_run_check_no_hits_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setattr(level_watcher, "load_level_watchlist", lambda: [])
    monkeypatch.setattr(level_watcher, "DataFetcher", lambda: _FakeFetcher())
    monkeypatch.setattr(level_watcher, "default_notification_queue", lambda: _RecordingQueue())
    monkeypatch.setattr(level_watcher, "Database", lambda: Database(db_path=str(tmp_path / "test.db")))

    assert level_watcher.run_check() == []


# -- build_scheduler ---------------------------------------------------------

def test_build_scheduler_registers_level_watch_job():
    sched = level_watcher.build_scheduler()
    job_ids = {job.id for job in sched.get_jobs()}
    assert job_ids == {"level_watch"}


# -- Database.level_alerts ---------------------------------------------------------

def test_database_insert_and_get_level_alerts(tmp_path):
    db = Database(db_path=str(tmp_path / "test.db"))
    db.insert_level_alert(
        symbol="AAPL", timeframe="1h", kind="support", level=99.5,
        price=100.0, distance_pct=0.005, message="msg",
    )
    db.insert_level_alert(
        symbol="BTC/USDT", timeframe="1h", kind="resistance", level=101.0,
        price=100.0, distance_pct=0.01, message="msg2",
    )
    rows = db.get_recent_level_alerts(limit=10)
    assert len(rows) == 2
    assert {r["symbol"] for r in rows} == {"AAPL", "BTC/USDT"}
