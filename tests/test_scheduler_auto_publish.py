from app.reporting.scheduler import run_scheduled_auto_publish


class _AutoService:
    def __init__(self):
        self.calls = 0

    def run(self):
        self.calls += 1
        return {"uploads_attempted": 1, "uploads_succeeded": 1}


def test_scheduler_claim_survives_duplicate_invocation(tmp_path):
    service = _AutoService()
    database = str(tmp_path / "scheduler.sqlite3")

    first = run_scheduled_auto_publish(database_path=database, auto_publish_service=service)
    second = run_scheduled_auto_publish(database_path=database, auto_publish_service=service)

    assert first["status"] == "completed"
    assert first["result"]["uploads_succeeded"] == 1
    assert second["status"] == "already_claimed_or_completed"
    assert service.calls == 1