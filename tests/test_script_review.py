"""Offline tests for explicit script review and review-state persistence."""

from datetime import datetime, timezone
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.agents.quality_agent import evaluate_script
from app.agents.script_agent import MockScriptGenerator
from app.database.db import (
    ScriptNotFoundError,
    ScriptReviewError,
    get_script_by_id,
    get_scripts_needing_review,
    review_script,
    save_script,
    create_production,
    update_production,
)
from app.schemas.script_schema import ScriptRequest
from app.api import create_app
from app.video.production_service import ProductionService


def _save_script(database_path, *, passed: bool = True):
    script = MockScriptGenerator().generate(ScriptRequest(topic="discipline"))
    evaluation = evaluate_script(script)
    if not passed:
        evaluation = evaluation.model_copy(
            update={"passed": False, "language_consistency": False}
        )
    return save_script(script, evaluation=evaluation, database_path=database_path)


def test_new_script_defaults_to_needs_review_even_when_quality_passes(tmp_path) -> None:
    record = _save_script(tmp_path / "scripts.sqlite3")

    assert record["status"] == "needs_review"
    assert record["evaluation"]["passed"] is True
    assert record["reviewed_at"] is None
    assert record["reviewed_by"] is None


def test_save_script_cannot_bypass_human_review_with_completed_status(tmp_path) -> None:
    script = MockScriptGenerator().generate(ScriptRequest(topic="discipline"))

    with pytest.raises(ValueError, match="must start in 'needs_review'"):
        save_script(script, status="completed", database_path=tmp_path / "scripts.sqlite3")


def test_failed_quality_evaluation_cannot_be_approved(tmp_path) -> None:
    database_path = tmp_path / "scripts.sqlite3"
    record = _save_script(database_path, passed=False)

    with pytest.raises(ScriptReviewError, match="must pass quality evaluation"):
        review_script(record["id"], "approved", "reviewer-test", database_path)

    assert get_script_by_id(record["id"], database_path)["status"] == "needs_review"


def test_inconsistent_passing_score_cannot_be_approved(tmp_path) -> None:
    database_path = tmp_path / "scripts.sqlite3"
    script = MockScriptGenerator().generate(ScriptRequest(topic="discipline"))
    evaluation = evaluate_script(script).model_dump(mode="json")
    evaluation.update({"quality_score": 0, "passed": True})
    record = save_script(script, evaluation=evaluation, database_path=database_path)

    with pytest.raises(ScriptReviewError, match="valid passing quality evaluation"):
        review_script(record["id"], "approved", "reviewer-test", database_path)

    assert get_script_by_id(record["id"], database_path)["status"] == "needs_review"


def test_missing_script_and_invalid_decision_are_rejected(tmp_path) -> None:
    database_path = tmp_path / "scripts.sqlite3"

    with pytest.raises(ScriptNotFoundError, match="Script not found"):
        review_script(404, "approved", "reviewer-test", database_path)
    with pytest.raises(ScriptReviewError, match="Decision must be"):
        review_script(404, "defer", "reviewer-test", database_path)


def test_script_can_be_approved_once_and_review_metadata_persists(tmp_path) -> None:
    database_path = tmp_path / "scripts.sqlite3"
    record = _save_script(database_path)

    reviewed = review_script(record["id"], "approved", "reviewer-test", database_path)
    loaded = get_script_by_id(record["id"], database_path)

    assert reviewed["status"] == "approved"
    assert loaded["status"] == "approved"
    assert loaded["reviewed_by"] == "reviewer-test"
    assert datetime.fromisoformat(loaded["reviewed_at"]).utcoffset() == timezone.utc.utcoffset(None)
    with pytest.raises(ScriptReviewError, match="Only scripts awaiting human review"):
        review_script(record["id"], "rejected", "reviewer-test", database_path)


def test_script_can_be_rejected_and_no_longer_appears_in_review_queue(tmp_path) -> None:
    database_path = tmp_path / "scripts.sqlite3"
    record = _save_script(database_path, passed=False)

    rejected = review_script(record["id"], "rejected", "reviewer-test", database_path)

    assert rejected["status"] == "rejected"
    assert rejected["reviewed_by"] == "reviewer-test"
    assert get_scripts_needing_review(database_path) == []


def test_review_queue_returns_passing_scripts_without_approving_them(tmp_path) -> None:
    database_path = tmp_path / "scripts.sqlite3"
    record = _save_script(database_path)

    queue = get_scripts_needing_review(database_path)

    assert [item["id"] for item in queue] == [record["id"]]
    assert queue[0]["status"] == "needs_review"
    assert queue[0]["evaluation"]["passed"] is True


