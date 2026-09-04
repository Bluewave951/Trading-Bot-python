"""Fibonacci retracement levels from swing high -> swing low."""
from __future__ import annotations

DEFAULT_FIB_LEVELS = [0.236, 0.382, 0.5, 0.618, 0.786]


def fib_retracement(
    swing_high: float, swing_low: float, levels: list[float] | None = None
) -> dict[str, float]:
    """Retracement price for each ratio, measured down from `swing_high`.

    Ratio 0.0 maps to `swing_high`, 1.0 maps to `swing_low`; e.g. the 0.618
    level is 61.8% of the way from high to low — a common pullback target
    in an uptrend. Keys are the ratio as a string (e.g. "0.618").
    """
    levels = levels if levels is not None else DEFAULT_FIB_LEVELS
    diff = swing_high - swing_low
    return {str(level): swing_high - diff * level for level in levels}
