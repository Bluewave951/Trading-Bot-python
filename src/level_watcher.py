"""Standalone S/R-proximity watcher.

User-requested ad-hoc monitor (2026-09-04) — NOT part of the
TRADING_BOT_PLAN.md phases and independent of `src/scheduler.py`'s
BUY-only trading strategy. Every `settings.level_watch.check_interval_minutes`
(default 30), fetches each symbol in `config/level_watch.yaml`'s watchlist,
computes support/resistance via the bot's own
`src.indicators.support_resistance` (swing-clustering — a different
algorithm from TradingView/SMC-based analysis, so its levels won't exactly
match a TradingView chart), and alerts Discord + Telegram only when price
is within `proximity_pct` of a level — not every cycle regardless. Every
hit is also persisted to the `level_alerts` DB table (see
`Database.insert_level_alert`) so the web dashboard can chart delivery
stats — see the "Level Watcher Alerts" section of `src/web/`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import timezone

from apscheduler.schedulers.background import BackgroundScheduler

from src.config import load_level_watchlist, settings
from src.data.data_fetcher import DataFetcher
from src.data.database import Database
from src.data.models import Symbol
from src.indicators.indicator_manager import calculate_all_indicators
from src.logger import get_logger
from src.notifications.discord_bot import DiscordNotifier
from src.notifications.notification_queue import NotificationQueue
from src.notifications.telegram_bot import TelegramNotifier

logger = get_logger(__name__)

_MIN_CANDLES = 250  # same floor as src/scheduler.py's scan

_KIND_TH = {"support": "แนวรับ", "resistance": "แนวต้าน"}


@dataclass
class LevelHit:
    """One symbol-near-a-level event, ready to persist to `level_alerts`."""
    kind: str  # "support" | "resistance" — stays English, matches the rest
               # of the codebase's vocabulary; only display text is Thai.
    level: float
    distance_pct: float


@dataclass
class LevelCheckResult:
    price: float
    hits: list[LevelHit]
    message: str  # combined Thai text for the one notification sent


def default_notification_queue() -> NotificationQueue:
    queue = NotificationQueue()
    queue.register(DiscordNotifier())
    queue.register(TelegramNotifier())
    return queue


def _is_near(price: float, level: float, proximity_pct: float) -> bool:
    return abs(price - level) / level <= proximity_pct


def check_symbol(
    fetcher: DataFetcher, symbol: Symbol, timeframe: str, proximity_pct: float
) -> LevelCheckResult | None:
    """Fetch + compute S/R for `symbol`; return a `LevelCheckResult` if price
    is near one or more levels, else `None`. Never raises — callers loop
    over a watchlist and one bad ticker shouldn't abort the rest (same
    fail-soft pattern as `src.scheduler.scan_watchlist`)."""
    candles = fetcher.fetch_ohlcv(symbol, timeframe, limit=_MIN_CANDLES)
    if len(candles) < settings.indicators.sr_lookback_candles:
        logger.debug("Skipping %s: only %d candles", symbol.ticker, len(candles))
        return None

    snapshot = calculate_all_indicators(candles)
    close = snapshot.close
    sr = snapshot.sr_levels

    raw_hits = [("support", level) for level in sr.support_levels if _is_near(close, level, proximity_pct)]
    raw_hits += [("resistance", level) for level in sr.resistance_levels if _is_near(close, level, proximity_pct)]
    if not raw_hits:
        return None

    hits = [LevelHit(kind=k, level=lvl, distance_pct=abs(close - lvl) / lvl) for k, lvl in raw_hits]

    lines = [f"⚠️ {symbol.ticker} ({timeframe}) ราคาปัจจุบัน = {close:.4f}"]
    lines += [f"   ใกล้{_KIND_TH[h.kind]}: {h.level:.4f} (ห่าง {h.distance_pct:.2%})" for h in hits]
    message = "\n".join(lines)

    return LevelCheckResult(price=close, hits=hits, message=message)


def run_check() -> list[str]:
    """One full pass over the watchlist. Returns the alert messages sent,
    for logging/testing — an empty list is the common case, not an error."""
    fetcher = DataFetcher()
    notification_queue = default_notification_queue()
    db = Database()
    cfg = settings.level_watch

    alerts: list[str] = []
    for symbol in load_level_watchlist():
        try:
            result = check_symbol(fetcher, symbol, cfg.timeframe, cfg.proximity_pct)
        except Exception:
            logger.exception("Error checking %s", symbol.ticker)
            continue
        if result is None:
            continue

        logger.info("Level alert:\n%s", result.message)
        notification_queue.dispatch(result.message)
        for hit in result.hits:
            db.insert_level_alert(
                symbol=symbol.ticker, timeframe=cfg.timeframe, kind=hit.kind,
                level=hit.level, price=result.price, distance_pct=hit.distance_pct,
                message=result.message,
            )
        alerts.append(result.message)

    if not alerts:
        logger.info("Level watch pass complete — nothing near a level.")
    return alerts


def build_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=timezone.utc)
    scheduler.add_job(
        run_check, "interval", minutes=settings.level_watch.check_interval_minutes, id="level_watch"
    )
    return scheduler


def run_forever() -> None:
    """Entry point: `python -m src.level_watcher`. Blocks until interrupted."""
    scheduler = build_scheduler()
    scheduler.start()
    logger.info(
        "Level watcher started — checking %d symbols every %d min (proximity %.1f%%)",
        len(load_level_watchlist()), settings.level_watch.check_interval_minutes,
        settings.level_watch.proximity_pct * 100,
    )
    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down level watcher")
        scheduler.shutdown()


if __name__ == "__main__":
    from src.logger import setup_logging

    setup_logging()
    run_forever()
