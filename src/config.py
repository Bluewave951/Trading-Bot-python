"""Application configuration.

Loads settings from `config/*.yaml` and environment variables (`.env`).
Secrets (API keys, tokens) must come from environment variables — never commit
them to the yaml files.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT_DIR / "config"
DATA_DIR = ROOT_DIR / "data"

load_dotenv(ROOT_DIR / ".env")


def _load_yaml(filename: str) -> dict[str, Any]:
    path = CONFIG_DIR / filename
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass
class DataSourceConfig:
    """Which data providers are enabled and their refresh intervals (seconds)."""
    okx_enabled: bool = True
    yfinance_enabled: bool = True
    tradingview_enabled: bool = False  # requires MCP session
    refresh_interval_1h: int = 300      # 5 min
    refresh_interval_4h: int = 900      # 15 min
    refresh_interval_1d: int = 3600     # 1 hour


@dataclass
class RiskConfig:
    """Global risk parameters used by signal/exit logic."""
    default_sl_pct: float = 0.02       # 2% fallback stop loss
    risk_reward_ratio: float = 1.5     # TP distance = risk * this
    profit_target_min_pct: float = 0.30
    profit_target_max_pct: float = 0.50
    max_position_risk_pct: float = 0.02  # per-trade account risk


@dataclass
class IndicatorConfig:
    """Indicator periods and S/R params, from settings.yaml `indicators:`."""
    rsi_period: int = 14
    ema_periods: list[int] = field(default_factory=lambda: [20, 50, 200])
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bollinger_period: int = 20
    bollinger_std_dev: float = 2.0
    atr_period: int = 14
    volume_avg_period: int = 20
    sr_lookback_candles: int = 50
    sr_min_touches: int = 2
    fib_levels: list[float] = field(
        default_factory=lambda: [0.236, 0.382, 0.5, 0.618, 0.786]
    )


@dataclass
class DeliveryConfig:
    """Retry/backoff for `notification_queue.NotificationQueue`, from
    settings.yaml `alerts.yaml`'s `delivery:` block."""
    max_retries: int = 3
    retry_backoff_seconds: float = 5.0
    queue_max_size: int = 500


@dataclass
class AlertsConfig:
    """Per-channel enable toggles from `config/alerts.yaml`. Secrets live in
    `.env` (see `AppConfig.telegram_bot_token` etc.) — a channel is only
    actually used if both this flag is true AND its secrets are present
    (see each notifier's `enabled` property)."""
    telegram_enabled: bool = False
    telegram_commands: list[str] = field(default_factory=list)
    discord_enabled: bool = False
    email_enabled: bool = False
    email_send_chart_screenshot: bool = True
    delivery: DeliveryConfig = field(default_factory=DeliveryConfig)


@dataclass
class SchedulerConfig:
    """Scan cadence per timeframe, from settings.yaml `scheduler:` block."""
    scan_interval_1h_seconds: int = 60
    scan_interval_4h_seconds: int = 300
    scan_1d_at_utc: str = "00:00"


@dataclass
class LevelWatchConfig:
    """`src/level_watcher.py`'s standalone S/R-proximity watcher, from
    `config/level_watch.yaml` — a separate watchlist/config from the
    BUY-only trading strategy (`symbols.yaml`/`load_watchlist()`)."""
    check_interval_minutes: int = 30
    proximity_pct: float = 0.01
    timeframe: str = "1h"
    watchlist: dict[str, list[dict]] = field(default_factory=dict)


