"""Centralized logging setup for the trading bot.

Every module should obtain its logger via `get_logger(__name__)` rather than
calling `logging.getLogger` directly, so log format/handlers stay consistent.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(level: int = logging.INFO, log_to_file: bool = True) -> None:
    """Configure the root logger once. Safe to call multiple times."""
    global _configured
    if _configured:
        return

    # Windows consoles often default to a non-UTF-8 codepage (e.g. cp874 for
    # Thai locale), which raises UnicodeEncodeError the moment a log message
    # contains an emoji or other non-ASCII character — and several message
    # builders in this codebase do (⚠️/🟢/🔴/📊 in notifications/formatting.py,
    # level_watcher.py's alerts). Reconfigure stdout to UTF-8 with a
    # replace-on-error fallback so logging never crashes over this; the file
    # handler below is already explicitly UTF-8.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass  # stdout isn't a reconfigurable TextIOWrapper (e.g. some redirects) — best effort only

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    if log_to_file:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_DIR / "trading_bot.log",
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=5,
            encoding="utf-8",
        )
        handlers.append(file_handler)

    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
        datefmt=DATE_FORMAT,
        handlers=handlers,
    )
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger, configuring logging on first use."""
    if not _configured:
        setup_logging()
    return logging.getLogger(name)
