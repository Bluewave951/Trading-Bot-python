"""Discord webhook alerts.

Uses an incoming webhook (`DISCORD_WEBHOOK_URL`), not the full `discord.py`
bot framework — a webhook is enough for one-way alert delivery and needs no
running bot process, matching how this channel is actually used (see
`notification_queue.NotificationQueue`).
"""
from __future__ import annotations

import requests

from src.config import settings
from src.logger import get_logger

logger = get_logger(__name__)

_TIMEOUT_SECONDS = 10


class DiscordNotifier:
    """Sends text alerts to a Discord channel via an incoming webhook."""

    def __init__(self, webhook_url: str | None = None) -> None:
        self.webhook_url = webhook_url if webhook_url is not None else settings.discord_webhook_url

    @property
    def enabled(self) -> bool:
        return bool(settings.alerts.discord_enabled and self.webhook_url)

    def send_text(self, content: str) -> bool:
        """POST `content` to the webhook. Returns False (never raises) on
        misconfiguration or delivery failure so callers/queues can retry or
        move on without a channel outage taking down the whole bot.
        Checks `enabled` itself, so it's always safe to call directly
        without a caller separately gating on `alerts.discord_enabled`."""
        if not self.enabled:
            logger.warning("Discord alerts disabled or webhook not configured; skipping")
            return False
        try:
            resp = requests.post(
                self.webhook_url, json={"content": content}, timeout=_TIMEOUT_SECONDS
            )
            resp.raise_for_status()
            return True
        except requests.RequestException:
            logger.exception("Failed to send Discord alert")
            return False
