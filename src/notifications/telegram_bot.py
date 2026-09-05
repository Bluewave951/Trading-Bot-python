"""Telegram alerts + control commands.

Commands (see plan): /status /analyze <SYMBOL> /backtest <SYMBOL> /settings /chart

`TelegramNotifier` (push alerts) uses the raw Bot API via `requests` — no
`python-telegram-bot` dependency needed for one-way delivery. The
interactive commands below (`build_application`/`run_bot`) do use
`python-telegram-bot`, imported lazily so importing this module for alerts
alone never requires it.
"""
from __future__ import annotations

import json

import requests

from src.config import settings
from src.data.models import AssetClass, Signal, SignalSide, Symbol
from src.logger import get_logger
from src.notifications.formatting import (
    format_analysis_text,
    format_backtest_text,
    format_status_text,
)

logger = get_logger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/{method}"
_TIMEOUT_SECONDS = 10


class TelegramNotifier:
    """Sends text alerts to a chat via the Telegram Bot API's `sendMessage`."""

    def __init__(self, bot_token: str | None = None, chat_id: str | None = None) -> None:
        self.bot_token = bot_token if bot_token is not None else settings.telegram_bot_token
        self.chat_id = chat_id if chat_id is not None else settings.telegram_chat_id

    @property
    def enabled(self) -> bool:
        return bool(settings.alerts.telegram_enabled and self.bot_token and self.chat_id)

    def send_text(self, content: str) -> bool:
        """Returns False (never raises) on misconfiguration or delivery
        failure so callers/queues can retry or move on without a channel
        outage taking down the whole bot."""
        if not self.enabled:
            logger.warning("Telegram alerts disabled or not configured; skipping")
            return False
        url = _API_BASE.format(token=self.bot_token, method="sendMessage")
        try:
            resp = requests.post(
                url, json={"chat_id": self.chat_id, "text": content}, timeout=_TIMEOUT_SECONDS
            )
            resp.raise_for_status()
            return True
        except requests.RequestException:
            logger.exception("Failed to send Telegram alert")
            return False


# -- Interactive commands (/status /analyze /backtest /settings /chart) -----------------
#
# Each command's logic lives in a plain function returning text, kept
# separate from the python-telegram-bot handler wiring so it's unit
# testable without a live bot/network round-trip.

def _resolve_symbol(ticker: str) -> Symbol:
    """`BTC/USDT`-shaped tickers are routed to OKX/crypto; anything
    else is treated as a yfinance stock ticker."""
    ticker = ticker.upper()
    if "/" in ticker:
        return Symbol(ticker=ticker, asset_class=AssetClass.CRYPTO, source="okx")
    return Symbol(ticker=ticker, asset_class=AssetClass.STOCK, source="yfinance")


def analyze_symbol_text(ticker: str, timeframe: str = "1h", limit: int = 250) -> str:
    """`/analyze <SYMBOL> [timeframe]` — fetch + compute indicators now."""
    from src.data.data_fetcher import DataFetcher
    from src.indicators.indicator_manager import calculate_all_indicators

    symbol = _resolve_symbol(ticker)
    candles = DataFetcher().fetch_ohlcv(symbol, timeframe, limit=limit)
    if len(candles) < 20:
        return f"Not enough data for {ticker} ({timeframe})."
    snapshot = calculate_all_indicators(candles)
    return format_analysis_text(snapshot)


def backtest_symbol_text(ticker: str, timeframe: str = "1h", limit: int = 2000) -> str:
    """`/backtest <SYMBOL> [timeframe]` — run a backtest now and summarize it."""
    from src.data.data_fetcher import DataFetcher
    from src.backtesting.backtest_engine import run_backtest
    from src.backtesting.backtest_stats import calculate_stats, validate_strategy

    symbol = _resolve_symbol(ticker)
    candles = DataFetcher().fetch_ohlcv(symbol, timeframe, limit=limit)
    if len(candles) < 100:
        return f"Not enough data to backtest {ticker} ({timeframe})."
    result = run_backtest(candles)
    stats = calculate_stats(result)
    validation = validate_strategy(stats)
    return format_backtest_text(ticker.upper(), timeframe, stats, validation)


def status_text(limit: int = 50) -> str:
    """`/status` — open positions currently tracked in the database."""
    from src.data.database import Database

    rows = Database().get_recent_signals(limit=limit)
    open_signals = [
        Signal(
            symbol=row["symbol"],
            timeframe=row["timeframe"],
            side=SignalSide(row["side"]),
            entry=row["entry"],
            sl=row["sl"],
            tp=row["tp"],
            risk_reward=row["risk_reward"],
            confidence=row["confidence"],
            reasons=json.loads(row["reasons"]),
        )
        for row in rows
        if row["status"] == "open"
    ]
    return format_status_text(open_signals)


def settings_text() -> str:
    """`/settings` — current alert/risk configuration summary."""
    cfg = settings
    return (
        "⚙️ Settings\n"
        f"Timeframes: {', '.join(cfg.timeframes)}\n"
        f"Risk/reward ratio: {cfg.risk.risk_reward_ratio}\n"
        f"Default SL: {cfg.risk.default_sl_pct:.1%}\n"
        f"Max position risk: {cfg.risk.max_position_risk_pct:.1%}\n"
        f"Telegram alerts: {'on' if cfg.alerts.telegram_enabled else 'off'}\n"
        f"Discord alerts: {'on' if cfg.alerts.discord_enabled else 'off'}\n"
        f"Email alerts: {'on' if cfg.alerts.email_enabled else 'off'}"
    )


def chart_text(ticker: str) -> str:
    """`/chart <SYMBOL>` — placeholder. Server-side chart image rendering
    isn't implemented yet; `src/web/static/charts.js` renders client-side
    in the (also stubbed) Phase 6 dashboard, not as a shareable image."""
    return f"Chart rendering for {ticker.upper()} isn't available yet — see src/web/ (Phase 6)."


def build_application():
    """Build a `python-telegram-bot` `Application` wired to the commands above.

    Raises `RuntimeError` if `TELEGRAM_BOT_TOKEN` isn't set. Import of
    `telegram` is local to this function so the rest of the module (and
    `TelegramNotifier`) works without the dependency installed.
    """
    from telegram import Update
    from telegram.ext import Application, CommandHandler, ContextTypes

    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    application = Application.builder().token(settings.telegram_bot_token).build()

    async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(status_text())

    async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            await update.message.reply_text("Usage: /analyze <SYMBOL> [timeframe]")
            return
        timeframe = context.args[1] if len(context.args) > 1 else "1h"
        await update.message.reply_text(analyze_symbol_text(context.args[0], timeframe))

    async def cmd_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            await update.message.reply_text("Usage: /backtest <SYMBOL> [timeframe]")
            return
        timeframe = context.args[1] if len(context.args) > 1 else "1h"
        await update.message.reply_text(backtest_symbol_text(context.args[0], timeframe))

    async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(settings_text())

    async def cmd_chart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        ticker = context.args[0] if context.args else "?"
        await update.message.reply_text(chart_text(ticker))

    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("analyze", cmd_analyze))
    application.add_handler(CommandHandler("backtest", cmd_backtest))
    application.add_handler(CommandHandler("settings", cmd_settings))
    application.add_handler(CommandHandler("chart", cmd_chart))

    return application


def run_bot() -> None:
    """Entry point for the interactive bot: `python -m src.notifications.telegram_bot`."""
    application = build_application()
    application.run_polling()


if __name__ == "__main__":
    from src.logger import setup_logging

    setup_logging()
    run_bot()
