"""Buy/Sell signal logic (KEY BUSINESS LOGIC).

See TRADING_BOT_PLAN.md "Key Algorithms > B. Entry Signal Logic":

    def generate_buy_signal(symbol: str, timeframe: str) -> Optional[Signal]:
        # ALL must be true:
        #   price_near_support
        #   rsi < 50
        #   volume > volume_avg_20
        #   ema_20 > ema_50 (uptrend)
        #   macd_histogram > 0
        #   close > ema_200

    SELL when price touches resistance OR RSI > 70.

Design deviation from the plan, based on backtesting (see the "Entry/exit
tuning" note in `trading-bot/README.md`): the plan's SELL condition opens a
new short. Backtested as an independent short entry across a full year of
1h data (7 symbols, 721 trades), it lost money in aggregate (40.9% win
rate, profit factor 0.95) — fading strength into resistance/overbought RSI
has no edge in the broadly upward-biased regimes (crypto, US equities)
this bot targets. SELL is instead used as `should_exit_long()`: a trigger
to close an already-open BUY position early, before its own SL/TP is hit.
There is no independent short-entry function anymore.
"""
from __future__ import annotations

import math

from src.data.models import ExitSignal, IndicatorSnapshot, Signal, SignalSide
from src.signals.exit_signals import calculate_exit_levels

# How close price has to be to a level (as a fraction of the level's price)
# to count as "near" it. Tuned down from an initial 0.01 (1%): with a
# 50-candle S/R lookback, 1% proximity was true on ~50% of all bars for
# BTC/USDT 1h — nowhere near a real level touch, just noise.
_LEVEL_PROXIMITY_PCT = 0.005


def _price_near_support(close: float, support_levels: list[float]) -> bool:
    return any(
        support <= close <= support * (1 + _LEVEL_PROXIMITY_PCT)
        for support in support_levels
    )


def _price_near_resistance(close: float, resistance_levels: list[float]) -> bool:
    return any(
        resistance * (1 - _LEVEL_PROXIMITY_PCT) <= close <= resistance
        for resistance in resistance_levels
    )


def _macd_turning_up(snapshot: IndicatorSnapshot) -> bool:
    """Bullish momentum: histogram already positive, or rising vs the prior
    bar (catches the turn a bar earlier than waiting for the zero-cross)."""
    if snapshot.macd_histogram > 0:
        return True
    if math.isnan(snapshot.macd_histogram_prev):
        return False
    return snapshot.macd_histogram > snapshot.macd_histogram_prev


def _macd_turning_down(snapshot: IndicatorSnapshot) -> bool:
    """Mirror of `_macd_turning_up` for the sell side."""
    if snapshot.macd_histogram < 0:
        return True
    if math.isnan(snapshot.macd_histogram_prev):
        return False
    return snapshot.macd_histogram < snapshot.macd_histogram_prev


def generate_buy_signal(snapshot: IndicatorSnapshot) -> Signal | None:
    """Confluence-based buy signal. Returns `None` unless ALL conditions hold."""
    sr = snapshot.sr_levels
    checks = {
        "price_near_support": _price_near_support(snapshot.close, sr.support_levels),
        "rsi<50": snapshot.rsi < 50,
        "volume>avg20": snapshot.volume > snapshot.volume_avg_20,
        "ema20>ema50": snapshot.ema_20 > snapshot.ema_50,
        "macd_turning_up": _macd_turning_up(snapshot),
        "close>ema200": snapshot.close > snapshot.ema_200,
    }
    if not all(checks.values()):
        return None

    entry = snapshot.close
    sl, tp = calculate_exit_levels(entry, sr, side="buy")
    risk = entry - sl
    reward = tp - entry
    if risk <= 0 or reward <= 0:
        return None

    return Signal(
        symbol=snapshot.symbol,
        timeframe=snapshot.timeframe,
        side=SignalSide.BUY,
        entry=entry,
        sl=sl,
        tp=tp,
        risk_reward=reward / risk,
        confidence=sum(checks.values()) / len(checks),  # all True here == 1.0
        reasons=[name for name, passed in checks.items() if passed],
    )


def _sell_conditions_met(snapshot: IndicatorSnapshot) -> tuple[bool, list[str]]:
    """The plan's SELL confluence: near resistance OR RSI>70, confirmed by
    ALL THREE bearish-momentum checks (EMA20<EMA50, MACD turning down,
    close<EMA200) — full mirror of how strict BUY is. Backtesting showed
    this matters: an "any 1 of 3" version fired constantly with confidence
    uncorrelated to outcome; even "majority (2 of 3)" still split cleanly
    by confirmation count (2-of-3 won ~31% of the time vs ~49% for 3-of-3).

    Shared by `should_exit_long()`; there is no standalone short-entry
    function using this — see the module docstring for why.
    """
    sr = snapshot.sr_levels
    triggered = _price_near_resistance(snapshot.close, sr.resistance_levels) or snapshot.rsi > 70
    if not triggered:
        return False, []

    confirmations = {
        "ema20<ema50": snapshot.ema_20 < snapshot.ema_50,
        "macd_turning_down": _macd_turning_down(snapshot),
        "close<ema200": snapshot.close < snapshot.ema_200,
    }
    if not all(confirmations.values()):
        return False, []

    reasons = ["price_near_resistance" if _price_near_resistance(snapshot.close, sr.resistance_levels) else "rsi>70"]
    reasons += [name for name, passed in confirmations.items() if passed]
    return True, reasons


def should_exit_long(snapshot: IndicatorSnapshot) -> ExitSignal | None:
    """Trigger to close an already-open long position early, before its
    own SL/TP is hit — the plan's SELL confluence, repurposed as an exit
    rather than a new short entry (see module docstring)."""
    triggered, reasons = _sell_conditions_met(snapshot)
    if not triggered:
        return None
    return ExitSignal(reasons=reasons, confidence=1.0)
