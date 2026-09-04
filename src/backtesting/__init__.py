"""Backtesting Engine — Phase 4.

See TRADING_BOT_PLAN.md section "5. Backtesting Engine".
`backtest_engine.run_backtest(candles)` replays a single symbol/timeframe
series through the live signal logic; `backtest_stats.calculate_stats()` +
`validate_strategy()` score the result against the plan's pre-live
checklist; `backtest_reports` writes CSV/HTML output.
"""
