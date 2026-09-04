"""RSI, MACD, Stochastic via pandas-ta."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pandas_ta as ta

from src.indicators._utils import to_array, to_dataframe


def rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Relative Strength Index, 0-100. <30 oversold, >70 overbought."""
    return to_array(ta.rsi(pd.Series(closes), length=period), len(closes))


@dataclass
class MACDResult:
    macd: np.ndarray
    signal: np.ndarray
    histogram: np.ndarray  # macd - signal; sign flip is the common trigger


def macd(
    closes: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9
) -> MACDResult:
    """MACD line, signal line, and histogram."""
    df = to_dataframe(
        ta.macd(pd.Series(closes), fast=fast, slow=slow, signal=signal), len(closes), 3
    )
    # Column order is fixed (MACD, histogram, signal) regardless of the
    # fast/slow/signal-derived column names, so select by position.
    macd_line, histogram, signal_line = (df.iloc[:, i].to_numpy() for i in range(3))
    return MACDResult(macd=macd_line, signal=signal_line, histogram=histogram)


def stochastic(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, k: int = 14, d: int = 3
) -> tuple[np.ndarray, np.ndarray]:
    """Stochastic oscillator: (%K, %D)."""
    df = to_dataframe(
        ta.stoch(pd.Series(highs), pd.Series(lows), pd.Series(closes), k=k, d=d),
        len(closes),
        3,
    )
    return df.iloc[:, 0].to_numpy(), df.iloc[:, 1].to_numpy()
