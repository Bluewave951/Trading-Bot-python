"""OBV, Volume Profile, N-period volume average."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pandas_ta as ta

from src.indicators._utils import to_array


def obv(closes: np.ndarray, volumes: np.ndarray) -> np.ndarray:
    """On-Balance Volume — cumulative volume signed by price direction."""
    return to_array(ta.obv(pd.Series(closes), pd.Series(volumes)), len(closes))


def volume_avg(volumes: np.ndarray, period: int = 20) -> np.ndarray:
    """Simple moving average of volume, used to detect buying/selling pressure."""
    return to_array(ta.sma(pd.Series(volumes), length=period), len(volumes))


def volume_profile(
    closes: np.ndarray, volumes: np.ndarray, bins: int = 10
) -> dict[str, float]:
    """Bucket traded volume by price level.

    Returns `{bucket_low_price: total_volume}` across `bins` equal-width
    price buckets spanning the range of `closes`, keyed low-to-high.
    """
    if len(closes) == 0:
        return {}
    totals, edges = np.histogram(closes, bins=bins, weights=volumes)
    return {f"{edges[i]:.4f}": float(totals[i]) for i in range(len(totals))}
