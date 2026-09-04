"""Internal helpers shared by the indicator wrapper modules.

`pandas-ta` returns `None` (not a NaN-filled Series/DataFrame) whenever
there isn't enough history to compute even the first value — e.g. asking
for a 200-period EMA on 120 candles. The rest of this package expects a
same-length array/frame back either way, so every public indicator
function normalizes through here instead of handling `None` itself.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def to_array(result: pd.Series | None, length: int) -> np.ndarray:
    """Normalize a single-column pandas-ta result to a NaN-filled array of `length`."""
    if result is None:
        return np.full(length, np.nan)
    return result.to_numpy()


def to_dataframe(result: pd.DataFrame | None, length: int, n_cols: int) -> pd.DataFrame:
    """Normalize a multi-column pandas-ta result to an all-NaN frame of `length` rows."""
    if result is None:
        return pd.DataFrame(np.full((length, n_cols), np.nan))
    return result
