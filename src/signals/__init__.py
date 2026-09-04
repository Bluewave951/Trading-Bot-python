"""Signal Generation Engine — Phase 3.

See TRADING_BOT_PLAN.md section "3. Signal Generation Engine".
`entry_signals.generate_buy_signal(snapshot) -> Signal | None` is the only
way to open a position (long-only — see the module docstring on why the
plan's SELL isn't a standalone short entry here);
`entry_signals.should_exit_long(snapshot) -> ExitSignal | None` triggers
closing one early, before its own SL/TP. `signal_aggregator.aggregate_signals()`
combines BUY signals across timeframes for multi-timeframe confluence.
"""
