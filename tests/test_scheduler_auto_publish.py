import pytest

from app.reporting.scheduler import build_scheduler, run_scheduled_auto_publish


class _AutoService:
    def __init__(self):
        self.calls = 0

    def run(self):
        self.calls += 1
        return {"uploads_attempted": 1, "uploads_succeeded": 1}


def test_scheduler_claim_survives_duplicate_invocation(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("YOUTUBE_PRIVACY_STATUS", "public")
    service = _AutoService()
    database = str(tmp_path / "scheduler.sqlite3")

    first = run_scheduled_auto_publish(database_path=database, auto_publish_service=service)
    second = run_scheduled_auto_publish(database_path=database, auto_publish_service=service)

    assert first["status"] == "completed"
    assert first["result"]["uploads_succeeded"] == 1
    assert second["status"] == "already_claimed_or_completed"
    assert service.calls == 1


def test_auto_cron_is_registered_only_when_enabled(monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("YOUTUBE_PRIVACY_STATUS", "public")
    monkeypatch.setenv("AUTO_PUBLISH_CRON", "15 7 * * *")
    scheduler = build_scheduler()

    job = scheduler.get_job("youtube-auto-publish")

    assert job is not None
    assert "15" in str(job.trigger)
    assert "7" in str(job.trigger)
    scheduler.start()
    scheduler.shutdown(wait=False)


def test_invalid_auto_cron_fails_scheduler_startup(monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("YOUTUBE_PRIVACY_STATUS", "public")
    monkeypatch.setenv("AUTO_PUBLISH_CRON", "not a cron")

    with pytest.raises(ValueError, match="AUTO_PUBLISH_CRON"):
        build_scheduler()
