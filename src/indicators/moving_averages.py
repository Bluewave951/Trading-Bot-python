"""SMA, EMA via pandas-ta.

    def sma(closes: np.ndarray, period: int) -> np.ndarray: ...
    def ema(closes: np.ndarray, period: int) -> np.ndarray: ...

Both return an array the same length as `closes`; the first `period - 1`
values are NaN (not enough history yet to fill the window).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pandas_ta as ta

from src.indicators._utils import to_array


def sma(closes: np.ndarray, period: int) -> np.ndarray:
    """Simple moving average."""
    return to_array(ta.sma(pd.Series(closes), length=period), len(closes))


def ema(closes: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average."""
    return to_array(ta.ema(pd.Series(closes), length=period), len(closes))
