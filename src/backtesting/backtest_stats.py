"""Win rate, profit factor, Sharpe ratio, max drawdown.

    profit_factor = sum(winning trade P&L) / sum(losing trade P&L)

See TRADING_BOT_PLAN.md "Testing & Validation Strategy" for the thresholds
`validate_strategy()` checks against.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.backtesting.backtest_engine import BacktestResult


@dataclass
class BacktestStats:
    total_trades: int
    win_rate: float          # fraction of trades with pnl_pct > 0
    profit_factor: float     # gross profit / gross loss; inf if no losses
    max_drawdown_pct: float  # largest peak-to-trough drop in cumulative equity
    total_return_pct: float
    avg_win_pct: float
    avg_loss_pct: float
    sharpe_ratio: float      # per-trade mean/stdev of pnl_pct, annualization left to the caller


_EMPTY_STATS = BacktestStats(
    total_trades=0,
    win_rate=0.0,
    profit_factor=0.0,
    max_drawdown_pct=0.0,
    total_return_pct=0.0,
    avg_win_pct=0.0,
    avg_loss_pct=0.0,
    sharpe_ratio=0.0,
)


def calculate_stats(result: BacktestResult) -> BacktestStats:
    """Summarize a `BacktestResult`'s trades into headline performance stats."""
    pnls = [t.pnl_pct for t in result.trades]
    if not pnls:
        return _EMPTY_STATS

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = float("inf") if gross_profit > 0 else 0.0

    equity = [1.0]
    for p in pnls:
        equity.append(equity[-1] * (1 + p))

    return BacktestStats(
        total_trades=len(pnls),
        win_rate=len(wins) / len(pnls),
        profit_factor=profit_factor,
        max_drawdown_pct=_max_drawdown(equity),
        total_return_pct=equity[-1] - 1.0,
        avg_win_pct=(gross_profit / len(wins)) if wins else 0.0,
        avg_loss_pct=(sum(losses) / len(losses)) if losses else 0.0,
        sharpe_ratio=_sharpe_ratio(pnls),
    )


def _max_drawdown(equity: list[float]) -> float:
    """Largest fractional drop from a running peak, e.g. 0.20 = -20%."""
    peak = equity[0]
    max_dd = 0.0
    for value in equity:
        peak = max(peak, value)
        max_dd = max(max_dd, (peak - value) / peak)
    return max_dd


def _sharpe_ratio(returns: list[float], risk_free_rate: float = 0.0) -> float:
    """Mean/stdev of per-trade returns (not annualized — trades aren't
    evenly spaced in time, so annualizing would need trade-duration
    weighting the caller is better positioned to supply)."""
    if len(returns) < 2:
        return 0.0
    excess = np.array(returns) - risk_free_rate
    std = excess.std(ddof=1)
    if std == 0:
        return 0.0
    return float(excess.mean() / std)


@dataclass
class ValidationResult:
    passed: bool
    checks: dict[str, bool]


def validate_strategy(
    stats: BacktestStats,
    min_win_rate: float = 0.5,
    min_profit_factor: float = 1.5,
    max_drawdown_pct: float = 0.20,
) -> ValidationResult:
    """Pass/fail against the plan's pre-live checklist:
    win rate >= 50%, profit factor >= 1.5, max drawdown <= 20%.
    """
    checks = {
        f"win_rate>={min_win_rate:.0%}": stats.win_rate >= min_win_rate,
        f"profit_factor>={min_profit_factor}": stats.profit_factor >= min_profit_factor,
        f"max_drawdown<={max_drawdown_pct:.0%}": stats.max_drawdown_pct <= max_drawdown_pct,
    }
    return ValidationResult(passed=all(checks.values()), checks=checks)
