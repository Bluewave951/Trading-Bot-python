"""Phase 2 tests: indicator calculations and S/R detection (no network)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from src.data.models import Candle
from src.indicators.fibonacci import fib_retracement
from src.indicators.indicator_manager import calculate_all_indicators
from src.indicators.moving_averages import ema, sma
from src.indicators.oscillators import macd, rsi, stochastic
from src.indicators.support_resistance import detect_support_resistance
from src.indicators.volatility import atr, bollinger_bands
from src.indicators.volumes import obv, volume_avg


def _trending_closes(n=100, start=100.0, step=0.5) -> np.ndarray:
    return start + np.arange(n) * step


def _candles_from_prices(prices: list[float], symbol="TEST", tf="1h") -> list[Candle]:
    base = datetime.now(timezone.utc)
    return [
        Candle(
            symbol=symbol,
            timeframe=tf,
            timestamp=base + timedelta(hours=i),
            open=p,
            high=p,
            low=p,
            close=p,
            volume=1000.0 + i,
        )
        for i, p in enumerate(prices)
    ]


# -- Moving averages ---------------------------------------------------------

def test_sma_matches_manual_average():
    closes = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    result = sma(closes, period=3)
    assert np.isnan(result[:2]).all()
    assert result[2] == pytest.approx(2.0)  # (1+2+3)/3
    assert result[3] == pytest.approx(3.0)  # (2+3+4)/3
    assert result[4] == pytest.approx(4.0)  # (3+4+5)/3


def test_ema_returns_nan_array_not_none_when_data_too_short():
    # pandas-ta returns bare `None` (not a NaN-filled Series) when there's
    # not enough history for even the first value of a long period — the
    # wrapper must normalize that so callers always get a same-length array.
    closes = np.array([100.0, 101.0, 102.0])
    result = ema(closes, period=200)
    assert isinstance(result, np.ndarray)
    assert len(result) == len(closes)
    assert np.isnan(result).all()


def test_ema_reacts_faster_than_sma_to_recent_move():
    closes = np.concatenate([np.full(30, 100.0), np.full(10, 110.0)])
    e = ema(closes, period=10)
    s = sma(closes, period=10)
    # Right at the jump, EMA (weights recent bars more heavily) should have
    # moved further from the old price than SMA (still mostly old values).
    jump_idx = 30
    assert e[jump_idx] > s[jump_idx]


# -- Oscillators ---------------------------------------------------------

def test_rsi_bounds_and_extremes():
    up = _trending_closes(60, start=100.0, step=1.0)
    down = _trending_closes(60, start=100.0, step=-1.0)
    r_up = rsi(up, period=14)
    r_down = rsi(down, period=14)
    valid_up = r_up[~np.isnan(r_up)]
    valid_down = r_down[~np.isnan(r_down)]
    assert ((valid_up >= 0) & (valid_up <= 100)).all()
    assert valid_up[-1] > 70  # steadily rising -> overbought
    assert valid_down[-1] < 30  # steadily falling -> oversold


def test_macd_histogram_equals_macd_minus_signal():
    closes = _trending_closes(100, start=100.0, step=0.3)
    result = macd(closes, fast=12, slow=26, signal=9)
    valid = ~np.isnan(result.macd) & ~np.isnan(result.signal)
    assert np.allclose(
        result.histogram[valid], result.macd[valid] - result.signal[valid]
    )


def test_stochastic_bounds():
    closes = _trending_closes(60, start=100.0, step=0.5)
    highs = closes + 1
    lows = closes - 1
    k, d = stochastic(highs, lows, closes, k=14, d=3)
    valid_k = k[~np.isnan(k)]
    assert ((valid_k >= 0) & (valid_k <= 100)).all()


# -- Volatility ---------------------------------------------------------

def test_bollinger_bands_ordering():
    closes = _trending_closes(60, start=100.0, step=0.2)
    bands = bollinger_bands(closes, period=20, std_dev=2.0)
    valid = ~np.isnan(bands.upper)
    assert (bands.upper[valid] >= bands.middle[valid]).all()
    assert (bands.middle[valid] >= bands.lower[valid]).all()


def test_atr_is_non_negative():
    closes = _trending_closes(40, start=100.0, step=0.4)
    highs = closes + 0.5
    lows = closes - 0.5
    values = atr(highs, lows, closes, period=14)
    valid = values[~np.isnan(values)]
    assert (valid >= 0).all()


# -- Volume ---------------------------------------------------------

def test_volume_avg_matches_manual_average():
    volumes = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
    result = volume_avg(volumes, period=3)
    assert result[2] == pytest.approx(20.0)
    assert result[4] == pytest.approx(40.0)


def test_obv_rises_on_up_days_falls_on_down_days():
    closes = np.array([100.0, 101.0, 100.5, 102.0])
    volumes = np.array([1000.0, 1000.0, 1000.0, 1000.0])
    result = obv(closes, volumes)
    # day 1 up -> +1000, day 2 down -> -1000, day 3 up -> +1000
    assert result[1] == pytest.approx(1000.0)
    assert result[2] == pytest.approx(0.0)
    assert result[3] == pytest.approx(1000.0)


# -- Fibonacci ---------------------------------------------------------

def test_fib_retracement_endpoints_and_midpoint():
    levels = fib_retracement(swing_high=100.0, swing_low=90.0, levels=[0.0, 0.5, 1.0])
    assert levels["0.0"] == pytest.approx(100.0)
    assert levels["1.0"] == pytest.approx(90.0)
    assert levels["0.5"] == pytest.approx(95.0)


# -- Support/Resistance (KEY algorithm) ---------------------------------------------------------

def test_detect_support_resistance_finds_repeated_levels():
    # Triangle wave bouncing between 90 (support) and 100 (resistance),
    # touched multiple times, so both survive the min_touches filter.
    cycle = [90.0, 92.0, 94.0, 96.0, 98.0, 100.0, 98.0, 96.0, 94.0, 92.0]
    prices = cycle * 6  # 60 candles, 6 full swings
    candles = _candles_from_prices(prices)

    result = detect_support_resistance(
        candles, lookback=60, min_touches=2, top_n=5, swing_window=2
    )

    assert result.resistance_levels, "expected at least one resistance level"
    assert result.support_levels, "expected at least one support level"
    assert max(result.resistance_levels) == pytest.approx(100.0, abs=0.01)
    assert min(result.support_levels) == pytest.approx(90.0, abs=0.01)
    # Fib levels computed from the 100 -> 90 swing.
    assert result.fib_levels["0.5"] == pytest.approx(95.0, abs=0.01)


def test_detect_support_resistance_empty_input():
    result = detect_support_resistance([], lookback=50)
    assert result.support_levels == []
    assert result.resistance_levels == []
    assert result.fib_levels == {}


def test_detect_support_resistance_filters_single_touch_levels():
    # Strictly monotonic ramp: every point is unique, no level is ever
    # touched twice, so nothing should survive min_touches=2.
    prices = list(np.linspace(100.0, 150.0, 40))
    candles = _candles_from_prices(prices)
    result = detect_support_resistance(candles, lookback=40, min_touches=2)
    assert result.support_levels == []
    assert result.resistance_levels == []


def test_detect_support_resistance_levels_are_plain_python_floats():
    # Regression: indexing a numpy array yields np.float64, which silently
    # propagates into Signal.entry/sl/tp and BacktestTrade.pnl_pct downstream
    # until FastAPI/pydantic's JSON serialization rejects the resulting
    # np.bool_/np.float64 outright (found via the web dashboard's
    # /api/backtest endpoint — see support_resistance.py's comment).
    cycle = [90.0, 92.0, 94.0, 96.0, 98.0, 100.0, 98.0, 96.0, 94.0, 92.0]
    candles = _candles_from_prices(cycle * 6)
    result = detect_support_resistance(candles, lookback=60, min_touches=2)

    for level in result.support_levels + result.resistance_levels:
        assert type(level) is float
    for level in result.fib_levels.values():
        assert type(level) is float


# -- Indicator manager (orchestrator) ---------------------------------------------------------

def test_calculate_all_indicators_assembles_snapshot():
    prices = list(_trending_closes(250, start=100.0, step=0.2))
    candles = _candles_from_prices(prices)

    snapshot = calculate_all_indicators(candles)

    assert snapshot.symbol == "TEST"
    assert snapshot.timeframe == "1h"
    assert snapshot.close == pytest.approx(prices[-1])
    assert 0 <= snapshot.rsi <= 100
    assert snapshot.ema_20 > 0
    assert snapshot.ema_200 > 0
    assert snapshot.sr_levels.symbol == "TEST"


def test_calculate_all_indicators_requires_candles():
    with pytest.raises(ValueError):
        calculate_all_indicators([])
