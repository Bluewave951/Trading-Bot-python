"""Plain-text message formatting shared by all notification channels.

Kept separate from the channel modules (`telegram_bot.py`, `discord_bot.py`,
`email_service.py`) so the text itself is testable without any network I/O
or channel-specific SDK.
"""
from __future__ import annotations

from src.backtesting.backtest_stats import BacktestStats, ValidationResult
from src.data.models import ExitSignal, IndicatorSnapshot, Signal


def format_signal_text(signal: Signal) -> str:
    """A new BUY entry signal, ready to alert."""
    reasons = ", ".join(signal.reasons) if signal.reasons else "n/a"
    return (
        f"\U0001F7E2 BUY {signal.symbol} ({signal.timeframe})\n"
        f"Entry: {signal.entry:.4f}\n"
        f"SL: {signal.sl:.4f}  TP: {signal.tp:.4f}\n"
        f"Risk/Reward: {signal.risk_reward:.2f}  Confidence: {signal.confidence:.0%}\n"
        f"Reasons: {reasons}"
    )


def format_exit_text(symbol: str, timeframe: str, exit_signal: ExitSignal) -> str:
    """An early-exit trigger for an open position (see `should_exit_long`)."""
    reasons = ", ".join(exit_signal.reasons) if exit_signal.reasons else "n/a"
    return (
        f"\U0001F534 EXIT {symbol} ({timeframe})\n"
        f"Reasons: {reasons}  Confidence: {exit_signal.confidence:.0%}"
    )


def format_analysis_text(snapshot: IndicatorSnapshot) -> str:
    """Manual `/analyze <SYMBOL>` snapshot — current indicator readout."""
    sr = snapshot.sr_levels
    supports = ", ".join(f"{s:.2f}" for s in sr.support_levels) or "none"
    resistances = ", ".join(f"{r:.2f}" for r in sr.resistance_levels) or "none"
    return (
        f"\U0001F4CA {snapshot.symbol} ({snapshot.timeframe})\n"
        f"Close: {snapshot.close:.4f}  RSI: {snapshot.rsi:.1f}\n"
        f"EMA20/50/200: {snapshot.ema_20:.4f} / {snapshot.ema_50:.4f} / {snapshot.ema_200:.4f}\n"
        f"MACD: {snapshot.macd:.4f}  Signal: {snapshot.macd_signal:.4f}  "
        f"Hist: {snapshot.macd_histogram:.4f}\n"
        f"Volume: {snapshot.volume:.1f} (avg20: {snapshot.volume_avg_20:.1f})\n"
        f"Support: {supports}\n"
        f"Resistance: {resistances}"
    )


def format_backtest_text(symbol: str, timeframe: str, stats: BacktestStats, validation: ValidationResult) -> str:
    """`/backtest <SYMBOL>` summary."""
    verdict = "PASSED" if validation.passed else "FAILED"
    checks = "\n".join(f"  {'✅' if ok else '❌'} {name}" for name, ok in validation.checks.items())
    return (
        f"\U0001F4C8 Backtest: {symbol} ({timeframe}) — {verdict}\n"
        f"Trades: {stats.total_trades}  Win rate: {stats.win_rate:.1%}\n"
        f"Profit factor: {stats.profit_factor:.2f}  Max drawdown: {stats.max_drawdown_pct:.1%}\n"
        f"Total return: {stats.total_return_pct:+.1%}\n"
        f"{checks}"
    )


def format_status_text(open_signals: list[Signal]) -> str:
    """`/status` — currently tracked open BUY positions/signals."""
    if not open_signals:
        return "\U0001F4CB No open positions."
    lines = [f"\U0001F4CB {len(open_signals)} open position(s):"]
    for signal in open_signals:
        lines.append(
            f"  {signal.symbol} ({signal.timeframe}) entry={signal.entry:.4f} "
            f"sl={signal.sl:.4f} tp={signal.tp:.4f}"
        )
    return "\n".join(lines)
