"""Notification System — Phase 5.

See TRADING_BOT_PLAN.md section "6. Notification System".
`formatting.py` has the (network-free, unit-tested) message text builders;
`telegram_bot.TelegramNotifier` / `discord_bot.DiscordNotifier` /
`email_service.EmailNotifier` each implement `send_text(str) -> bool` +
`enabled`; `notification_queue.NotificationQueue` fans a message out to
whichever of those are registered, with per-channel retry/backoff.
`telegram_bot.build_application()`/`run_bot()` wire up the interactive
`/status /analyze /backtest /settings /chart` commands.
"""
