"""Safe Gmail SMTP delivery for MotivationOS reports."""

import logging
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from html.parser import HTMLParser

from app.config import (
    get_report_email_to, get_smtp_host, get_smtp_password, get_smtp_port,
    get_smtp_user,
)

logger = logging.getLogger(__name__)


class EmailDeliveryError(RuntimeError):
    """Raised when configured SMTP delivery fails without exposing credentials."""


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def send_email(subject: str, html_body: str, to: str | None = None) -> bool:
    """Send multipart text/HTML email over authenticated Gmail STARTTLS."""
    recipient = to or get_report_email_to()
    logger.info("Email send attempt timestamp=%s recipient=%s", __import__("datetime").datetime.now().astimezone().isoformat(), recipient)
    password = get_smtp_password()
    if not password:
        logger.warning("Report email not sent: SMTP_PASSWORD is empty (recipient=%s).", recipient)
        raise EmailDeliveryError("SMTP_PASSWORD is empty; set a Gmail App Password to send reports.")
    username = get_smtp_user()
    if not username:
        raise EmailDeliveryError("SMTP_USER is empty; configure the Gmail account for report delivery.")
    message = EmailMessage()
    message["From"] = f"{os.getenv('SMTP_FROM_NAME', 'MotivationOS').strip()} <{username}>"
    message["To"] = recipient
    message["Subject"] = subject
    parser = _HTMLText()
    parser.feed(html_body)
    message.set_content(re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip())
    message.add_alternative(html_body, subtype="html")
    try:
        with smtplib.SMTP(get_smtp_host(), get_smtp_port(), timeout=20) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(username, password)
            server.send_message(message)
    except Exception as error:
        # SMTP exception text can echo credentials or server commands; keep it out of logs.
        logger.error("Email send failed timestamp=%s recipient=%s error_type=%s", __import__("datetime").datetime.now().astimezone().isoformat(), recipient, type(error).__name__)
        raise EmailDeliveryError(f"SMTP delivery failed: {type(error).__name__}.") from error
    logger.info("Email sent timestamp=%s recipient=%s", __import__("datetime").datetime.now().astimezone().isoformat(), recipient)
    return True


class SMTPEmailService:
    """Compatibility adapter for existing report API callers."""

    def send(self, *, subject: str, body: str) -> dict[str, str | bool]:
        # Preserve the previous injected plain-text configuration for callers that still use it.
        legacy_configured = any(os.getenv(key) for key in ("EMAIL_FROM", "EMAIL_TO", "SMTP_USERNAME"))
        if not os.getenv("SMTP_HOST") and not legacy_configured:
            return {"sent": False, "status": "not_configured", "missing_configuration": "SMTP_HOST"}
        legacy_missing = [key for key in ("SMTP_HOST", "EMAIL_FROM", "EMAIL_TO") if not os.getenv(key, "").strip()]
        if legacy_configured and legacy_missing:
            return {"sent": False, "status": "not_configured", "missing_configuration": ", ".join(legacy_missing)}
        if legacy_configured and os.getenv("EMAIL_FROM") and os.getenv("EMAIL_TO"):
            message = EmailMessage()
            message["From"] = os.environ["EMAIL_FROM"].strip()
            message["To"] = os.environ["EMAIL_TO"].strip()
            message["Subject"] = subject
            message.set_content(body)
            try:
                with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587")), timeout=20) as server:
                    server.ehlo()
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                    username = os.getenv("SMTP_USERNAME", "").strip()
                    password = os.getenv("SMTP_PASSWORD", "")
                    if username:
                        server.login(username, password)
                    server.send_message(message)
            except Exception as error:
                logger.error("Legacy SMTP delivery failed error_type=%s", type(error).__name__)
                raise EmailDeliveryError(f"SMTP delivery failed: {type(error).__name__}.") from error
            return {"sent": True, "status": "sent"}
        if not get_smtp_password():
            logger.warning("Report email not sent: SMTP_PASSWORD is empty.")
            return {"sent": False, "status": "not_configured", "missing_configuration": "SMTP_PASSWORD"}
        escaped = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html = f'<html><body style="font-family:Arial,sans-serif;white-space:pre-wrap">{escaped}</body></html>'
        try:
            send_email(subject, html)
        except EmailDeliveryError as error:
            return {"sent": False, "status": "failed", "detail": str(error)}
        return {"sent": True, "status": "sent"}
