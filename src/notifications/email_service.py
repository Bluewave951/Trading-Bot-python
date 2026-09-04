"""SMTP email alerts.

Chart-screenshot attachment (`alerts.yaml`'s `email.send_chart_screenshot`)
is not implemented — Phase 6 (`src/web/`) doesn't yet render chart images
to attach. `send_text`/`send_html` work today; wire in an attachment path
once that exists.
"""
from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.config import settings
from src.logger import get_logger

logger = get_logger(__name__)

_TIMEOUT_SECONDS = 10
_DEFAULT_SUBJECT = "Trading Bot Alert"


class EmailNotifier:
    """Sends alert emails via SMTP (e.g. Gmail) using settings from `.env`."""

    def __init__(
        self,
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        to_address: str | None = None,
    ) -> None:
        self.smtp_host = smtp_host if smtp_host is not None else settings.email_smtp_host
        self.smtp_port = smtp_port if smtp_port is not None else settings.email_smtp_port
        self.username = username if username is not None else settings.email_username
        self.password = password if password is not None else settings.email_password
        # Alerts default to mailing the same account that sends them.
        self.to_address = to_address if to_address is not None else self.username

    @property
    def enabled(self) -> bool:
        return bool(
            settings.alerts.email_enabled
            and self.smtp_host
            and self.username
            and self.password
            and self.to_address
        )

    def send_text(self, content: str, subject: str = _DEFAULT_SUBJECT) -> bool:
        """Send a plain-text alert email. Returns False (never raises) on
        misconfiguration or delivery failure so callers/queues can retry or
        move on without a channel outage taking down the whole bot."""
        if not self.enabled:
            logger.warning("Email alerts disabled or not fully configured; skipping")
            return False

        message = MIMEMultipart()
        message["From"] = self.username
        message["To"] = self.to_address
        message["Subject"] = subject
        message.attach(MIMEText(content, "plain"))

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=_TIMEOUT_SECONDS) as server:
                server.starttls()
                server.login(self.username, self.password)
                server.send_message(message)
            return True
        except (smtplib.SMTPException, OSError):
            logger.exception("Failed to send email alert")
            return False
