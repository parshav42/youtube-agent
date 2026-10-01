"""Offline release QA and authenticated local production review checks."""

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api import ProductionReviewDecision, create_app
from app.agents.script_agent import MockScriptGenerator
from app.database.db import create_production, save_script
from app.schemas.script_schema import ScriptRequest
from app.video.quality import run_video_quality_assurance


def test_video_qa_missing_and_invalid_media_fail_closed(tmp_path):
    missing = run_video_quality_assurance(tmp_path / "missing.mp4")
    invalid_path = tmp_path / "invalid.mp4"
    invalid_path.write_bytes(b"not media")
    invalid = run_video_quality_assurance(invalid_path)
    assert missing["technical_status"] == "failed"
    assert "missing" in missing["findings"][0]
    assert invalid["technical_status"] == "failed"


def test_production_review_auth_and_one_time_approval(tmp_path, monkeypatch):
    database = tmp_path / "review.sqlite3"
    monkeypatch.setenv("MOTIVATIONOS_ADMIN_TOKEN", "test-local-token")
    script = MockScriptGenerator().generate(ScriptRequest(topic="focus"))
    saved = save_script(script, database_path=database)
    output = tmp_path / "production"
    output.mkdir()
    (output / "final.mp4").write_bytes(b"synthetic preview")
    metadata = {
        "youtube_metadata": {"title": "Fixture title", "description": "Fixture description"},
        "audio_qa": {"technical_status": "needs_review", "findings": ["Approximate signal analysis."]},
        "video_qa": {"technical_status": "passed", "findings": ["Video passed."]},
    }
    create_production("fixture-production", saved["id"], "focus", output, metadata, database)
    from app.database.db import update_production

    update_production("fixture-production", "needs_review", metadata, database_path=database)
    application = create_app(database)
    review_route = next(route for route in application.routes if getattr(route, "path", None) == "/api/productions/{production_id}/review")
    with pytest.raises(HTTPException) as missing:
        review_route.endpoint("not-a-production")
    assert missing.value.status_code == 404
    auth = review_route.dependant.dependencies[0].call
    with pytest.raises(HTTPException) as denied:
        auth(credentials=None)
    assert denied.value.status_code == 401

    decision_route = next(route for route in application.routes if getattr(route, "path", None) == "/api/productions/{production_id}/review/{decision}")
    body = ProductionReviewDecision(confirm_review=True, reviewer="Local reviewer")
    assert decision_route.endpoint("fixture-production", "approve", body)["status"] == "completed"
    with pytest.raises(HTTPException) as repeated:
        decision_route.endpoint("fixture-production", "reject", body)
    assert repeated.value.status_code == 409

    failed_output = tmp_path / "failed-production"
    failed_output.mkdir()
    failed_metadata = {**metadata, "video_qa": {"technical_status": "failed", "findings": ["Decode errors."]}}
    create_production("fixture-failed-video", saved["id"], "focus", failed_output, failed_metadata, database)
    update_production("fixture-failed-video", "needs_review", failed_metadata, database_path=database)
    with pytest.raises(HTTPException) as qa_blocked:
        decision_route.endpoint("fixture-failed-video", "approve", body)
    assert qa_blocked.value.status_code == 409

    reject_output = tmp_path / "rejected-production"
    reject_output.mkdir()
    create_production("fixture-rejected", saved["id"], "focus", reject_output, metadata, database)
    update_production("fixture-rejected", "needs_review", metadata, database_path=database)
    rejected = decision_route.endpoint("fixture-rejected", "reject", body)
    assert rejected["status"] == "rejected"
    with pytest.raises(HTTPException) as repeated_rejection:
        decision_route.endpoint("fixture-rejected", "reject", body)
    assert repeated_rejection.value.status_code == 409
