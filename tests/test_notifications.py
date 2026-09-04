"""Phase 5 tests: notification formatting, channels, and delivery queue.

Channel tests monkeypatch the network call (`requests.post` / `smtplib.SMTP`)
so nothing here touches a real Telegram/Discord/SMTP endpoint.
"""
from __future__ import annotations

from src.backtesting.backtest_stats import BacktestStats, ValidationResult
from src.data.models import (
    ExitSignal,
    IndicatorSnapshot,
    Signal,
    SignalSide,
    SupportResistanceLevels,
)
from src.notifications.discord_bot import DiscordNotifier
from src.notifications.email_service import EmailNotifier
from src.notifications.formatting import (
    format_analysis_text,
    format_backtest_text,
    format_exit_text,
    format_signal_text,
    format_status_text,
)
from src.notifications.notification_queue import NotificationQueue
from src.notifications.telegram_bot import TelegramNotifier, settings_text, status_text


def _signal(symbol="AAPL", **overrides) -> Signal:
    defaults = dict(
        symbol=symbol, timeframe="1h", side=SignalSide.BUY,
        entry=100.0, sl=98.0, tp=104.0, risk_reward=2.0, confidence=1.0,
        reasons=["price_near_support", "rsi<50"],
    )
    defaults.update(overrides)
    return Signal(**defaults)


def _snapshot(symbol="AAPL") -> IndicatorSnapshot:
    sr = SupportResistanceLevels(
        symbol=symbol, timeframe="1h", support_levels=[98.0], resistance_levels=[105.0]
    )
    return IndicatorSnapshot(
        symbol=symbol, timeframe="1h", close=100.0, rsi=45.0,
        ema_20=101.0, ema_50=99.0, ema_200=95.0,
        macd=0.5, macd_signal=0.3, macd_histogram=0.2,
        volume=1000.0, volume_avg_20=800.0, sr_levels=sr,
    )


# -- formatting ---------------------------------------------------------

def test_format_signal_text_includes_key_fields():
    text = format_signal_text(_signal())
    assert "AAPL" in text
    assert "100.0000" in text  # entry
    assert "98.0000" in text   # sl
    assert "104.0000" in text  # tp
    assert "price_near_support" in text


def test_format_exit_text_includes_reasons():
    text = format_exit_text("AAPL", "1h", ExitSignal(reasons=["rsi>70"], confidence=1.0))
    assert "AAPL" in text
    assert "rsi>70" in text


def test_format_analysis_text_includes_levels():
    text = format_analysis_text(_snapshot())
    assert "AAPL" in text
    assert "98.00" in text   # support
    assert "105.00" in text  # resistance


def test_format_backtest_text_shows_verdict():
    stats = BacktestStats(
        total_trades=10, win_rate=0.6, profit_factor=1.8, max_drawdown_pct=0.1,
        total_return_pct=0.15, avg_win_pct=0.03, avg_loss_pct=-0.02, sharpe_ratio=1.1,
    )
    validation = ValidationResult(passed=True, checks={"win_rate>=50%": True})
    text = format_backtest_text("AAPL", "1h", stats, validation)
    assert "PASSED" in text
    assert "60.0%" in text  # win rate


def test_format_status_text_empty():
    assert "No open positions" in format_status_text([])


def test_format_status_text_lists_signals():
    text = format_status_text([_signal(symbol="AAPL"), _signal(symbol="MSFT")])
    assert "AAPL" in text
    assert "MSFT" in text
    assert "2 open position" in text


# -- DiscordNotifier ---------------------------------------------------------

def test_discord_notifier_disabled_when_config_toggle_is_off(monkeypatch):
    import src.notifications.discord_bot as discord_bot

    # Config toggle off, regardless of whatever config/alerts.yaml currently
    # has it set to (it may be enabled for real use in this deployment).
    monkeypatch.setattr(discord_bot.settings.alerts, "discord_enabled", False)
    notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/123/abc")
    assert notifier.enabled is False
    assert notifier.send_text("hello") is False


def test_discord_notifier_disabled_without_webhook_url(monkeypatch):
    import src.notifications.discord_bot as discord_bot

    monkeypatch.setattr(discord_bot.settings.alerts, "discord_enabled", True)
    # `webhook_url=None` alone isn't enough to test "not configured" — the
    # constructor falls back to `settings.discord_webhook_url`, which is a
    # real secret in this deployment's `.env`. Patch that fallback too.
    monkeypatch.setattr(discord_bot.settings, "discord_webhook_url", None)
    notifier = DiscordNotifier(webhook_url=None)
    assert notifier.enabled is False
    assert notifier.send_text("hello") is False