@dataclass
class AppConfig:
    """Top-level application configuration, assembled from yaml + env."""
    symbols: dict[str, list[str]] = field(default_factory=dict)
    timeframes: list[str] = field(default_factory=lambda: ["1h", "4h", "1d"])
    data_sources: DataSourceConfig = field(default_factory=DataSourceConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    indicators: IndicatorConfig = field(default_factory=IndicatorConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    level_watch: LevelWatchConfig = field(default_factory=LevelWatchConfig)
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    discord_webhook_url: str | None = None
    email_smtp_host: str | None = None
    email_smtp_port: int = 587
    email_username: str | None = None
    email_password: str | None = None
    database_path: str = str(DATA_DIR / "trading.db")
    redis_url: str = "redis://localhost:6379/0"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    @classmethod
    def load(cls) -> "AppConfig":
        symbols_cfg = _load_yaml("symbols.yaml")
        settings_cfg = _load_yaml("settings.yaml")
        alerts_cfg = _load_yaml("alerts.yaml")
        level_watch_cfg = _load_yaml("level_watch.yaml")

        timeframes = settings_cfg.get("timeframes", ["1h", "4h", "1d"])
        ds_cfg = settings_cfg.get("data_sources", {})
        risk_cfg = settings_cfg.get("risk", {})
        ind_cfg = settings_cfg.get("indicators", {})
        macd_cfg = ind_cfg.get("macd", {})
        bb_cfg = ind_cfg.get("bollinger", {})

        sched_cfg = settings_cfg.get("scheduler", {})

        telegram_cfg = alerts_cfg.get("telegram", {})
        discord_cfg = alerts_cfg.get("discord", {})
        email_cfg = alerts_cfg.get("email", {})
        delivery_cfg = alerts_cfg.get("delivery", {})

        return cls(
            symbols=symbols_cfg.get("watchlist", {}),
            timeframes=timeframes,
            data_sources=DataSourceConfig(**ds_cfg),
            risk=RiskConfig(**risk_cfg),
            indicators=IndicatorConfig(
                rsi_period=ind_cfg.get("rsi_period", 14),
                ema_periods=ind_cfg.get("ema_periods", [20, 50, 200]),
                macd_fast=macd_cfg.get("fast", 12),
                macd_slow=macd_cfg.get("slow", 26),
                macd_signal=macd_cfg.get("signal", 9),
                bollinger_period=bb_cfg.get("period", 20),
                bollinger_std_dev=bb_cfg.get("std_dev", 2.0),
                atr_period=ind_cfg.get("atr_period", 14),
                volume_avg_period=ind_cfg.get("volume_avg_period", 20),
                sr_lookback_candles=ind_cfg.get("sr_lookback_candles", 50),
                sr_min_touches=ind_cfg.get("sr_min_touches", 2),
                fib_levels=ind_cfg.get(
                    "fib_levels", [0.236, 0.382, 0.5, 0.618, 0.786]
                ),
            ),
            alerts=AlertsConfig(
                telegram_enabled=telegram_cfg.get("enabled", False),
                telegram_commands=telegram_cfg.get("commands", []),
                discord_enabled=discord_cfg.get("enabled", False),
                email_enabled=email_cfg.get("enabled", False),
                email_send_chart_screenshot=email_cfg.get("send_chart_screenshot", True),
                delivery=DeliveryConfig(
                    max_retries=delivery_cfg.get("max_retries", 3),
                    retry_backoff_seconds=delivery_cfg.get("retry_backoff_seconds", 5.0),
                    queue_max_size=delivery_cfg.get("queue_max_size", 500),
                ),
            ),
            scheduler=SchedulerConfig(
                scan_interval_1h_seconds=sched_cfg.get("scan_interval_1h_seconds", 60),
                scan_interval_4h_seconds=sched_cfg.get("scan_interval_4h_seconds", 300),
                scan_1d_at_utc=sched_cfg.get("scan_1d_at_utc", "00:00"),
            ),
            level_watch=LevelWatchConfig(
                check_interval_minutes=level_watch_cfg.get("check_interval_minutes", 30),
                proximity_pct=level_watch_cfg.get("proximity_pct", 0.01),
                timeframe=level_watch_cfg.get("timeframe", "1h"),
                watchlist=level_watch_cfg.get("watchlist", {}),
            ),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
            discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL"),
            email_smtp_host=os.getenv("EMAIL_SMTP_HOST"),
            email_smtp_port=int(os.getenv("EMAIL_SMTP_PORT", "587")),
            email_username=os.getenv("EMAIL_USERNAME"),
            email_password=os.getenv("EMAIL_PASSWORD"),
            database_path=os.getenv("DATABASE_PATH", str(DATA_DIR / "trading.db")),
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            api_host=os.getenv("API_HOST", "0.0.0.0"),
            api_port=int(os.getenv("API_PORT", "8000")),
        )


settings = AppConfig.load()


def load_watchlist() -> list["Symbol"]:
    """Build `Symbol` objects from `settings.symbols` (config/symbols.yaml's
    `watchlist:` block). Shared by `main.py` and `src.scheduler` so the
    watchlist is parsed the same way everywhere.
    """
    from src.data.models import AssetClass, Symbol

    asset_class_map = {
        "stock": AssetClass.STOCK,
        "crypto": AssetClass.CRYPTO,
        "commodity": AssetClass.COMMODITY,
    }

    symbols: list[Symbol] = []
    for asset_key, entries in settings.symbols.items():
        asset_class = asset_class_map.get(asset_key)
        if asset_class is None:
            continue  # unknown asset class in symbols.yaml; caller may log this
        for entry in entries or []:
            symbols.append(
                Symbol(
                    ticker=entry["ticker"],
                    asset_class=asset_class,
                    source=entry.get("source", "yfinance"),
                )
            )
    return symbols


def load_level_watchlist() -> list["Symbol"]:
    """Build `Symbol` objects from `settings.level_watch.watchlist`
    (`config/level_watch.yaml`) — the separate watchlist for
    `src.level_watcher`, not the trading strategy's own `load_watchlist()`.
    """
    from src.data.models import AssetClass, Symbol

    asset_class_map = {
        "stock": AssetClass.STOCK,
        "crypto": AssetClass.CRYPTO,
        "commodity": AssetClass.COMMODITY,
    }

    symbols: list[Symbol] = []
    for asset_key, entries in settings.level_watch.watchlist.items():
        asset_class = asset_class_map.get(asset_key)
        if asset_class is None:
            continue
        for entry in entries or []:
            symbols.append(
                Symbol(
                    ticker=entry["ticker"],
                    asset_class=asset_class,
                    source=entry.get("source", "yfinance"),
                )
            )
    return symbols
