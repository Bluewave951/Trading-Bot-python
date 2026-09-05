# Trading Analysis & Signal Bot

Automated trading assistant that analyzes US Stocks, Crypto (Bitcoin), and
Gold across multiple timeframes, detects Support/Resistance, and generates
Buy/Sell signals with dynamic SL/TP. See [`../TRADING_BOT_PLAN.md`](../TRADING_BOT_PLAN.md)
for the full design.

## Status

| Phase | Component | Status |
|-------|-----------|--------|
| 1 | Data Pipeline | ✅ Implemented |
| 2 | Indicator Engine | ✅ Implemented (`src/indicators/`) |
| 3 | Signal Generation | ✅ Implemented (`src/signals/`) |
| 4 | Backtesting | ✅ Implemented (`src/backtesting/`) |
| 5 | Notifications | ✅ Implemented (`src/notifications/`) |
| — | Scheduler (`src/scheduler.py`) | ✅ Implemented — wires fetch→indicators→signal→alert into a real running app |
| 6 | Web Dashboard | ✅ Implemented (`src/web/`) |
| 7 | Integration & Testing | 🟡 Partial — pipeline is wired & tested; paper-trading run not done |

## Setup

```bash
cd trading-bot
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy .env.example .env        # fill in tokens/keys as needed
```

Edit `config/symbols.yaml` to set your watchlist and `config/settings.yaml`
for indicator/risk parameters.

## Run

```bash
python main.py            # one full pass: fetch -> indicators -> signal -> alert, then exit
python -m src.scheduler   # same pipeline, running continuously on the configured cadence
```

