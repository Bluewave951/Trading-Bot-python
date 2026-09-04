"""/api/backtest endpoints."""
from __future__ import annotations

import math

from fastapi import APIRouter, HTTPException, Query

from src.backtesting.backtest_engine import run_backtest
from src.backtesting.backtest_stats import calculate_stats, validate_strategy
from src.data.data_fetcher import DataFetcher
from src.data.models import AssetClass, Symbol

router = APIRouter()


def _resolve_symbol(ticker: str) -> Symbol:
    """`BTC/USDT`-shaped tickers route to Binance/crypto; anything else is
    treated as a yfinance stock ticker (same rule as `telegram_bot.py`)."""
    ticker = ticker.upper()
    if "/" in ticker:
        return Symbol(ticker=ticker, asset_class=AssetClass.CRYPTO, source="binance")
    return Symbol(ticker=ticker, asset_class=AssetClass.STOCK, source="yfinance")


def _json_safe_float(value: float) -> float | None:
    """`profit_factor` can be `inf` (no losing trades) — plain `json.dumps`
    would emit the literal token `Infinity`, which `JSON.parse` in a
    browser rejects. Send `null` instead; the frontend renders it as "∞"."""
    return None if math.isinf(value) or math.isnan(value) else value


@router.get("/api/backtest")
def run_backtest_api(
    symbol: str = Query(..., description="e.g. AAPL or BTC/USDT"),
    timeframe: str = Query("1h"),
    limit: int = Query(1000, ge=100, le=10000, description="candles of history to fetch"),
) -> dict:
    sym = _resolve_symbol(symbol)
    candles = DataFetcher().fetch_ohlcv(sym, timeframe, limit=limit)
    if len(candles) < 100:
        raise HTTPException(
            status_code=400, detail=f"Not enough data for {symbol} ({timeframe}): only {len(candles)} candles"
        )

    result = run_backtest(candles)
    stats = calculate_stats(result)
    validation = validate_strategy(stats)

    return {
        "symbol": result.symbol,
        "timeframe": result.timeframe,
        "candles_used": len(candles),
        "stats": {
            "total_trades": stats.total_trades,
            "win_rate": stats.win_rate,
            "profit_factor": _json_safe_float(stats.profit_factor),
            "max_drawdown_pct": stats.max_drawdown_pct,
            "total_return_pct": stats.total_return_pct,
            "avg_win_pct": stats.avg_win_pct,
            "avg_loss_pct": stats.avg_loss_pct,
            "sharpe_ratio": stats.sharpe_ratio,
        },
        "validation": {"passed": validation.passed, "checks": validation.checks},
        "equity_curve": [
            {"timestamp": ts.isoformat(), "equity": equity} for ts, equity in result.equity_curve
        ],
        "trades": [
            {
                "side": t.side.value,
                "entry": t.entry,
                "sl": t.sl,
                "tp": t.tp,
                "entry_time": t.entry_time.isoformat(),
                "exit_time": t.exit_time.isoformat(),
                "exit_price": t.exit_price,
                "exit_reason": t.exit_reason,
                "pnl_pct": t.pnl_pct,
            }
            for t in result.trades
        ],
    }
