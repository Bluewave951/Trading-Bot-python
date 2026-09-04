"""Main backtester (KEY).

Replays historical OHLC data, applies the exact same entry/exit logic as the
live bot (`src.signals.entry_signals`), and records trades for
`src.backtesting.backtest_stats` to summarize.

Long-only: `entry_signals.generate_buy_signal` is the only way to open a
position; `entry_signals.should_exit_long` (the plan's SELL confluence,
repurposed) can close one early, alongside the usual SL/TP. See
`entry_signals`'s module docstring for why there's no independent short
entry — backtesting an "open a short on SELL" version lost money in
aggregate (see the "Entry/exit tuning" note in `trading-bot/README.md`).

Scope note: this replays a single symbol/timeframe series and doesn't use
the multi-timeframe `signal_aggregator` confluence (that would need several
timeframes' candle series walked in lockstep by timestamp — a further
refinement left for when Phase 7 integration needs it).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from src.config import settings
from src.data.models import Candle, ExitSignal, Signal, SignalSide
from src.indicators.indicator_manager import calculate_all_indicators
from src.signals.entry_signals import generate_buy_signal, should_exit_long


@dataclass
class BacktestTrade:
    """A single simulated round-trip trade."""
    symbol: str
    timeframe: str
    side: SignalSide
    entry: float
    sl: float
    tp: float
    entry_time: datetime
    exit_time: datetime
    exit_price: float
    exit_reason: str  # "tp" | "sl" | "signal_exit" | "end_of_data"
    exit_index: int
    pnl_pct: float
    risk_reward: float
    confidence: float


@dataclass
class BacktestResult:
    symbol: str
    timeframe: str
    trades: list[BacktestTrade] = field(default_factory=list)
    # (timestamp, cumulative equity multiplier) after each closed trade, starting implicitly at 1.0
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)


@dataclass
class _OpenPosition:
    signal: Signal
    entry_index: int
    entry_time: datetime


def run_backtest(
    candles: list[Candle], min_history: int | None = None, window_size: int = 250
) -> BacktestResult:
    """Replay `candles` (chronological, oldest first) through the live signal
    logic and record every trade taken.

    Only one position is open at a time — while a trade is open, new BUY
    signals are ignored (matches a single-position-per-symbol live bot).
    Entry is filled at the signal's snapshot close (the candle whose
    indicators triggered it); this is a simplification versus filling at
    the *next* candle's open, which would remove a fraction-of-a-bar
    look-ahead but adds complexity not needed for a first validation pass.
    Each open bar is checked for SL/TP first, then `should_exit_long` as a
    softer fallback — a precise price level takes priority over a
    confluence-based exit trigger.

    `min_history` (default `settings.indicators.sr_lookback_candles`)
    candles are skipped up front as an indicator warm-up window.

    `window_size` bounds how much history each indicator snapshot sees
    (the most recent `window_size` candles), matching how the live bot
    actually operates — `DataFetcher.fetch_ohlcv` pulls a bounded number of
    recent candles too, never "all history". This also keeps a long
    backtest at O(n * window_size) instead of O(n^2).
    """
    if min_history is None:
        min_history = settings.indicators.sr_lookback_candles

    trades: list[BacktestTrade] = []
    equity_curve: list[tuple[datetime, float]] = []
    cumulative_equity = 1.0
    position: _OpenPosition | None = None

    i = min_history
    n = len(candles)
    while i < n:
        window = candles[max(0, i + 1 - window_size) : i + 1]
        snapshot = calculate_all_indicators(window)
        candle = candles[i]

        if position is None:
            signal = generate_buy_signal(snapshot)
            if signal is not None:
                position = _OpenPosition(signal=signal, entry_index=i, entry_time=candle.timestamp)
            i += 1
            continue

        trade = _check_exit(position, candle, exit_index=i, exit_signal=should_exit_long(snapshot))
        if trade is not None:
            trades.append(trade)
            cumulative_equity *= 1 + trade.pnl_pct
            equity_curve.append((trade.exit_time, cumulative_equity))
            position = None
        i += 1

    if position is not None:
        last_index = n - 1
        last = candles[last_index]
        trade = _close_trade(
            position, exit_time=last.timestamp, exit_price=last.close,
            exit_reason="end_of_data", exit_index=last_index,
        )
        trades.append(trade)
        cumulative_equity *= 1 + trade.pnl_pct
        equity_curve.append((trade.exit_time, cumulative_equity))

    symbol = candles[-1].symbol if candles else ""
    timeframe = candles[-1].timeframe if candles else ""
    return BacktestResult(
        symbol=symbol, timeframe=timeframe, trades=trades, equity_curve=equity_curve
    )


def _check_exit(
    position: _OpenPosition, candle: Candle, exit_index: int, exit_signal: ExitSignal | None
) -> BacktestTrade | None:
    """Close `position` if this candle hits SL/TP or `should_exit_long` fired."""
    signal = position.signal
    hit_sl = candle.low <= signal.sl
    hit_tp = candle.high >= signal.tp

    if hit_sl or hit_tp:
        # If a single candle's range spans both levels we can't know which
        # was touched first intrabar — conservatively assume SL (worst case).
        exit_price, exit_reason = (signal.sl, "sl") if hit_sl else (signal.tp, "tp")
        return _close_trade(position, candle.timestamp, exit_price, exit_reason, exit_index)

    if exit_signal is not None:
        return _close_trade(position, candle.timestamp, candle.close, "signal_exit", exit_index)

    return None


def _close_trade(
    position: _OpenPosition, exit_time: datetime, exit_price: float, exit_reason: str, exit_index: int
) -> BacktestTrade:
    signal = position.signal
    pnl_pct = (exit_price - signal.entry) / signal.entry  # long-only, see module docstring
    return BacktestTrade(
        symbol=signal.symbol,
        timeframe=signal.timeframe,
        side=signal.side,
        entry=signal.entry,
        sl=signal.sl,
        tp=signal.tp,
        entry_time=position.entry_time,
        exit_time=exit_time,
        exit_price=exit_price,
        exit_reason=exit_reason,
        exit_index=exit_index,
        pnl_pct=pnl_pct,
        risk_reward=signal.risk_reward,
        confidence=signal.confidence,
    )
