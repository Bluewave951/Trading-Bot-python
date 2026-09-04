"""Multi-timeframe confluence aggregation.

Generates a BUY signal independently on each timeframe, then only returns
the primary (entry) timeframe's signal if a majority of the other
timeframes also agree — this is the "multi-timeframe confluence" step
called out in TRADING_BOT_PLAN.md section 3.

Only BUY entries are aggregated here (see `entry_signals`'s module
docstring for why there's no standalone short entry to confluence-check
against). Exit timing (`should_exit_long`) is intentionally checked on a
single timeframe per bar, not aggregated — an exit is meant to be more
reactive than an entry, so waiting for multi-timeframe agreement to close
a position would just widen losses.
"""
from __future__ import annotations

from src.data.models import IndicatorSnapshot, Signal
from src.signals.entry_signals import generate_buy_signal


def aggregate_signals(snapshots: dict[str, IndicatorSnapshot]) -> Signal | None:
    """Combine per-timeframe BUY signals into one confluence-checked signal.

    `snapshots` maps timeframe -> IndicatorSnapshot (e.g.
    `{"1h": ..., "4h": ..., "1d": ...}`); the *first* key is treated as the
    primary/entry timeframe. Returns `None` unless:
      1. The primary timeframe itself produced a buy signal, and
      2. At least half of all timeframes (including the primary) also did.
    On success, the primary signal's confidence is nudged up and a
    "confluence:<timeframes>" reason is appended.
    """
    if not snapshots:
        return None

    per_tf_signal: dict[str, Signal | None] = {
        tf: generate_buy_signal(snap) for tf, snap in snapshots.items()
    }

    primary_tf = next(iter(snapshots))
    primary_signal = per_tf_signal[primary_tf]
    if primary_signal is None:
        return None

    agreeing_tfs = [tf for tf, sig in per_tf_signal.items() if sig is not None]
    if len(agreeing_tfs) < len(snapshots) / 2:
        return None

    extra_confirmations = len(agreeing_tfs) - 1
    if extra_confirmations > 0:
        primary_signal.confidence = min(1.0, primary_signal.confidence + 0.1 * extra_confirmations)
        primary_signal.reasons.append(f"confluence:{','.join(agreeing_tfs)}")

    return primary_signal
