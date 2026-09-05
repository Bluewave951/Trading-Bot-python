"""Core dataclasses shared across the data, indicator, and signal layers."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AssetClass(str, Enum):
    STOCK = "stock"
    CRYPTO = "crypto"
    COMMODITY = "commodity"


class Timeframe(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


class SignalSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class Candle:
    """Single OHLCV bar."""
    symbol: str
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "timestamp": self.timestamp.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass
class Symbol:
    """A tradable instrument and where to source its data."""
    ticker: str                # e.g. "AAPL", "BTC/USDT", "XAUUSD"
    asset_class: AssetClass
    display_name: str = ""
    source: str = "yfinance"   # "yfinance" | "okx" | "tradingview"

    def __post_init__(self) -> None:
        if not self.display_name:
            self.display_name = self.ticker


@dataclass
class SupportResistanceLevels:
    """Output of the S/R detection algorithm for one symbol/timeframe."""
    symbol: str
    timeframe: str
    support_levels: list[float] = field(default_factory=list)
    resistance_levels: list[float] = field(default_factory=list)
    fib_levels: dict[str, float] = field(default_factory=dict)
    computed_at: datetime = field(default_factory=_utcnow)


@dataclass
class IndicatorSnapshot:
    """All indicator values needed by the signal engine at a point in time."""
    symbol: str
    timeframe: str
    close: float
    rsi: float
    ema_20: float
    ema_50: float
    ema_200: float
    macd: float
    macd_signal: float
    macd_histogram: float
    volume: float
    volume_avg_20: float
    sr_levels: SupportResistanceLevels
    computed_at: datetime = field(default_factory=_utcnow)
    # Previous bar's histogram value, so signal logic can detect momentum
    # *turning* up/down (histogram rising/falling) rather than only its
    # already-crossed-zero state. NaN if there's no prior bar to compare to.
    macd_histogram_prev: float = float("nan")


@dataclass
class ExitSignal:
    """A trigger to close an already-open long position early, before its
    SL/TP is hit — e.g. "price is at resistance / RSI overbought and
    momentum is turning" (see `src.signals.entry_signals.should_exit_long`).
    Deliberately has no entry/sl/tp/risk_reward of its own: it doesn't open
    a new position, just ends one.
    """
    reasons: list[str] = field(default_factory=list)
    confidence: float = 0.0
    created_at: datetime = field(default_factory=_utcnow)


@dataclass
class Signal:
    """A generated trade signal, ready for risk-sizing and delivery."""
    symbol: str
    timeframe: str
    side: SignalSide
    entry: float
    sl: float
    tp: float
    risk_reward: float
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=_utcnow)

    @property
    def risk_pct(self) -> float:
        return abs(self.entry - self.sl) / self.entry

    @property
    def reward_pct(self) -> float:
        return abs(self.tp - self.entry) / self.entry