def test_discord_notifier_sends_when_enabled(monkeypatch):
    import src.notifications.discord_bot as discord_bot

    monkeypatch.setattr(discord_bot.settings.alerts, "discord_enabled", True)
    notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/123/abc")

    calls = {}

    class _FakeResponse:
        def raise_for_status(self):
            pass

    def fake_post(url, json, timeout):
        calls["url"] = url
        calls["json"] = json
        return _FakeResponse()

    monkeypatch.setattr(discord_bot.requests, "post", fake_post)

    assert notifier.send_text("hello world") is True
    assert calls["json"] == {"content": "hello world"}


def test_discord_notifier_handles_request_failure(monkeypatch):
    import requests as requests_module

    import src.notifications.discord_bot as discord_bot

    monkeypatch.setattr(discord_bot.settings.alerts, "discord_enabled", True)
    notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/123/abc")

    def fake_post(*args, **kwargs):
        raise requests_module.ConnectionError("boom")

    monkeypatch.setattr(discord_bot.requests, "post", fake_post)

    assert notifier.send_text("hello") is False


# -- EmailNotifier ---------------------------------------------------------

def test_email_notifier_disabled_without_full_config(monkeypatch):
    import src.notifications.email_service as email_service

    # Even with the config toggle on, missing username makes it unusable.
    # `username=None` alone isn't enough — the constructor falls back to
    # `settings.email_username`, a real value in this deployment's `.env`.
    monkeypatch.setattr(email_service.settings.alerts, "email_enabled", True)
    monkeypatch.setattr(email_service.settings, "email_username", None)
    notifier = EmailNotifier(smtp_host="smtp.example.com", username=None, password="x")
    assert notifier.enabled is False
    assert notifier.send_text("hi") is False


def test_email_notifier_disabled_when_config_toggle_is_off(monkeypatch):
    import src.notifications.email_service as email_service

    monkeypatch.setattr(email_service.settings.alerts, "email_enabled", False)
    notifier = EmailNotifier(
        smtp_host="smtp.example.com", username="bot@example.com", password="x",
        to_address="me@example.com",
    )
    assert notifier.enabled is False
    assert notifier.send_text("hi") is False


def test_email_notifier_sends_when_enabled(monkeypatch):
    import src.notifications.email_service as email_service

    monkeypatch.setattr(email_service.settings.alerts, "email_enabled", True)
    notifier = EmailNotifier(
        smtp_host="smtp.example.com", smtp_port=587,
        username="bot@example.com", password="secret", to_address="me@example.com",
    )

    sent = {}

    class _FakeSMTP:
        def __init__(self, host, port, timeout):
            sent["host"] = host
            sent["port"] = port

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            sent["starttls"] = True

        def login(self, username, password):
            sent["login"] = (username, password)

        def send_message(self, message):
            sent["message"] = message

    monkeypatch.setattr(email_service.smtplib, "SMTP", _FakeSMTP)

    assert notifier.send_text("hello", subject="Test") is True
    assert sent["login"] == ("bot@example.com", "secret")
    assert sent["message"]["Subject"] == "Test"


def test_email_notifier_handles_smtp_failure(monkeypatch):
    import smtplib

    import src.notifications.email_service as email_service

    monkeypatch.setattr(email_service.settings.alerts, "email_enabled", True)
    notifier = EmailNotifier(
        smtp_host="smtp.example.com", smtp_port=587,
        username="bot@example.com", password="secret", to_address="me@example.com",
    )

    class _FailingSMTP:
        def __init__(self, *args, **kwargs):
            raise smtplib.SMTPConnectError(421, "down")

    monkeypatch.setattr(email_service.smtplib, "SMTP", _FailingSMTP)

    assert notifier.send_text("hello") is False


# -- TelegramNotifier ---------------------------------------------------------

def test_telegram_notifier_disabled_without_config(monkeypatch):
    import src.notifications.telegram_bot as telegram_bot

    # Even with the config toggle on, a missing bot_token makes it unusable.
    # `bot_token=None` alone isn't enough — the constructor falls back to
    # `settings.telegram_bot_token`, a real value in this deployment's `.env`.
    monkeypatch.setattr(telegram_bot.settings.alerts, "telegram_enabled", True)
    monkeypatch.setattr(telegram_bot.settings, "telegram_bot_token", None)
    notifier = TelegramNotifier(bot_token=None, chat_id="123")
    assert notifier.enabled is False
    assert notifier.send_text("hi") is False


