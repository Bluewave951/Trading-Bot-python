"""Support/Resistance detection (KEY algorithm).

See TRADING_BOT_PLAN.md "Key Algorithms > A. Support/Resistance Detection"
for the spec this implements:

    1. Find swing highs (local max within +/-2 candles)
    2. Find swing lows (local min within +/-2 candles)
    3. Filter out levels touched < 2 times
    4. Calculate Fibonacci from highest swing high -> lowest swing low
    5. Return top 5 of each (by strength/touches)
"""
from __future__ import annotations

import numpy as np

from src.data.models import Candle, SupportResistanceLevels
from src.indicators.fibonacci import DEFAULT_FIB_LEVELS, fib_retracement


def _swing_high_indices(highs: np.ndarray, window: int) -> list[int]:
    """Indices where `highs[i]` is the max of its +/- `window` neighborhood."""
    return [
        i
        for i in range(window, len(highs) - window)
        if highs[i] == highs[i - window : i + window + 1].max()
    ]


def _swing_low_indices(lows: np.ndarray, window: int) -> list[int]:
    """Indices where `lows[i]` is the min of its +/- `window` neighborhood."""
    return [
        i
        for i in range(window, len(lows) - window)
        if lows[i] == lows[i - window : i + window + 1].min()
    ]


def _cluster_levels(
    prices: list[float], tolerance_pct: float = 0.0025
) -> list[tuple[float, int]]:
    """Group nearby prices into levels.

    Prices within `tolerance_pct` of the previous price in sorted order are
    merged into one cluster. Returns `(avg_price, touch_count)` sorted by
    touch count descending (strongest level first).
    """
    if not prices:
        return []
    ordered = sorted(prices)
    clusters: list[list[float]] = [[ordered[0]]]
    for price in ordered[1:]:
        if abs(price - clusters[-1][-1]) / clusters[-1][-1] <= tolerance_pct:
            clusters[-1].append(price)
        else:
            clusters.append([price])
    levels = [(sum(c) / len(c), len(c)) for c in clusters]
    levels.sort(key=lambda level: level[1], reverse=True)
    return levels


def detect_support_resistance(
    candles: list[Candle],
    lookback: int = 50,
    min_touches: int = 2,
    top_n: int = 5,
    swing_window: int = 2,
    fib_levels: list[float] | None = None,
) -> SupportResistanceLevels:
    """Detect support/resistance levels and Fibonacci retracements.

    Levels are swing highs/lows clustered by proximity and required to have
    been touched at least `min_touches` times within the `lookback` window;
    the strongest `top_n` of each are returned, sorted ascending by price.
    """
    if not candles:
        return SupportResistanceLevels(symbol="", timeframe="")

    window_candles = candles[-lookback:]
    last = window_candles[-1]

    highs = np.array([c.high for c in window_candles])
    lows = np.array([c.low for c in window_candles])

    # float() here matters: indexing a numpy array yields np.float64, and
    # that dtype silently propagates through every later computation
    # (cluster averages, Signal.entry/sl/tp, backtest P&L, validation
    # comparisons) until something demands a real Python type — e.g.
    # pydantic/FastAPI JSON serialization, which rejects np.bool_/np.float64
    # outright. Casting at the source avoids chasing it downstream.
    swing_highs = [float(highs[i]) for i in _swing_high_indices(highs, swing_window)]
    swing_lows = [float(lows[i]) for i in _swing_low_indices(lows, swing_window)]

    resistance_clusters = [
        level
        for level in _cluster_levels(swing_highs)
        if level[1] >= min_touches
    ][:top_n]
    support_clusters = [
        level for level in _cluster_levels(swing_lows) if level[1] >= min_touches
    ][:top_n]

    resistance_levels = sorted(price for price, _touches in resistance_clusters)
    support_levels = sorted(price for price, _touches in support_clusters)

    fibs: dict[str, float] = {}
    if swing_highs and swing_lows:
        fibs = fib_retracement(
            max(swing_highs), min(swing_lows), fib_levels or DEFAULT_FIB_LEVELS
        )

    return SupportResistanceLevels(
        symbol=last.symbol,
        timeframe=last.timeframe,
        support_levels=support_levels,
        resistance_levels=resistance_levels,
        fib_levels=fibs,
    )
