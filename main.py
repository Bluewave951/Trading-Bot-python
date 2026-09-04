"""Entry point.

Runs one full pass of the pipeline across the watchlist and every
configured timeframe: fetch OHLCV -> compute indicators -> check for a BUY
signal -> persist to SQLite + dispatch alerts on a hit. This is
`src.scheduler.scan_watchlist()` called once per timeframe rather than on
a repeating schedule — for continuous operation run:

    python -m src.scheduler
"""
from __future__ import annotations

from src.config import settings
from src.data.data_fetcher import DataFetcher
from src.data.database import Database
from src.logger import get_logger, setup_logging
from src.scheduler import default_notification_queue, scan_watchlist


def run_once() -> None:
    logger = get_logger(__name__)
    fetcher = DataFetcher()
    db = Database()
    notification_queue = default_notification_queue()

    watchlist_size = len(settings.symbols)
    logger.info(
        "Scanning %d asset class(es) across timeframes: %s",
        watchlist_size, ", ".join(settings.timeframes),
    )

    total_signals = 0
    for timeframe in settings.timeframes:
        signals = scan_watchlist(fetcher, db, notification_queue, timeframe)
        total_signals += len(signals)
        logger.info(
            "%s: %d BUY signal(s)%s",
            timeframe, len(signals),
            "" if signals else " (none this pass - that's normal, not an error)",
        )

    logger.info("Done - %d signal(s) found across %d timeframe(s).", total_signals, len(settings.timeframes))


if __name__ == "__main__":
    setup_logging()
    run_once()
