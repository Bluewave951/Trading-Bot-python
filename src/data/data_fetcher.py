"""Unified API client for all data sources (yfinance, OKX via ccxt,
TradingView MCP placeholder).

Usage:
    fetcher = DataFetcher()
    candles = fetcher.fetch_ohlcv(Symbol("BTC/USDT", AssetClass.CRYPTO, source="okx"), "1h")

All fetchers return a normalized `list[Candle]`, oldest-first. Results are
cached (via `cache_manager`) and persisted to SQLite (via `Database`) so
downstream indicator/signal code never has to know which API a symbol
came from.
"""
from __future__ import annotations

import abc
from datetime import datetime, timezone

from src.data.cache_manager import cache
from src.data.database import Database
from src.data.models import AssetClass, Candle, Symbol
from src.logger import get_logger

logger = get_logger(__name__)

# yfinance interval strings per our internal timeframe names.
_YF_INTERVAL = {"1h": "60m", "4h": "60m", "1d": "1d"}  # 4h resampled from 60m
# ccxt/OKX timeframe strings map 1:1 with ours except naming.
_CCXT_TIMEFRAME = {"1h": "1h", "4h": "4h", "1d": "1d"}


class BaseFetcher(abc.ABC):
    """Interface every data-source fetcher must implement."""

    name: str = "base"

    @abc.abstractmethod
    def fetch(self, ticker: str, timeframe: str, limit: int) -> list[Candle]:
        """Return up to `limit` most-recent candles, oldest-first."""
        raise NotImplementedError


class YFinanceFetcher(BaseFetcher):
    """Stocks & Gold (e.g. AAPL, SPY, GC=F, XAUUSD=X) via yfinance."""

    name = "yfinance"

    def fetch(self, ticker: str, timeframe: str, limit: int) -> list[Candle]:
        import yfinance as yf

        interval = _YF_INTERVAL.get(timeframe, "1d")
        # yfinance needs a generous period to guarantee `limit` bars back.
        # "730d" is yfinance's actual max lookback for 60m-interval data
        # (an explicit day count goes further back than the "2y" period
        # string does) — needed to cover the plan's "1 year of data"
        # backtest requirement on the 1h/4h timeframes.
        period_by_interval = {"60m": "730d", "1d": "2y"}
        period = period_by_interval.get(interval, "1y")

        df = yf.Ticker(ticker).history(period=period, interval=interval)
        if df.empty:
            logger.warning("yfinance returned no data for %s (%s)", ticker, timeframe)
            return []

        if timeframe == "4h":
            df = (
                df.resample("4h")
                .agg(
                    {
                        "Open": "first",
                        "High": "max",
                        "Low": "min",
                        "Close": "last",
                        "Volume": "sum",
                    }
                )
                .dropna()
            )

        df = df.tail(limit)
        candles = [
            Candle(
                symbol=ticker,
                timeframe=timeframe,
                timestamp=ts.to_pydatetime().astimezone(timezone.utc),
                open=float(row.Open),
                high=float(row.High),
                low=float(row.Low),
                close=float(row.Close),
                volume=float(row.Volume),
            )
            for ts, row in df.iterrows()
        ]
        return candles


