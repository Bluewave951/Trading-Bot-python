"""Generate HTML/CSV backtest reports + equity curve chart.

No plotting library is in `requirements.txt` (per the lightweight,
dependency-light style of the rest of the data layer), so the equity curve
is rendered as a small self-contained inline SVG polyline instead of a
matplotlib/plotly figure.
"""
from __future__ import annotations

import csv
from pathlib import Path

from src.backtesting.backtest_engine import BacktestResult
from src.backtesting.backtest_stats import BacktestStats, ValidationResult


def export_trades_csv(result: BacktestResult, path: str | Path) -> None:
    """Write one row per trade to `path`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "symbol", "timeframe", "side", "entry", "sl", "tp",
        "entry_time", "exit_time", "exit_price", "exit_reason",
        "pnl_pct", "risk_reward", "confidence",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in result.trades:
            writer.writerow(
                {
                    "symbol": t.symbol,
                    "timeframe": t.timeframe,
                    "side": t.side.value,
                    "entry": t.entry,
                    "sl": t.sl,
                    "tp": t.tp,
                    "entry_time": t.entry_time.isoformat(),
                    "exit_time": t.exit_time.isoformat(),
                    "exit_price": t.exit_price,
                    "exit_reason": t.exit_reason,
                    "pnl_pct": t.pnl_pct,
                    "risk_reward": t.risk_reward,
                    "confidence": t.confidence,
                }
            )


def _equity_curve_svg(
    equity_curve: list[tuple[object, float]], width: int = 640, height: int = 200
) -> str:
    """A minimal inline SVG line chart of cumulative equity, starting at 1.0."""
    values = [1.0] + [v for _ts, v in equity_curve]
    if len(values) < 2:
        return "<p>Not enough trades to plot an equity curve.</p>"

    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    pad = 10
    plot_w, plot_h = width - 2 * pad, height - 2 * pad

    def point(i: int, v: float) -> tuple[float, float]:
        x = pad + (i / (len(values) - 1)) * plot_w
        y = pad + (1 - (v - lo) / span) * plot_h
        return x, y

    points = " ".join(f"{x:.1f},{y:.1f}" for x, y in (point(i, v) for i, v in enumerate(values)))
    stroke = "#2ecc71" if values[-1] >= values[0] else "#e74c3c"
    return (
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'<polyline fill="none" stroke="{stroke}" stroke-width="2" points="{points}" />'
        f'<line x1="{pad}" y1="{point(0, 1.0)[1]:.1f}" x2="{width - pad}" y2="{point(0, 1.0)[1]:.1f}" '
        f'stroke="#999" stroke-dasharray="4,4" />'
        f"</svg>"
    )


def generate_html_report(
    result: BacktestResult,
    stats: BacktestStats,
    validation: ValidationResult,
    path: str | Path,
) -> None:
    """Write a single self-contained HTML report: summary stats table,
    pass/fail checklist, equity curve, and full trade log.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    verdict = "PASSED" if validation.passed else "FAILED"
    verdict_color = "#2ecc71" if validation.passed else "#e74c3c"

    checks_rows = "".join(
        f"<tr><td>{name}</td><td>{'✅' if ok else '❌'}</td></tr>"
        for name, ok in validation.checks.items()
    )

    def _trade_row(t) -> str:
        color = "#2ecc71" if t.pnl_pct > 0 else "#e74c3c"
        return (
            f"<tr><td>{t.entry_time:%Y-%m-%d %H:%M}</td><td>{t.side.value}</td>"
            f"<td>{t.entry:.4f}</td><td>{t.sl:.4f}</td><td>{t.tp:.4f}</td>"
            f"<td>{t.exit_price:.4f}</td><td>{t.exit_reason}</td>"
            f"<td style='color:{color}'>{t.pnl_pct:+.2%}</td></tr>"
        )

    trade_rows = "".join(_trade_row(t) for t in result.trades)

    html = f"""<title>Backtest Report: {result.symbol} {result.timeframe}</title>
<style>
  body {{ font-family: sans-serif; max-width: 900px; margin: 2rem auto; color: #222; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
  th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; font-size: 0.9rem; }}
  th {{ background: #f5f5f5; }}
  .verdict {{ font-size: 1.5rem; font-weight: bold; color: {verdict_color}; }}
</style>
<h1>Backtest Report — {result.symbol} ({result.timeframe})</h1>
<p class="verdict">{verdict}</p>

<h2>Summary</h2>
<table>
  <tr><th>Total Trades</th><td>{stats.total_trades}</td></tr>
  <tr><th>Win Rate</th><td>{stats.win_rate:.1%}</td></tr>
  <tr><th>Profit Factor</th><td>{stats.profit_factor:.2f}</td></tr>
  <tr><th>Max Drawdown</th><td>{stats.max_drawdown_pct:.1%}</td></tr>
  <tr><th>Total Return</th><td>{stats.total_return_pct:+.1%}</td></tr>
  <tr><th>Avg Win</th><td>{stats.avg_win_pct:+.2%}</td></tr>
  <tr><th>Avg Loss</th><td>{stats.avg_loss_pct:+.2%}</td></tr>
  <tr><th>Sharpe (per-trade)</th><td>{stats.sharpe_ratio:.2f}</td></tr>
</table>

<h2>Validation Checklist</h2>
<table>{checks_rows}</table>

<h2>Equity Curve</h2>
{_equity_curve_svg(result.equity_curve)}

<h2>Trade Log ({len(result.trades)})</h2>
<table>
  <tr><th>Entry Time</th><th>Side</th><th>Entry</th><th>SL</th><th>TP</th>
      <th>Exit Price</th><th>Exit Reason</th><th>P&amp;L</th></tr>
  {trade_rows}
</table>
"""
    path.write_text(html, encoding="utf-8")
