"""Orchestrator: runs all indicator calculations for a symbol/timeframe and
assembles a `src.data.models.IndicatorSnapshot`.
"""
from __future__ import annotations

import numpy as np

from src.config import settings
from src.data.models import Candle, IndicatorSnapshot
from src.indicators.moving_averages import ema
from src.indicators.oscillators import macd, rsi
from src.indicators.support_resistance import detect_support_resistance
from src.indicators.volumes import volume_avg


def calculate_all_indicators(candles: list[Candle]) -> IndicatorSnapshot:
    """Compute the full indicator set for the latest candle in `candles`.

    `candles` must be chronological (oldest first). Periods configured
    longer than `len(candles)` (e.g. EMA 200 on 50 candles) simply yield
    NaN for that field rather than raising — callers filtering signals
    should treat NaN indicator values as "not enough data yet".
    """
    if not candles:
        raise ValueError("calculate_all_indicators requires at least one candle")

    cfg = settings.indicators
    closes = np.array([c.close for c in candles])
    volumes = np.array([c.volume for c in candles])

    ema_short, ema_mid, ema_long = (cfg.ema_periods + [20, 50, 200])[:3]
    ema_20 = ema(closes, ema_short)
    ema_50 = ema(closes, ema_mid)
    ema_200 = ema(closes, ema_long)
    rsi_values = rsi(closes, cfg.rsi_period)
    macd_result = macd(closes, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    volume_avg_values = volume_avg(volumes, cfg.volume_avg_period)

    sr_levels = detect_support_resistance(
        candles,
        lookback=cfg.sr_lookback_candles,
        min_touches=cfg.sr_min_touches,
        fib_levels=cfg.fib_levels,
    )

    last = candles[-1]
    histogram_prev = (
        float(macd_result.histogram[-2]) if len(macd_result.histogram) >= 2 else float("nan")
    )
    return IndicatorSnapshot(
        symbol=last.symbol,
        timeframe=last.timeframe,
        close=last.close,
        rsi=float(rsi_values[-1]),
        ema_20=float(ema_20[-1]),
        ema_50=float(ema_50[-1]),
        ema_200=float(ema_200[-1]),
        macd=float(macd_result.macd[-1]),
        macd_signal=float(macd_result.signal[-1]),
        macd_histogram=float(macd_result.histogram[-1]),
        macd_histogram_prev=histogram_prev,
        volume=last.volume,
        volume_avg_20=float(volume_avg_values[-1]),
        sr_levels=sr_levels,
    )