Both fetch OHLCV for every watchlist symbol/timeframe from yfinance/OKX
(caching via Redis if available, else in-memory automatically — no Redis
server required), compute indicators, check for a BUY signal, and on a hit
persist it to `data/trading.db` and dispatch alerts via whichever
notification channels are enabled (see Notifications below). `main.py`
scans once and exits (good for a manual check or `cron`); `src.scheduler`
runs the three plan-specified jobs (1h every 60s, 4h every 300s, 1d at
00:00 UTC — configurable in `config/settings.yaml`'s `scheduler:` block)
until interrupted (Ctrl+C).

Verified live on 2026-09-04: `python main.py` completed a full 3-timeframe
scan of the 8-symbol watchlist in ~7s (0 signals that pass — expected,
not an error); `python -m src.scheduler` was started, its `scan_1h` job
fired on schedule at the 60s mark and completed successfully, then it was
stopped — confirming the continuous mode actually runs, not just the
one-shot path.

## Test

```bash
pytest
```

`tests/test_data_pipeline.py` covers the Phase 1 database + cache layer,
`tests/test_indicators.py` covers Phase 2 (moving averages, RSI/MACD/
Stochastic, Bollinger/ATR, OBV/volume avg, Fibonacci, and the S/R detection
algorithm), `tests/test_signals.py` covers Phase 3 (entry/exit signal
confluence, position sizing, multi-timeframe aggregation), `tests/test_backtest.py`
covers Phase 4 (trade simulation, stats, validation, CSV/HTML reports),
`tests/test_notifications.py` covers Phase 5 (message formatting, each
channel, the retry queue), `tests/test_scheduler.py` covers the scan/
persist/alert wiring in `src/scheduler.py`, `tests/test_web.py` covers
Phase 6 (every API route, JSON serialization, WebSocket connect/broadcast).
`tests/test_integration.py`
still has real Phase 7 gaps (paper trading, a multi-day live comparison)
that no single pytest run can cover — see its docstring.

## Project Layout

See the "Critical Files to Create/Modify" section of `TRADING_BOT_PLAN.md`
for the intended full structure. Files not yet implemented contain a
docstring pointing back to the relevant plan section.

## Backtest a symbol

```python
from src.data.data_fetcher import DataFetcher
from src.data.models import AssetClass, Symbol
from src.backtesting.backtest_engine import run_backtest
from src.backtesting.backtest_stats import calculate_stats, validate_strategy
from src.backtesting.backtest_reports import export_trades_csv, generate_html_report

fetcher = DataFetcher()
symbol = Symbol(ticker="BTC/USDT", asset_class=AssetClass.CRYPTO, source="okx")
# limit=8760 = ~1 year of 1h candles; OKXFetcher/YFinanceFetcher page
# past their single-call caps automatically for limits this large.
candles = fetcher.fetch_ohlcv(symbol, "1h", limit=8760)

result = run_backtest(candles)
stats = calculate_stats(result)
validation = validate_strategy(stats)  # win_rate>=50%, profit_factor>=1.5, max_dd<=20%

export_trades_csv(result, "data/backtest_trades.csv")
generate_html_report(result, stats, validation, "data/backtest_report.html")
```

### Switched crypto data source: Binance → OKX (2026-09-05)

The GitHub Actions scan workflow was "green" every hour but never actually
found data: `api.binance.com` returns HTTP 451 ("restricted location") to
GitHub-hosted runner IPs, so every crypto fetch failed, was swallowed by
the fallback-to-cache handling in `DataFetcher.fetch_ohlcv`, and the run
logged `0 signal(s) found` — with no exception, so CI never went red.
Replaced `BinanceFetcher` with `OKXFetcher` (`src/data/data_fetcher.py`,
`source: okx` in `config/symbols.yaml`/`config/level_watch.yaml`); OKX's
public market-data endpoints aren't geo-blocked for those runners and
ccxt's unified `BTC/USDT`-style symbols need no other changes. Note OKX's
per-call candle cap is 300, not Binance's 1000 — `_MAX_LIMIT_PER_CALL` was
adjusted accordingly so `_fetch_paginated` still pages correctly.

### Entry/exit tuning (2026-09-04)

**Round 1 — diagnosis on a 1000-candle (~6 week) sample.** The initial
implementation scored ~38% win rate / 0.79 profit factor on BTC/USDT 1h.
The trade log showed why: BUY requires all 6 plan conditions at once
(fires <1% of bars) while SELL's original "any 1 of 3" confirmation fired
constantly with confidence uncorrelated to outcome (~617 sells vs ~8 buys
across a 5-symbol sample — SELL was opening a *short* at this point).
Tightened SELL to require all three bearish confirmations, tightened S/R
proximity from 1% to 0.5%, and let BUY's MACD condition fire on the
histogram *turning up* (not just already positive). Result on that same
small sample: win rate 37.8%→47.4%, profit factor 0.79→1.14 in aggregate —
looked like real progress.

**Round 2 — it wasn't (mostly).** `data_fetcher.py` didn't support fetching
more than one API call's worth of history (yfinance's `period="60d"`,
Binance's 1000-candle cap), so every result above was one 6-week window.
Adding pagination (`BinanceFetcher._fetch_paginated`, walking `since`
backward; `YFinanceFetcher` bumped to `period="730d"`) enabled a real
1-year backtest (8760 hourly candles for crypto). Re-run on the same
7 symbols: win rate 47.4%→**40.9%**, profit factor 1.14→**0.95**. The
"improvement" from round 1 was mostly small-sample noise.

**Round 3 — the real fix.** The 1-year run's honest numbers exposed the
actual structural problem: SELL, opening independent short positions, lost
money in aggregate (40.9% win rate isn't from a bad BUY side — see the
buy/sell split below) because fading strength into resistance/RSI>70 has
no edge in the broadly upward-biased regimes (crypto, US equities) this
bot targets. Redefined SELL: `entry_signals.should_exit_long()` now closes
an already-open BUY position early instead of opening a short —
`generate_sell_signal` (short entry) no longer exists. Re-run on the same
1-year/7-symbol set:

| | Short-enabled (1yr) | Long-only + early-exit (1yr) |
|---|---|---|
| Trades | 721 (buy=112, sell=609) | 112 (buy=112, sell=0) |
| Win rate | 40.9% | 41.1% |
| Profit factor | 0.95 | 0.93 |
| Total return | -6.35% | **-1.22%** |

Removing the short side cut the loss by ~5x (fewer, less-noisy trades) —
a clear, validated improvement. **What's still unresolved**: BUY alone
doesn't have a robust edge across this asset universe yet. Per-symbol
results vary widely — SPY (55.9% win rate, 1.41 PF) and AAPL (1.21 PF) are
promising, SOL is bad (11.1%, 9 trades), TSLA never fired in a full year
(entry conditions too strict for its behavior). None pass full validation
(win rate≥50% + PF≥1.5 + drawdown≤20%) yet. A plausible next step —
**not implemented**: per-asset-class thresholds (crypto's volatility likely
needs different RSI/EMA parameters than large-cap equities), since one
fixed `IndicatorConfig` for both is probably part of why performance is
so uneven — this is exactly the "per-asset-class risk parameters" gap
`src/assets/base_analyzer.py` is stubbed for (Phase 3's plan section).

Backtesting still replays a single symbol/timeframe through
`entry_signals` directly rather than the full multi-timeframe
`signal_aggregator` — see the scope note in `backtest_engine.py`.

## Notifications

```python
from src.notifications.discord_bot import DiscordNotifier
from src.notifications.telegram_bot import TelegramNotifier
from src.notifications.email_service import EmailNotifier
from src.notifications.notification_queue import NotificationQueue
from src.notifications.formatting import format_signal_text

queue = NotificationQueue(channels=[DiscordNotifier(), TelegramNotifier(), EmailNotifier()])
queue.dispatch(format_signal_text(signal))  # sends to every *enabled* channel, with retry
```

Each channel is only used if **both** its `config/alerts.yaml` toggle is
`true` **and** its secrets are set in `.env` — see each notifier's
`enabled` property.

**Status in this deployment (verified 2026-09-04, all three toggles now
`true` in `config/alerts.yaml`):**
- ✅ **Discord** — incoming webhook, sent a real test message successfully.
- ✅ **Telegram** — raw Bot API. First attempt failed with `400 chat not
  found`: a bot can't message a chat until that chat has messaged it first.
  Sent `/start` to the bot, confirmed the chat via `getUpdates`, retried —
  succeeded with the same `TELEGRAM_CHAT_ID` already in `.env`.
- ✅ **Email** — Gmail SMTP. First attempt failed with `535 Username and
  Password not accepted`: Gmail requires an **App Password**
  (myaccount.google.com/apppasswords, needs 2-Step Verification on first),
  not the regular account password. Updated `EMAIL_PASSWORD` in `.env` to
  the 16-character app password — succeeded.

`telegram_bot.build_application()` / `run_bot()` additionally wire up the
plan's interactive commands (`/status /analyze /backtest /settings /chart`)
via `python-telegram-bot` — run `python -m src.notifications.telegram_bot`
to start that bot (needs `TELEGRAM_BOT_TOKEN` set; `/chart` is still a
placeholder — the dashboard below renders its equity curve client-side in
the browser, not as a server-side image Telegram could attach).

## Level Watcher (ad-hoc, outside the trading strategy)

```bash
python -m src.level_watcher   # checks config/level_watch.yaml's watchlist every N min until Ctrl+C
```

A user-requested standalone monitor (2026-09-04), independent of the
BUY-only strategy above — it doesn't generate trade signals, just alerts
Discord + Telegram when a symbol's price comes within `proximity_pct`
(default 1%) of a support/resistance level from the bot's own
`src.indicators.support_resistance` (swing-clustering — **not** the same
levels a TradingView SMC/order-block analysis would show for the same
symbol; the two are different algorithms and won't match exactly).
Watchlist, interval (default 30 min — no 1-hour floor like the cloud
`schedule` skill has, since this runs as a plain local `APScheduler` job),
and proximity threshold are all in `config/level_watch.yaml`. Every hit is
also persisted to a `level_alerts` DB table (`Database.insert_level_alert`)
— one row per level, so a symbol near both support and resistance in the
same pass produces two rows but one combined notification. The web
dashboard's "Level Watcher Alerts" section (below) reads this table for
stats/charts.

Verified live on 2026-09-04: two real passes over all 17 configured
symbols found and alerted 14 individual level hits across 8 symbols (NBIS,
WDC, TSM, NVDA, VOO ×4, QQQM, GC=F ×4, SUSHI/USDT), dispatched to both
channels and persisted to the DB, then confirmed rendering correctly on
the dashboard (light + dark, via the `browser-automation` skill).
Currently running as a background process from that session — it stops if
that process/machine goes down; for a true always-on 24/7 service (survives
reboots/crashes) it'd need wrapping as an actual Windows Service or Scheduled
Task, not done here.

**Note on the watchlist**: `NASDAQ:GOLD`/`NYSE:GOLD` is not Barrick Gold —
both yfinance and TradingView currently resolve that ticker to "Gold.com,
Inc.", an unrelated small-cap finance company. Gold *price* exposure here
uses `GC=F` (COMEX gold futures) instead, since `XAUUSD=X` no longer
resolves on yfinance (404).

## Web Dashboard

```bash
uvicorn src.web.app:app --reload --port 8000   # dev, auto-restarts on file changes
# or: python -m src.web.app                    # plain run
```

Open `http://localhost:8000`. It's a single hand-rolled HTML/CSS/vanilla-JS
page (`src/web/static/`) — no React/Vue/build step, matching the rest of
this project's dependency-light style — served by FastAPI
(`src/web/app.py`), backed by four JSON routes:

- `GET /api/signals` — recent rows from the same `data/trading.db` `src.scheduler` writes to.
- `POST /api/test` — dispatch a message through `NotificationQueue` to every enabled channel (a "does delivery still work" button, independent of any real signal firing).
- `GET /api/backtest?symbol=&timeframe=&limit=` — run `run_backtest()` live and return stats/validation/equity-curve/trade-log as JSON.
- `GET /api/level-alerts?limit=` — recent rows from `src.level_watcher`'s `level_alerts` table (see "Level Watcher" above). The "Level Watcher Alerts" dashboard section computes stats (total/today/by-kind), a 14-day stacked bar chart (support vs resistance, per the dataviz method's categorical-color rules), and a ranked horizontal bar chart of most-alerted symbols — all client-side from this one list, same pattern as the Recent Signals stat tiles.

New signals reach the page over `/ws` without a manual refresh: a
background asyncio task in the web process polls the DB every 5s and
broadcasts anything new (see `websocket_manager.py`) — the scheduler
process (a separate `python -m src.scheduler` run) never talks to the web
process directly, only through the shared SQLite file.

**Verified live (2026-09-04)**, via the `browser-automation` skill —
loaded the page (0 console errors, 0 failed requests), ran a real backtest
through the UI (stat tiles + equity curve + trade log all rendered
correctly in both light and dark mode), clicked "Send Test Alert" and
confirmed all three channels reported `sent`, and inserted a signal
directly into the DB to confirm the dashboard's signal count and table
update **without a page reload** — the WebSocket push actually works, not
just the polling fallback.

**Bug found via this testing** (fixed, with a regression test): `/api/backtest`
500'd with `Unable to serialize unknown type: numpy.bool`. Root cause was in
`support_resistance.py`, not the web layer — indexing a numpy array
(`highs[i]`) yields `np.float64`, which silently propagated through every
downstream computation (S/R levels → `Signal.entry/sl/tp` → backtest P&L →
`validate_strategy()`'s comparisons) until FastAPI's JSON serializer, the
first thing in the whole pipeline that actually rejects numpy scalars,
hit it. Fixed by casting to `float()` at the source. This means every
`Signal`/`BacktestTrade` produced before this fix could have been carrying
numpy-typed fields undetected — worth keeping in mind if old data or
logs look at those fields.

## Next Steps

The bot runs end-to-end now, with a dashboard to watch it
(`python -m src.scheduler` + `uvicorn src.web.app:app`), but the BUY entry
logic isn't yet validated as profitable across the full asset universe
(see tuning notes above) — treat live signals accordingly until
per-asset-class tuning (`src/assets/`) or further backtesting closes that
gap. After that: real Phase 7 validation (paper trading, no live money,
for at least a week) before ever considering Phase 8.
