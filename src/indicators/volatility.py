"""Bollinger Bands, ATR via pandas-ta."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pandas_ta as ta

from src.indicators._utils import to_array, to_dataframe


@dataclass
class BollingerBands:
    upper: np.ndarray
    middle: np.ndarray  # SMA
    lower: np.ndarray


def bollinger_bands(
    closes: np.ndarray, period: int = 20, std_dev: float = 2.0
) -> BollingerBands:
    """Bollinger Bands: middle SMA +/- `std_dev` standard deviations."""
    df = to_dataframe(
        ta.bbands(pd.Series(closes), length=period, std=std_dev), len(closes), 5
    )
    # Column order is fixed (lower, middle, upper, bandwidth, %B).
    lower, middle, upper = (df.iloc[:, i].to_numpy() for i in range(3))
    return BollingerBands(upper=upper, middle=middle, lower=lower)


def atr(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14
) -> np.ndarray:
    """Average True Range — volatility measure used for SL sizing."""
    return to_array(
        ta.atr(pd.Series(highs), pd.Series(lows), pd.Series(closes), length=period),
        len(closes),
    )
