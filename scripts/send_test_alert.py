"""Send a one-off test message through every configured alert channel.

Standalone from the scan pipeline (no data fetch involved) - just exercises
`NotificationQueue.dispatch()` against whichever of Telegram/Discord/Email
have real secrets configured, so you can confirm delivery actually works
without waiting for a live BUY signal.

Usage:
    python scripts/send_test_alert.py

Exits 0 if at least one enabled channel delivered successfully (or if no
channel is enabled/configured - that's a config question, not a delivery
failure), 1 if every enabled channel failed to deliver.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Make `src` importable when run as `python scripts/send_test_alert.py` from
# the repo root (that invocation puts this file's own directory, not the
# repo root, at sys.path[0]).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.logger import get_logger, setup_logging  # noqa: E402
from src.scheduler import default_notification_queue  # noqa: E402

logger = get_logger(__name__)


def main() -> int:
    setup_logging()
    queue = default_notification_queue()

    enabled = [type(c).__name__ for c in queue.channels if getattr(c, "enabled", False)]
    if not enabled:
        logger.warning(
            "No alert channel is enabled+configured (check config/alerts.yaml "
            "toggles and the TELEGRAM_*/DISCORD_*/EMAIL_* secrets/env vars); "
            "nothing to test."
        )
        return 0
    logger.info("Enabled channels: %s", ", ".join(enabled))

    message = (
        "\U0001f9ea Test alert from Trading-Bot-python\n"
        f"Sent at {datetime.now(timezone.utc).isoformat(timespec='seconds')} "
        "- if you can read this, delivery on this channel works."
    )
    results = queue.dispatch(message)
    for channel_name, success in results.items():
        logger.info("%s: %s", channel_name, "delivered" if success else "FAILED")

    if results and not any(results.values()):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