def test_authenticated_review_api_requires_confirmation_and_keeps_approval_separate(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "scripts.sqlite3"
    record = _save_script(database_path)
    rejected_record = _save_script(database_path, passed=False)
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-token")
    monkeypatch.setenv("MOTIVATIONOS_SCHEDULER_ENABLED", "false")
    client = TestClient(create_app(database_path))
    headers = {"Authorization": "Bearer test-admin-token"}

    with client:
        assert client.get("/api/scripts/review").status_code == 401
        assert client.get(f"/api/scripts/{record['id']}/review").status_code == 401
        assert client.post(
            f"/api/scripts/{record['id']}/approve",
            json={"confirm_review": True, "reviewer": "reviewer-test"},
        ).status_code == 401
        queued = client.get("/api/scripts/review", headers=headers)
        assert queued.status_code == 200
        assert {item["id"] for item in queued.json()} == {
            record["id"], rejected_record["id"]
        }
        assert client.post(
            f"/api/scripts/{record['id']}/approve",
            headers=headers,
            json={"confirm_review": False, "reviewer": "reviewer-test"},
        ).status_code == 400
        assert client.post(
            f"/api/scripts/{rejected_record['id']}/reject",
            headers=headers,
            json={"confirm_review": False, "reviewer": "reviewer-test"},
        ).status_code == 400
        details = client.get(f"/api/scripts/{record['id']}/review", headers=headers)
        assert details.status_code == 200
        assert details.json()["script_id"] == record["id"]
        assert details.json()["script"] == record["script"]
        assert details.json()["evaluation"]["passed"] is True
        failed_approval = client.post(
            f"/api/scripts/{rejected_record['id']}/approve",
            headers=headers,
            json={"confirm_review": True, "reviewer": "reviewer-test"},
        )
        assert failed_approval.status_code == 409
        assert get_script_by_id(rejected_record["id"], database_path)["status"] == "needs_review"
        approved = client.post(
            f"/api/scripts/{record['id']}/approve",
            headers=headers,
            json={"confirm_review": True, "reviewer": "reviewer-test"},
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"
        status = client.get(f"/api/scripts/{record['id']}/review", headers=headers)
        assert status.json()["reviewed_by"] == "reviewer-test"
        duplicate = client.post(
            f"/api/scripts/{record['id']}/reject",
            headers=headers,
            json={"confirm_review": True, "reviewer": "reviewer-test"},
        )
        assert duplicate.status_code == 409
        rejected = client.post(
            f"/api/scripts/{rejected_record['id']}/reject",
            headers=headers,
            json={"confirm_review": True, "reviewer": "reviewer-test"},
        )
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "rejected"
        missing = client.get("/api/scripts/404/review", headers=headers)
        assert missing.status_code == 404
        invalid_id = client.get("/api/scripts/not-an-id/review", headers=headers)
        assert invalid_id.status_code == 422

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM productions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM youtube_uploads").fetchone()[0] == 0


def test_legacy_completed_script_is_visible_and_requires_fresh_confirmation(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    record = _save_script(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE scripts SET status = 'completed' WHERE id = ?", (record["id"],)
        )
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-token")
    monkeypatch.setenv("MOTIVATIONOS_SCHEDULER_ENABLED", "false")
    client = TestClient(create_app(database_path))
    headers = {"Authorization": "Bearer test-admin-token"}

    with client:
        queued = client.get("/api/scripts/review", headers=headers)
        assert queued.status_code == 200
        assert [(item["id"], item["status"]) for item in queued.json()] == [
            (record["id"], "completed")
        ]
        denied = client.post(
            f"/api/scripts/{record['id']}/approve",
            headers=headers,
            json={"confirm_review": False, "reviewer": "reviewer-test"},
        )
        assert denied.status_code == 400
        assert get_script_by_id(record["id"], database_path)["status"] == "completed"

        approved = client.post(
            f"/api/scripts/{record['id']}/approve",
            headers=headers,
            json={"confirm_review": True, "reviewer": "reviewer-test"},
        )

    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    reviewed = get_script_by_id(record["id"], database_path)
    assert reviewed["reviewed_by"] == "reviewer-test"
    assert datetime.fromisoformat(reviewed["reviewed_at"]).utcoffset() == timezone.utc.utcoffset(None)


@pytest.mark.parametrize("initial_status", ["needs_review", "rejected", "completed"])
def test_production_and_resume_block_unapproved_and_legacy_scripts(tmp_path, initial_status) -> None:
    database_path = tmp_path / f"{initial_status}.sqlite3"
    record = _save_script(database_path, passed=initial_status != "rejected")
    if initial_status == "rejected":
        review_script(record["id"], "rejected", "reviewer-test", database_path)
    elif initial_status == "completed":
        with sqlite3.connect(database_path) as connection:
            connection.execute("UPDATE scripts SET status = 'completed' WHERE id = ?", (record["id"],))

    output_root = tmp_path / "productions"
    service = ProductionService(database_path, output_root)
    service._run = lambda production, script: pytest.fail("Production pipeline must not start")

    with pytest.raises(ValueError, match="explicit human approval"):
        service.produce_script_record(record["id"])
    production = create_production(
        f"failed-{initial_status}",
        record["id"],
        "test topic",
        output_root / f"failed-{initial_status}",
        database_path=database_path,
    )
    update_production(
        production["id"], "failed", {}, database_path=database_path
    )
    with pytest.raises(ValueError, match="explicit human approval"):
        service.resume(production["id"])
    assert not output_root.exists()


def test_only_review_approved_script_passes_production_gate_without_running_pipeline(tmp_path) -> None:
    database_path = tmp_path / "approved.sqlite3"
    record = _save_script(database_path)
    review_script(record["id"], "approved", "reviewer-test", database_path)
    service = ProductionService(database_path, tmp_path / "productions")
    calls = []
    service._run = lambda production, script: calls.append((production, script)) or {
        "status": "test-only"
    }

    result = service.produce_script_record(record["id"])

    assert result["status"] == "test-only"
    assert len(calls) == 1
    assert calls[0][1].topic == "discipline"
    failed_production = create_production(
        "failed-approved",
        record["id"],
        "test topic",
        tmp_path / "productions" / "failed-approved",
        database_path=database_path,
    )
    update_production(
        failed_production["id"], "failed", {}, database_path=database_path
    )
    resumed = service.resume(failed_production["id"])

    assert resumed["status"] == "test-only"
    assert len(calls) == 2
    assert not (tmp_path / "productions").exists()
