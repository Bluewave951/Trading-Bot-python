"""Phase 1 tests: Database and CacheManager (no network required)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.data.database import Database
from src.data.models import Candle, Signal, SignalSide


@pytest.fixture()
def db(tmp_path):
    return Database(db_path=str(tmp_path / "test.db"))


def _make_candle(symbol="BTC/USDT", tf="1h", ts=None, close=100.0) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=tf,
        timestamp=ts or datetime.now(timezone.utc),
        open=close - 1,
        high=close + 1,
        low=close - 2,
        close=close,
        volume=1000.0,
    )


def test_insert_and_get_candles(db: Database):
    base_ts = datetime.now(timezone.utc)
    candles = [
        _make_candle(close=100 + i, ts=base_ts + timedelta(hours=i)) for i in range(5)
    ]
    inserted = db.insert_candles(candles)
    assert inserted == 5

    fetched = db.get_candles("BTC/USDT", "1h", limit=10)
    assert len(fetched) == 5
    # chronological order preserved
    assert fetched[0].close <= fetched[-1].close


def test_insert_candles_deduplicates(db: Database):
    ts = datetime.now(timezone.utc)
    candle = _make_candle(ts=ts)
    db.insert_candles([candle])
    second_insert = db.insert_candles([candle])  # same symbol/tf/timestamp
    assert second_insert == 0


def test_insert_and_get_signal(db: Database):
    signal = Signal(
        symbol="AAPL",
        timeframe="1h",
        side=SignalSide.BUY,
        entry=100.0,
        sl=98.0,
        tp=103.0,
        risk_reward=1.5,
        reasons=["price_near_support", "rsi<50"],
    )
    signal_id = db.insert_signal(signal)
    assert signal_id > 0

    rows = db.get_recent_signals(limit=5)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["side"] == "buy"


def test_signal_risk_reward_properties():
    signal = Signal(
        symbol="AAPL", timeframe="1h", side=SignalSide.BUY,
        entry=100.0, sl=98.0, tp=103.0, risk_reward=1.5,
    )
    assert signal.risk_pct == pytest.approx(0.02)
    assert signal.reward_pct == pytest.approx(0.03)


def test_cache_manager_in_memory_roundtrip():
    from src.data.cache_manager import CacheManager

    cache = CacheManager()  # falls back to in-memory if Redis isn't running
    key = cache.make_key("test", "roundtrip")
    cache.set(key, {"hello": "world"}, timeframe="1h")
    assert cache.get(key) == {"hello": "world"}

    cache.delete(key)
    assert cache.get(key) is None
