"""SMTP delivery for locally persisted MotivationOS reports."""

import os
import smtplib
import ssl
from email.message import EmailMessage


class EmailDeliveryError(RuntimeError):
    """Raised when configured SMTP delivery fails without exposing credentials."""


class SMTPEmailService:
    """Send a plain-text daily report using environment-only SMTP configuration."""

    @staticmethod
    def missing_configuration() -> list[str]:
        required = ("SMTP_HOST", "EMAIL_FROM", "EMAIL_TO")
        missing = [name for name in required if not os.getenv(name, "").strip()]
        has_username = bool(os.getenv("SMTP_USERNAME", "").strip())
        has_password = bool(os.getenv("SMTP_PASSWORD", ""))
        if has_username != has_password:
            missing.append("SMTP_USERNAME and SMTP_PASSWORD must both be configured")
        return missing

    def send(self, *, subject: str, body: str) -> dict[str, str | bool]:
        missing = self.missing_configuration()
        if missing:
            return {"sent": False, "status": "not_configured", "missing_configuration": ", ".join(missing)}
        message = EmailMessage()
        message["From"] = os.environ["EMAIL_FROM"].strip()
        recipients = [item.strip() for item in os.environ["EMAIL_TO"].split(",") if item.strip()]
        if not recipients:
            return {"sent": False, "status": "not_configured", "missing_configuration": "EMAIL_TO"}
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject
        message.set_content(body)
        host = os.environ["SMTP_HOST"].strip()
        try:
            port = int(os.getenv("SMTP_PORT", "587"))
        except ValueError as error:
            raise EmailDeliveryError("SMTP_PORT must be a valid port number.") from error
        username = os.getenv("SMTP_USERNAME", "").strip()
        password = os.getenv("SMTP_PASSWORD", "")
        timeout = 20
        try:
            if port == 465:
                with smtplib.SMTP_SSL(
                    host, port, timeout=timeout, context=ssl.create_default_context()
                ) as server:
                    if username:
                        server.login(username, password)
                    refused = server.send_message(message, to_addrs=recipients)
            else:
                with smtplib.SMTP(host, port, timeout=timeout) as server:
                    server.ehlo()
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                    if username:
                        server.login(username, password)
                    refused = server.send_message(message, to_addrs=recipients)
        except Exception as error:
            raise EmailDeliveryError(
                f"SMTP delivery failed ({type(error).__name__}); inspect provider settings and retry safely."
            ) from error
        if refused:
            raise EmailDeliveryError("SMTP server rejected one or more report recipients.")
        return {"sent": True, "status": "sent"}