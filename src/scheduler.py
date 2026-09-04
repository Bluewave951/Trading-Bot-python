"""Main scheduler loop (KEY).

APScheduler running in a background thread:
  - Scan every `scheduler.scan_interval_1h_seconds` (default 60s = 1 min,
    per plan) for 1h-timeframe signals
  - Scan every `scheduler.scan_interval_4h_seconds` (default 300s) for 4h
  - Scan at `scheduler.scan_1d_at_utc` (default 00:00 UTC) for 1d
On signal: persist to DB, then dispatch alerts via `NotificationQueue`.

See TRADING_BOT_PLAN.md section "8. Main Scheduler".
"""
from __future__ import annotations

import time
from datetime import timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import load_watchlist, settings
from src.data.data_fetcher import DataFetcher
from src.data.database import Database
from src.data.models import Signal, Symbol
from src.indicators.indicator_manager import calculate_all_indicators
from src.logger import get_logger
from src.notifications.discord_bot import DiscordNotifier
from src.notifications.email_service import EmailNotifier
from src.notifications.formatting import format_signal_text
from src.notifications.notification_queue import NotificationQueue
from src.notifications.telegram_bot import TelegramNotifier
from src.signals.entry_signals import generate_buy_signal

logger = get_logger(__name__)

_MIN_CANDLES_FOR_SCAN = 250  # enough for EMA200 + the S/R lookback window


def default_notification_queue() -> NotificationQueue:
    """A queue with every channel registered; each is only actually used if
    its `enabled` property is true (config toggle + secrets present)."""
    queue = NotificationQueue()
    queue.register(DiscordNotifier())
    queue.register(TelegramNotifier())
    queue.register(EmailNotifier())
    return queue


def scan_symbol_timeframe(
    fetcher: DataFetcher, db: Database, notification_queue: NotificationQueue,
    symbol: Symbol, timeframe: str,
) -> Signal | None:
    """Fetch latest candles, compute indicators, check for a BUY signal, and
    if one fires, persist it and dispatch alerts. Returns the signal (or
    `None`) so callers can log/count/test without re-deriving it.
    """
    candles = fetcher.fetch_ohlcv(symbol, timeframe, limit=_MIN_CANDLES_FOR_SCAN)
    if len(candles) < settings.indicators.sr_lookback_candles:
        logger.debug(
            "Skipping %s (%s): only %d candles, need >= %d",
            symbol.ticker, timeframe, len(candles), settings.indicators.sr_lookback_candles,
        )
        return None

    snapshot = calculate_all_indicators(candles)
    signal = generate_buy_signal(snapshot)
    if signal is None:
        return None

    logger.info(
        "Signal: BUY %s (%s) entry=%.4f sl=%.4f tp=%.4f rr=%.2f",
        symbol.ticker, timeframe, signal.entry, signal.sl, signal.tp, signal.risk_reward,
    )
    db.insert_signal(signal)
    notification_queue.dispatch(format_signal_text(signal))
    return signal


def scan_watchlist(
    fetcher: DataFetcher, db: Database, notification_queue: NotificationQueue, timeframe: str,
) -> list[Signal]:
    """Scan every symbol in the watchlist for `timeframe`. A failure on one
    symbol (bad ticker, API hiccup) is logged and skipped rather than
    aborting the whole scan — matches `DataFetcher`'s own fail-soft design.
    """
    signals: list[Signal] = []
    for symbol in load_watchlist():
        try:
            signal = scan_symbol_timeframe(fetcher, db, notification_queue, symbol, timeframe)
        except Exception:
            logger.exception("Error scanning %s (%s)", symbol.ticker, timeframe)
            continue
        if signal is not None:
            signals.append(signal)
    return signals


def build_scheduler(
    fetcher: DataFetcher | None = None,
    db: Database | None = None,
    notification_queue: NotificationQueue | None = None,
) -> BackgroundScheduler:
    """Assemble (but don't start) the three scan jobs described in the
    module docstring."""
    fetcher = fetcher or DataFetcher()
    db = db or Database()
    notification_queue = notification_queue or default_notification_queue()
    cfg = settings.scheduler

    scheduler = BackgroundScheduler(timezone=timezone.utc)
    scheduler.add_job(
        lambda: scan_watchlist(fetcher, db, notification_queue, "1h"),
        "interval", seconds=cfg.scan_interval_1h_seconds, id="scan_1h",
    )
    scheduler.add_job(
        lambda: scan_watchlist(fetcher, db, notification_queue, "4h"),
        "interval", seconds=cfg.scan_interval_4h_seconds, id="scan_4h",
    )
    hour, minute = (int(part) for part in cfg.scan_1d_at_utc.split(":"))
    scheduler.add_job(
        lambda: scan_watchlist(fetcher, db, notification_queue, "1d"),
        CronTrigger(hour=hour, minute=minute, timezone=timezone.utc), id="scan_1d",
    )
    return scheduler


def run_scheduler() -> None:
    """Entry point: `python -m src.scheduler`. Blocks until interrupted
    (Ctrl+C), running the three scan jobs on their configured cadence."""
    cfg = settings.scheduler
    scheduler = build_scheduler()
    scheduler.start()
    logger.info(
        "Scheduler started - 1h every %ds, 4h every %ds, 1d at %s UTC",
        cfg.scan_interval_1h_seconds, cfg.scan_interval_4h_seconds, cfg.scan_1d_at_utc,
    )
    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down scheduler")
        scheduler.shutdown()


if __name__ == "__main__":
    from src.logger import setup_logging

    setup_logging()
    run_scheduler()
