from email.message import EmailMessage

from app.reporting import email_service


def test_send_email_uses_starttls_and_sends_multipart(monkeypatch):
    monkeypatch.setattr(email_service, "get_smtp_host", lambda: "smtp.test")
    monkeypatch.setattr(email_service, "get_smtp_port", lambda: 587)
    monkeypatch.setattr(email_service, "get_smtp_user", lambda: "sender@example.test")
    monkeypatch.setattr(email_service, "get_smtp_password", lambda: "never-log-this")
    monkeypatch.setattr(email_service, "get_report_email_to", lambda: "to@example.test")
    events = []

    class SMTP:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def ehlo(self): pass
        def starttls(self, **kwargs): events.append("starttls")
        def login(self, user, password): events.append(("login", user, password))
        def send_message(self, message): events.append(message)

    monkeypatch.setattr(email_service.smtplib, "SMTP", SMTP)
    assert email_service.send_email("Subject", "<h1>Hello</h1>") is True
    assert events[0] == "starttls"
    assert events[1] == ("login", "sender@example.test", "never-log-this")
    message = events[2]
    assert message.is_multipart()
    assert {part.get_content_type() for part in message.iter_parts()} == {"text/plain", "text/html"}


def test_send_email_does_not_log_password(monkeypatch, caplog):
    monkeypatch.setattr(email_service, "get_smtp_host", lambda: "smtp.test")
    monkeypatch.setattr(email_service, "get_smtp_port", lambda: 587)
    monkeypatch.setattr(email_service, "get_smtp_user", lambda: "sender@example.test")
    monkeypatch.setattr(email_service, "get_smtp_password", lambda: "secret-password")
    monkeypatch.setattr(email_service, "get_report_email_to", lambda: "to@example.test")

    class SMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def ehlo(self): pass
        def starttls(self, **k): pass
        def login(self, *a): raise RuntimeError("secret-password")

    monkeypatch.setattr(email_service.smtplib, "SMTP", SMTP)
    try:
        email_service.send_email("Subject", "<p>Body</p>")
    except email_service.EmailDeliveryError:
        pass
    assert "secret-password" not in caplog.text
