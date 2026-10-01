"""Offline coverage for YouTube publishing, analytics, reports, and access control."""

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from google.oauth2.credentials import Credentials

from app.api import create_app
from app.database.db import create_production, update_production
from app.reporting.daily_report import DailyReportService
from app.reporting.email_service import EmailDeliveryError, SMTPEmailService
from app.youtube.analytics import YouTubeAnalyticsService
from app.youtube.auth import GoogleOAuthService, YouTubeAuthError
from app.youtube.publisher import YouTubePublishError, YouTubePublisher
from app.youtube.repository import (
    approve_production,
    claim_job,
    claim_upload,
    get_analytics,
    get_encrypted_credentials,
    get_job_execution,
    get_upload,
    initialize_youtube_database,
    request_review,
    save_report,
    update_upload_progress,
)


def make_completed_production(database_path: Path, output_root: Path) -> tuple[str, Path]:
    production_id = "production-test-1"
    output_root.mkdir(parents=True, exist_ok=True)
    video_path = output_root / "final.mp4"
    video_path.write_bytes(b"test-only MP4 fixture")
    create_production(production_id, 1, "Test topic", output_root, database_path=database_path)
    review_decision = {
        "decision": "approve",
        "reviewer": "test-production-reviewer",
        "confirmed": True,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    production_metadata = {
        "video": {"path": str(video_path)},
        "audio_qa": {"technical_status": "passed", "findings": []},
        "video_qa": {"technical_status": "passed", "findings": []},
        "review_decision": review_decision,
    }
    update_production(production_id, "completed", production_metadata, database_path=database_path)
    artifact = {
        "production_id": production_id,
        "status": "completed",
        "metadata": {
            **production_metadata,
            "youtube_metadata": {
                "title": "A Small Step",
                "description": "A short original test description.",
                "hashtags": ["#Discipline"],
                "tags": ["discipline", "study"],
                "language": "hi-IN",
                "category_id": "22",
                "publishing_notes": "Test preview only.",
            }
        },
    }
    (output_root / "metadata.json").write_text(json.dumps(artifact), encoding="utf-8")
    return production_id, video_path


def test_review_approval_and_upload_claim_are_idempotent(tmp_path) -> None:
    database_path = tmp_path / "app.sqlite3"
    production_id, _ = make_completed_production(database_path, tmp_path / "production")
    initialize_youtube_database(database_path)

    pending = request_review(
        production_id,
        "private",
        {"title": "A Small Step"},
        database_path,
    )
    assert pending["state"] == "awaiting_review"
    assert pending["approved_at"] is None
    with pytest.raises(ValueError, match="approval"):
        claim_upload(production_id, database_path)

    approval = approve_production(
        production_id, "private", {"title": "A Small Step"}, database_path
    )
    assert approval["approved_at"]
    claimed = claim_upload(production_id, database_path)
    assert claimed["status"] == "uploading"
    update_upload_progress(
        production_id,
        status="uploading",
        resumable_uri="https://upload.example/session/opaque",
        database_path=database_path,
    )
    update_upload_progress(
        production_id,
        status="failed",
        error="temporary interruption",
        database_path=database_path,
    )
    resumed = claim_upload(production_id, database_path)
    assert resumed["attempt_count"] == 2
    assert resumed["resumable_uri"] == "https://upload.example/session/opaque"
    update_upload_progress(
        production_id,
        status="uploaded_private",
        video_id="real-response-id-fixture",
        api_response={"id": "real-response-id-fixture"},
        database_path=database_path,
    )
    with pytest.raises(ValueError, match="uploaded_private"):
        claim_upload(production_id, database_path)


def test_failed_upload_without_session_cannot_be_restarted(tmp_path) -> None:
    database_path = tmp_path / "app.sqlite3"
    production_id, _ = make_completed_production(database_path, tmp_path / "production")
    request_review(production_id, "private", {"title": "reviewed"}, database_path)
    approve_production(production_id, "private", {"title": "reviewed"}, database_path)
    claim_upload(production_id, database_path)
    update_upload_progress(
        production_id,
        status="failed",
        error="no session returned",
        database_path=database_path,
    )
    with pytest.raises(ValueError, match="blocked"):
        claim_upload(production_id, database_path)


def test_google_credentials_are_encrypted_and_status_is_safe(tmp_path, monkeypatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("GOOGLE_TOKEN_ENCRYPTION_KEY", key)
    oauth = GoogleOAuthService(str(tmp_path / "app.sqlite3"))
    credentials = Credentials(
        token="access-token-test-value",
        refresh_token="refresh-token-test-value",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="client-id",
        client_secret="client-secret",
        expiry=datetime.now(timezone.utc) + timedelta(days=1),
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )

    oauth._save_credentials(credentials)
    saved = oauth.status()
    raw_row = oauth._fernet().decrypt(
        get_encrypted_credentials(str(tmp_path / "app.sqlite3"))["encrypted_token"].encode()
    ).decode()

    assert saved["status"] == "connected"
    assert "access-token-test-value" not in str(saved)
    assert "access-token-test-value" in raw_row
    assert oauth.authorized_credentials().valid


def test_oauth_start_uses_pkce_and_callback_state_is_single_use(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret-fixture")
    monkeypatch.setenv("GOOGLE_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    oauth = GoogleOAuthService(str(tmp_path / "app.sqlite3"))

    result = oauth.start_authorization()

    assert result["expires_in_seconds"] == "600"
    assert "code_challenge_method=S256" in result["authorization_url"]
    assert "access_type=offline" in result["authorization_url"]
    assert "client-secret-fixture" not in result["authorization_url"]
    with pytest.raises(YouTubeAuthError, match="invalid, expired, or already used"):
        oauth.complete_authorization(state="not-a-live-state", code="never-exchanged")


def test_new_oauth_flow_cleans_only_expired_ephemeral_state(tmp_path) -> None:
    from app.youtube.repository import create_oauth_state

    database_path = tmp_path / "app.sqlite3"
    create_oauth_state("expired-hash", "encrypted-verifier", "2000-01-01T00:00:00+00:00", database_path)
    create_oauth_state("active-hash", "active-verifier", "2999-01-01T00:00:00+00:00", database_path)
    create_oauth_state("new-hash", "new-verifier", "2999-01-01T00:00:00+00:00", database_path)

    with sqlite3.connect(database_path) as connection:
        states = {
            row[0]
            for row in connection.execute("SELECT state_hash FROM youtube_oauth_states")
        }

    assert states == {"active-hash", "new-hash"}


def test_analytics_persists_only_returned_metrics(tmp_path) -> None:
    class Reports:
        def query(self, **kwargs):
            self.kwargs = kwargs
            return self

        def execute(self, **kwargs):
            return {
                "columnHeaders": [
                    {"name": "day"},
                    {"name": "views"},
                    {"name": "likes"},
                    {"name": "comments"},
                ],
                "rows": [["2026-09-29", 41, 6, 1]],
            }

    reports = Reports()
    service = YouTubeAnalyticsService(
        tmp_path / "app.sqlite3",
        oauth=SimpleNamespace(authorized_credentials=lambda: object()),
        api_builder=lambda credentials: SimpleNamespace(reports=lambda: reports),
    )
    response = service.video_report(
        "video-id-fixture", "2026-09-29", "2026-09-29"
    )

    assert reports.kwargs["ids"] == "channel==MINE"
    assert reports.kwargs["filters"] == "video==video-id-fixture"
    assert response["metrics_available"] is True
    assert response["rows"] == [
        {"date": "2026-09-29", "metrics": {"views": 41.0, "likes": 6.0, "comments": 1.0}}
    ]
    stored = get_analytics(video_id="video-id-fixture", database_path=tmp_path / "app.sqlite3")
    assert {row["metric_name"]: row["metric_value"] for row in stored} == {
        "views": 41.0,
        "likes": 6.0,
        "comments": 1.0,
    }


def test_report_is_saved_locally_when_email_is_unconfigured(tmp_path, monkeypatch) -> None:
    for name in ("SMTP_HOST", "EMAIL_FROM", "EMAIL_TO"):
        monkeypatch.delenv(name, raising=False)

    class Analytics:
        def channel_report(self, start_date, end_date):
            return {
                "retrieved": True,
                "metrics_available": True,
                "rows": [{"date": end_date, "metrics": {"views": 8}}],
            }

        def collect_uploaded_videos(self, start_date, end_date):
            return []

    reports = DailyReportService(
        tmp_path / "app.sqlite3",
        tmp_path / "reports",
        analytics=Analytics(),
    )
    result = reports.run(date(2026, 9, 30))

    assert Path(result["report_path"]).is_file()
    assert "views" in Path(result["report_path"]).read_text(encoding="utf-8")
    assert result["email"]["status"] == "not_configured"
    assert result["report"]["analytics"]["channel"]["rows"][0]["metrics"]["views"] == 8


def test_uncertain_email_send_is_not_automatically_repeated(tmp_path) -> None:
    class Analytics:
        def channel_report(self, start_date, end_date):
            return {"retrieved": True, "metrics_available": False, "rows": []}

        def collect_uploaded_videos(self, start_date, end_date):
            return []

    class AmbiguousEmail:
        calls = 0

        def send(self, **kwargs):
            self.calls += 1
            raise EmailDeliveryError("SMTP outcome unknown.")

    email = AmbiguousEmail()
    service = DailyReportService(
        tmp_path / "app.sqlite3",
        tmp_path / "reports",
        analytics=Analytics(),
        email=email,
    )
    service.run(date(2026, 9, 30))
    second = service.run(date(2026, 9, 30))

    assert email.calls == 1
    assert second["email"]["status"] == "delivery_unknown"


def test_smtp_delivery_requires_tls_and_hides_authentication_errors(monkeypatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "mail.example.invalid")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "reporter")
    monkeypatch.setenv("SMTP_PASSWORD", "mail-secret-fixture")
    monkeypatch.setenv("EMAIL_FROM", "reports@example.invalid")
    monkeypatch.setenv("EMAIL_TO", "owner@example.invalid")
    events = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            events.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def ehlo(self):
            events.append(("ehlo",))

        def starttls(self, context):
            events.append(("starttls", context))

        def login(self, username, password):
            events.append(("login", username))
            raise RuntimeError("mail-secret-fixture rejected")

    monkeypatch.setattr("app.reporting.email_service.smtplib.SMTP", FakeSMTP)

    with pytest.raises(EmailDeliveryError) as error:
        SMTPEmailService().send(subject="Daily report", body="Actual report body")

    assert [event[0] for event in events] == ["connect", "ehlo", "starttls", "ehlo", "login"]
    assert "mail-secret-fixture" not in str(error.value)


def test_job_claim_and_report_history_are_durable(tmp_path) -> None:
    database_path = tmp_path / "app.sqlite3"
    assert claim_job("daily", "2026-09-30", database_path, max_attempts=2)
    assert not claim_job("daily", "2026-09-30", database_path, max_attempts=2)
    from app.youtube.repository import finish_job

    finish_job("daily", "2026-09-30", status="failed", error="offline", database_path=database_path)
    assert claim_job("daily", "2026-09-30", database_path, max_attempts=2)
    finish_job("daily", "2026-09-30", status="completed", database_path=database_path)
    assert get_job_execution("daily", "2026-09-30", database_path)["attempts"] == 2
    saved = save_report(
        "2026-09-30", "report.md", {"views": 8}, "sent", database_path=database_path
    )
    assert saved["report"]["views"] == 8
    assert saved["email_status"] == "sent"


def test_publisher_rejects_missing_approval_before_any_api_call(tmp_path) -> None:
    database_path = tmp_path / "app.sqlite3"
    production_id, _ = make_completed_production(database_path, tmp_path / "production")
    api_calls = []
    publisher = YouTubePublisher(
        database_path,
        oauth=SimpleNamespace(authorized_credentials=lambda: object()),
        api_builder=lambda credentials: api_calls.append(credentials),
        video_validator=lambda path: {"valid": True},
    )

    with pytest.raises(YouTubePublishError, match="approval"):
        publisher.upload(production_id)
    assert api_calls == []
    assert get_upload(production_id, database_path) is None


@pytest.mark.parametrize(
    "invalid_metadata",
    [
        {"review_decision": None},
        {"review_decision": {"decision": "approve", "confirmed": True}},
        {"review_decision": {"decision": "approve", "reviewer": "reviewer", "confirmed": True}},
        {"audio_qa": {"technical_status": "failed"}},
        {"audio_qa": {"technical_status": "needs_review"}},
        {"video_qa": {"technical_status": "failed"}},
        {"video_qa": None},
        {"audio_qa": None},
    ],
)
def test_publishing_review_rejects_ineligible_production_before_clients(
    tmp_path, invalid_metadata
) -> None:
    database_path = tmp_path / "app.sqlite3"
    production_id, _ = make_completed_production(database_path, tmp_path / "production")
    from app.database.db import get_production_by_id

    production = get_production_by_id(production_id, database_path)
    production["metadata"].update(invalid_metadata)
    update_production(production_id, "completed", production["metadata"], database_path=database_path)
    calls = []
    publisher = YouTubePublisher(
        database_path,
        oauth=SimpleNamespace(authorized_credentials=lambda: calls.append("oauth")),
        api_builder=lambda credentials: calls.append("api"),
        media_upload_factory=lambda path: calls.append("upload"),
    )

    with pytest.raises(YouTubePublishError):
        publisher.request_review(production_id)

    with pytest.raises(YouTubePublishError):
        publisher.upload(production_id)
    assert calls == []
    assert get_upload(production_id, database_path) is None


def test_publishing_review_rejects_duplicate_active_record(tmp_path) -> None:
    database_path = tmp_path / "app.sqlite3"
    production_id, _ = make_completed_production(database_path, tmp_path / "production")
    publisher = YouTubePublisher(database_path)
    publisher.request_review(production_id)

    with pytest.raises(YouTubePublishError, match="already exists"):
        publisher.request_review(production_id)

    assert get_upload(production_id, database_path)["status"] == "awaiting_review"


def test_publishing_review_accepts_separately_reviewed_audio(tmp_path) -> None:
    database_path = tmp_path / "app.sqlite3"
    production_id, _ = make_completed_production(database_path, tmp_path / "production")
    from app.database.db import get_production_by_id

    production = get_production_by_id(production_id, database_path)
    production["metadata"]["audio_qa"] = {"technical_status": "needs_review", "findings": []}
    production["metadata"]["audio_review_decision"] = {
        "decision": "approve",
        "reviewer": "test-audio-reviewer",
        "confirmed": True,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    update_production(production_id, "completed", production["metadata"], database_path=database_path)
    publisher = YouTubePublisher(database_path)

    result = publisher.request_review(production_id)

    assert result["approval"]["state"] == "awaiting_review"


def test_publishing_rejects_missing_or_ineligible_production_before_clients(tmp_path) -> None:
    database_path = tmp_path / "app.sqlite3"
    production_id, _ = make_completed_production(database_path, tmp_path / "production")
    from app.database.db import get_production_by_id

    production = get_production_by_id(production_id, database_path)
    calls = []
    publisher = YouTubePublisher(
        database_path,
        oauth=SimpleNamespace(authorized_credentials=lambda: calls.append("oauth")),
        api_builder=lambda credentials: calls.append("api"),
        media_upload_factory=lambda path: calls.append("upload"),
    )
    update_production(production_id, "needs_review", production["metadata"], database_path=database_path)

    with pytest.raises(YouTubePublishError, match="Only completed"):
        publisher.request_review(production_id)
    with pytest.raises(YouTubePublishError, match="not found"):
        publisher.request_review("missing-production")

    assert calls == []
    assert get_upload(production_id, database_path) is None


def test_upload_rechecks_production_approval_before_any_upload_method(tmp_path) -> None:
    database_path = tmp_path / "app.sqlite3"
    production_id, _ = make_completed_production(database_path, tmp_path / "production")
    calls = []

    class OAuthFixture:
        def authorized_credentials(self):
            calls.append("oauth")
            return object()

    publisher = YouTubePublisher(
        database_path,
        oauth=OAuthFixture(),
        api_builder=lambda credentials: calls.append("api"),
        media_upload_factory=lambda path: calls.append("upload"),
        video_validator=lambda path: calls.append("validate"),
    )
    publisher.request_review(production_id)
    publisher.approve(production_id)
    from app.database.db import get_production_by_id

    production = get_production_by_id(production_id, database_path)
    production["metadata"].pop("review_decision")
    update_production(production_id, "completed", production["metadata"], database_path=database_path)

    with pytest.raises(YouTubePublishError, match="production-review approval"):
        publisher.upload(production_id)

    assert calls == []


def test_confirmed_upload_persists_actual_response_and_visibility(tmp_path) -> None:
    database_path = tmp_path / "app.sqlite3"
    production_id, _ = make_completed_production(database_path, tmp_path / "production")
    cipher = Fernet.generate_key()

    class OAuthFixture:
        def authorized_credentials(self):
            return object()

        def encrypt_persistent_secret(self, value):
            return Fernet(cipher).encrypt(value.encode()).decode()

        def decrypt_persistent_secret(self, value):
            return Fernet(cipher).decrypt(value.encode()).decode()

    class UploadRequest:
        resumable_uri = None

        def next_chunk(self, num_retries):
            self.resumable_uri = "https://upload.example/session/test"
            return SimpleNamespace(progress=lambda: 1.0), {
                "id": "youtube-response-id",
                "status": {"privacyStatus": "private"},
            }

    class Videos:
        body = None

        def insert(self, **kwargs):
            self.body = kwargs["body"]
            return UploadRequest()

    videos = Videos()
    publisher = YouTubePublisher(
        database_path,
        oauth=OAuthFixture(),
        api_builder=lambda credentials: SimpleNamespace(videos=lambda: videos),
        media_upload_factory=lambda path: object(),
        video_validator=lambda path: {"valid": True},
    )
    publisher.request_review(production_id)
    publisher.approve(production_id, confirm_public=False)

    uploaded = publisher.upload(production_id)

    assert videos.body["status"]["privacyStatus"] == "private"
    assert uploaded["status"] == "uploaded_private"
    assert uploaded["video_id"] == "youtube-response-id"
    assert uploaded["api_response"]["id"] == "youtube-response-id"
    assert get_upload(production_id, database_path)["attempt_count"] == 1
    assert get_upload(production_id, database_path)["resumable_uri"] != "https://upload.example/session/test"


def test_failed_upload_resumes_saved_session_without_duplicate_insert(tmp_path) -> None:
    database_path = tmp_path / "app.sqlite3"
    production_id, _ = make_completed_production(database_path, tmp_path / "production")
    cipher = Fernet(Fernet.generate_key())

    class OAuthFixture:
        def authorized_credentials(self):
            return object()

        def encrypt_persistent_secret(self, value):
            return cipher.encrypt(value.encode()).decode()

        def decrypt_persistent_secret(self, value):
            return cipher.decrypt(value.encode()).decode()

    class UploadRequest:
        def __init__(self, fail: bool) -> None:
            self.fail = fail
            self.resumable_uri = None

        def next_chunk(self, num_retries):
            if self.fail:
                self.resumable_uri = "https://upload.example/session/retry-fixture"
                raise RuntimeError("simulated interruption")
            assert self.resumable_uri == "https://upload.example/session/retry-fixture"
            return SimpleNamespace(progress=lambda: 1.0), {
                "id": "youtube-resumed-id",
                "status": {"privacyStatus": "private"},
            }

    requests = [UploadRequest(fail=True), UploadRequest(fail=False)]
    inserts = []

    class Videos:
        def insert(self, **kwargs):
            inserts.append(kwargs)
            return requests.pop(0)

    publisher = YouTubePublisher(
        database_path,
        oauth=OAuthFixture(),
        api_builder=lambda credentials: SimpleNamespace(videos=lambda: Videos()),
        media_upload_factory=lambda path: object(),
        video_validator=lambda path: {"valid": True},
    )
    publisher.request_review(production_id)
    publisher.approve(production_id)

    with pytest.raises(YouTubePublishError, match="network or API error"):
        publisher.upload(production_id)
    failed = get_upload(production_id, database_path)
    assert failed["status"] == "failed"
    assert failed["resumable_uri"]
    assert "retry-fixture" not in failed["resumable_uri"]

    uploaded = publisher.upload(production_id)

    assert uploaded["status"] == "uploaded_private"
    assert uploaded["video_id"] == "youtube-resumed-id"
    assert uploaded["attempt_count"] == 2
    assert len(inserts) == 2


def test_ambiguous_upload_reconciles_by_production_reference(tmp_path) -> None:
    database_path = tmp_path / "app.sqlite3"
    production_id, _ = make_completed_production(database_path, tmp_path / "production")
    request_review(production_id, "private", {"title": "reviewed"}, database_path)
    approve_production(production_id, "private", {"title": "reviewed"}, database_path)
    claim_upload(production_id, database_path)
    update_upload_progress(
        production_id,
        status="failed",
        error="connection lost before response persisted",
        database_path=database_path,
    )
    service_calls = []

    class Search:
        def list(self, **kwargs):
            service_calls.append(("search", kwargs))
            return self

        def execute(self, **kwargs):
            return {"items": [{"id": {"videoId": "youtube-found-id"}}]}

    class Videos:
        def list(self, **kwargs):
            service_calls.append(("videos", kwargs))
            return self

        def execute(self, **kwargs):
            return {
                "items": [
                    {
                        "id": "youtube-found-id",
                        "snippet": {
                            "description": f"MotivationOS production reference: {production_id}"
                        },
                        "status": {"privacyStatus": "private"},
                    }
                ]
            }

    publisher = YouTubePublisher(
        database_path,
        oauth=SimpleNamespace(authorized_credentials=lambda: object()),
        api_builder=lambda credentials: SimpleNamespace(search=lambda: Search(), videos=lambda: Videos()),
    )

    result = publisher.reconcile(production_id)

    assert result == {
        "production_id": production_id,
        "status": "uploaded_private",
        "video_id": "youtube-found-id",
        "reconciled": True,
    }
    assert service_calls[0][1]["forMine"] is True
    assert get_upload(production_id, database_path)["video_id"] == "youtube-found-id"


def test_admin_api_requires_bearer_token_and_never_returns_oauth_tokens(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOTIVATIONOS_ADMIN_TOKEN", "admin-test-token")

    class OAuthFixture:
        def status(self):
            return {"status": "disconnected", "configured": True, "missing_configuration": []}

    app = create_app(tmp_path / "app.sqlite3", oauth=OAuthFixture())
    with TestClient(app) as client:
        assert client.get("/api/youtube/status").status_code == 401
        response = client.get(
            "/api/youtube/status", headers={"Authorization": "Bearer admin-test-token"}
        )

    assert response.status_code == 200
    assert "token" not in response.text.lower()