class OKXFetcher(BaseFetcher):
    """Crypto (BTC/USDT, ETH/USDT, ...) via OKX through ccxt.

    Was Binance until api.binance.com started returning HTTP 451
    ("restricted location") to GitHub Actions runner IPs. OKX's public
    market-data endpoints don't require an API key and aren't
    geo-blocked for those runners.
    """

    name = "okx"
    # OKX's actual max candles per fetch_ohlcv call (vs. Binance's 1000) —
    # ccxt silently clamps a higher `limit` down to this rather than
    # erroring, so this must be accurate or `_fetch_paginated` below will
    # mistake a clamped-short batch for "reached the most recent candle".
    _MAX_LIMIT_PER_CALL = 300

    def __init__(self) -> None:
        import ccxt

        self._exchange = ccxt.okx({"enableRateLimit": True})

    def fetch(self, ticker: str, timeframe: str, limit: int) -> list[Candle]:
        tf = _CCXT_TIMEFRAME.get(timeframe, "1h")
        if limit <= self._MAX_LIMIT_PER_CALL:
            raw = self._exchange.fetch_ohlcv(ticker, timeframe=tf, limit=limit)
        else:
            raw = self._fetch_paginated(ticker, tf, limit)
        return [
            Candle(
                symbol=ticker,
                timeframe=timeframe,
                timestamp=datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
            for row in raw
        ]

    def _fetch_paginated(self, ticker: str, tf: str, limit: int) -> list[list]:
        """Page backward in `_MAX_LIMIT_PER_CALL`-sized chunks to satisfy a
        `limit` beyond OKX's per-call cap — e.g. ~1 year of 1h candles
        (8760) needs ~29 calls. `enableRateLimit=True` (set in `__init__`)
        makes ccxt throttle these automatically.
        """
        tf_ms = self._exchange.parse_timeframe(tf) * 1000
        since = self._exchange.milliseconds() - limit * tf_ms
        collected: dict[int, list] = {}

        while len(collected) < limit:
            batch = self._exchange.fetch_ohlcv(
                ticker, timeframe=tf, since=since, limit=self._MAX_LIMIT_PER_CALL
            )
            if not batch:
                break
            for row in batch:
                collected[row[0]] = row

            next_since = batch[-1][0] + tf_ms
            if next_since <= since:
                break  # safety net against a non-advancing loop
            since = next_since
            if len(batch) < self._MAX_LIMIT_PER_CALL:
                break  # reached the most recent available candle

        rows = sorted(collected.values(), key=lambda row: row[0])
        return rows[-limit:]


class TradingViewFetcher(BaseFetcher):
    """Placeholder bridge to the TradingView MCP tools.

    The MCP tools are only reachable from within a Claude Code / MCP-client
    session, not from a standalone Python process. When running standalone,
    inject a callable (e.g. an HTTP bridge to that session) via
    `set_mcp_client()`. Until configured, this fetcher raises so callers fall
    back to yfinance/ccxt instead of silently returning empty data.
    """

    name = "tradingview"
    _mcp_client = None

    @classmethod
    def set_mcp_client(cls, client) -> None:
        cls._mcp_client = client

    def fetch(self, ticker: str, timeframe: str, limit: int) -> list[Candle]:
        if self._mcp_client is None:
            raise RuntimeError(
                "TradingView MCP client not configured; call "
                "TradingViewFetcher.set_mcp_client() or use source='yfinance'/'okx'."
            )
        raw = self._mcp_client.get_ohlcv(ticker, timeframe, limit)
        return [
            Candle(
                symbol=ticker,
                timeframe=timeframe,
                timestamp=datetime.fromisoformat(row["time"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 0.0)),
            )
            for row in raw
        ]


class DataFetcher:
    """Routes each `Symbol` to the correct source fetcher, with caching + persistence."""

    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database()
        self._fetchers: dict[str, BaseFetcher] = {}

    def _get_fetcher(self, source: str) -> BaseFetcher:
        if source not in self._fetchers:
            if source == "yfinance":
                self._fetchers[source] = YFinanceFetcher()
            elif source == "okx":
                self._fetchers[source] = OKXFetcher()
            elif source == "tradingview":
                self._fetchers[source] = TradingViewFetcher()
            else:
                raise ValueError(f"Unknown data source: {source}")
        return self._fetchers[source]

    def fetch_ohlcv(
        self, symbol: Symbol, timeframe: str, limit: int = 200, use_cache: bool = True
    ) -> list[Candle]:
        """Fetch OHLCV candles, using cache first, falling back to the live API.

        Successful live fetches are cached and persisted to SQLite.
        """
        cache_key = cache.make_key("ohlcv", symbol.ticker, timeframe, str(limit))

        if use_cache:
            cached = cache.get(cache_key)
            if cached:
                return [
                    Candle(
                        symbol=c["symbol"],
                        timeframe=c["timeframe"],
                        timestamp=datetime.fromisoformat(c["timestamp"]),
                        open=c["open"],
                        high=c["high"],
                        low=c["low"],
                        close=c["close"],
                        volume=c["volume"],
                    )
                    for c in cached
                ]

        try:
            fetcher = self._get_fetcher(symbol.source)
            candles = fetcher.fetch(symbol.ticker, timeframe, limit)
        except Exception:
            logger.exception(
                "Failed to fetch %s (%s) from %s", symbol.ticker, timeframe, symbol.source
            )
            # Fall back to last known data in the DB rather than failing hard.
            return self.db.get_candles(symbol.ticker, timeframe, limit)

        if candles:
            self.db.insert_candles(candles)
            cache.set(cache_key, [c.to_dict() for c in candles], timeframe=timeframe)
        return candles

    def fetch_many(
        self, symbols: list[Symbol], timeframes: list[str], limit: int = 200
    ) -> dict[tuple[str, str], list[Candle]]:
        """Convenience batch fetch across symbols x timeframes."""
        result: dict[tuple[str, str], list[Candle]] = {}
        for symbol in symbols:
            for tf in timeframes:
                result[(symbol.ticker, tf)] = self.fetch_ohlcv(symbol, tf, limit)
        return result
