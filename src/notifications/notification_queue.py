"""Queue for reliable alert delivery (retries, backoff).

Fans a message out to every registered channel (`TelegramNotifier`,
`DiscordNotifier`, `EmailNotifier`, or any object with a `send_text(str) ->
bool` method and an `enabled` property), retrying each with exponential
backoff independently so a slow/down channel doesn't block the others.
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Protocol

from src.config import settings
from src.logger import get_logger

logger = get_logger(__name__)


class NotificationChannel(Protocol):
    """Structural type every notifier (`TelegramNotifier`, `DiscordNotifier`,
    `EmailNotifier`, or a test double) satisfies."""
    enabled: bool

    def send_text(self, content: str) -> bool: ...


@dataclass
class NotificationQueue:
    """Buffers alert text and dispatches it to every registered channel.

    `dispatch()` is the synchronous core (call it directly for a one-off
    send, or in tests); `enqueue()` + `start()`/`stop()` run the same logic
    on a background thread for live use.
    """
    channels: list[NotificationChannel] = field(default_factory=list)
    max_retries: int = field(default_factory=lambda: settings.alerts.delivery.max_retries)
    backoff_base_seconds: float = field(
        default_factory=lambda: settings.alerts.delivery.retry_backoff_seconds
    )
    max_queue_size: int = field(default_factory=lambda: settings.alerts.delivery.queue_max_size)

    def __post_init__(self) -> None:
        self._queue: queue.Queue[str] = queue.Queue(maxsize=self.max_queue_size)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def register(self, channel: NotificationChannel) -> None:
        self.channels.append(channel)

    # -- Synchronous API ------------------------------------------------

    def dispatch(self, message: str) -> dict[str, bool]:
        """Send `message` to every enabled channel now, retrying failures.
        Returns `{channel_class_name: success}` for the caller/tests to
        inspect (a channel's own name isn't unique across instances, but
        `NotificationQueue` doesn't expect more than one of each type)."""
        results: dict[str, bool] = {}
        for channel in self.channels:
            name = type(channel).__name__
            if not getattr(channel, "enabled", False):
                logger.debug("Skipping disabled channel %s", name)
                continue
            results[name] = self._send_with_retry(channel, message)
        return results

    def _send_with_retry(self, channel: NotificationChannel, message: str) -> bool:
        for attempt in range(1, self.max_retries + 1):
            try:
                if channel.send_text(message):
                    return True
            except Exception:
                logger.exception("Channel %s raised while sending", type(channel).__name__)
            if attempt < self.max_retries:
                time.sleep(self.backoff_base_seconds * (2 ** (attempt - 1)))
        logger.error(
            "Failed to deliver notification via %s after %d attempt(s)",
            type(channel).__name__, self.max_retries,
        )
        return False

    # -- Background-thread API ------------------------------------------------

    def enqueue(self, message: str) -> None:
        """Queue `message` for background delivery (see `start()`)."""
        self._queue.put(message)

    def start(self) -> None:
        """Start the background dispatch thread (no-op if already running)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the background thread to stop and wait for it to exit."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def flush(self) -> None:
        """Block until every currently-enqueued message has been dispatched."""
        self._queue.join()

    def _worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                message = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.dispatch(message)
            finally:
                self._queue.task_done()