def test_telegram_notifier_disabled_when_config_toggle_is_off(monkeypatch):
    import src.notifications.telegram_bot as telegram_bot

    monkeypatch.setattr(telegram_bot.settings.alerts, "telegram_enabled", False)
    notifier = TelegramNotifier(bot_token="TOKEN", chat_id="123")
    assert notifier.enabled is False
    assert notifier.send_text("hi") is False


def test_telegram_notifier_sends_when_enabled(monkeypatch):
    import src.notifications.telegram_bot as telegram_bot

    monkeypatch.setattr(telegram_bot.settings.alerts, "telegram_enabled", True)
    notifier = TelegramNotifier(bot_token="TOKEN", chat_id="123")

    calls = {}

    class _FakeResponse:
        def raise_for_status(self):
            pass

    def fake_post(url, json, timeout):
        calls["url"] = url
        calls["json"] = json
        return _FakeResponse()

    monkeypatch.setattr(telegram_bot.requests, "post", fake_post)

    assert notifier.send_text("hello") is True
    assert calls["json"] == {"chat_id": "123", "text": "hello"}
    assert "TOKEN" in calls["url"]


# -- telegram_bot command logic (no live bot needed) ---------------------------------------------------------

def test_settings_text_reports_channel_state():
    text = settings_text()
    assert "Telegram alerts:" in text
    assert "Discord alerts:" in text
    assert "Email alerts:" in text


def test_status_text_reports_open_signals_from_db(tmp_path, monkeypatch):
    from src.data.database import Database

    db = Database(db_path=str(tmp_path / "test.db"))
    db.insert_signal(_signal(symbol="AAPL"))
    closed = _signal(symbol="MSFT")
    signal_id = db.insert_signal(closed)
    # Mark the MSFT signal closed so it should NOT show up in /status.
    with db._connect() as conn:
        conn.execute("UPDATE signals SET status = 'hit_tp' WHERE id = ?", (signal_id,))

    # status_text() does `from src.data.database import Database` locally,
    # so patching the module attribute redirects that lookup at call time.
    import src.data.database as database_module

    monkeypatch.setattr(database_module, "Database", lambda: db)

    text = status_text()
    assert "AAPL" in text
    assert "MSFT" not in text


# -- NotificationQueue ---------------------------------------------------------

class _FakeChannel:
    def __init__(self, enabled=True, fail_times=0):
        self.enabled = enabled
        self.fail_times = fail_times
        self.calls: list[str] = []

    def send_text(self, content: str) -> bool:
        self.calls.append(content)
        if self.fail_times > 0:
            self.fail_times -= 1
            return False
        return True


class _OtherFakeChannel(_FakeChannel):
    """Distinct class so its dispatch-result key doesn't collide with
    `_FakeChannel`'s (results are keyed by class name)."""


def test_notification_queue_dispatches_to_enabled_channels_only():
    enabled_channel = _FakeChannel(enabled=True)
    disabled_channel = _OtherFakeChannel(enabled=False)
    nq = NotificationQueue(channels=[enabled_channel, disabled_channel], backoff_base_seconds=0)

    results = nq.dispatch("hello")

    assert enabled_channel.calls == ["hello"]
    assert disabled_channel.calls == []
    assert results[type(enabled_channel).__name__] is True
    assert type(disabled_channel).__name__ not in results


def test_notification_queue_retries_before_succeeding():
    flaky = _FakeChannel(enabled=True, fail_times=2)
    nq = NotificationQueue(channels=[flaky], max_retries=3, backoff_base_seconds=0)

    results = nq.dispatch("retry me")

    assert results[type(flaky).__name__] is True
    assert len(flaky.calls) == 3  # failed twice, succeeded on the 3rd


def test_notification_queue_gives_up_after_max_retries():
    always_fails = _FakeChannel(enabled=True, fail_times=99)
    nq = NotificationQueue(channels=[always_fails], max_retries=2, backoff_base_seconds=0)

    results = nq.dispatch("never works")

    assert results[type(always_fails).__name__] is False
    assert len(always_fails.calls) == 2


def test_notification_queue_channel_exception_is_treated_as_failure():
    class _RaisingChannel:
        enabled = True

        def send_text(self, content):
            raise RuntimeError("network exploded")

    nq = NotificationQueue(channels=[_RaisingChannel()], max_retries=1, backoff_base_seconds=0)
    results = nq.dispatch("hello")
    assert list(results.values()) == [False]


def test_notification_queue_background_thread_delivers(monkeypatch):
    channel = _FakeChannel(enabled=True)
    nq = NotificationQueue(channels=[channel], backoff_base_seconds=0)

    nq.start()
    try:
        nq.enqueue("async hello")
        nq.flush()
        assert channel.calls == ["async hello"]
    finally:
        nq.stop()
