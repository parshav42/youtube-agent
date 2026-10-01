"""Offline release QA and authenticated local production review checks."""

import sqlite3

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from app.api import ProductionReviewDecision, create_app
from app.agents.script_agent import MockScriptGenerator
from app.database.db import create_production, get_production_by_id, save_script, update_production
from app.schemas.script_schema import ScriptRequest
from app.video.quality import run_video_quality_assurance


def _create_audio_review_fixture(database, production_id, audio_status="needs_review"):
    output = database.parent / production_id
    output.mkdir()
    (output / "final.mp4").write_bytes(b"synthetic review fixture")
    metadata = {
        "audio_qa": {"technical_status": audio_status, "findings": ["Synthetic audio finding."]},
        "video_qa": {"technical_status": "passed", "findings": []},
    }
    create_production(production_id, 1, "fixture", output, metadata, database)
    update_production(production_id, "needs_review", metadata, database_path=database)
    return metadata


@pytest.fixture
def audio_review_api(tmp_path, monkeypatch):
    database = tmp_path / "audio-review.sqlite3"
    _create_audio_review_fixture(database, "audio-review-production")
    monkeypatch.setenv("MOTIVATIONOS_ADMIN_TOKEN", "audio-review-test-token")
    monkeypatch.setenv("MOTIVATIONOS_SCHEDULER_ENABLED", "false")
    with TestClient(create_app(database)) as client:
        yield client, database


def _audio_review_headers():
    return {"Authorization": "Bearer audio-review-test-token"}


def _audio_review_body(*, confirm=True):
    return {"confirm_review": confirm, "reviewer": "Audio reviewer"}


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
        "audio_qa": {"technical_status": "passed", "findings": ["Synthetic fixture passed."]},
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


@pytest.mark.parametrize(
    "audio_qa",
    [None, {}, {"technical_status": "failed"}, {"technical_status": "needs_review"}, {"technical_status": 1}],
)
def test_audio_qa_without_approval_blocks_production_without_mutation(tmp_path, audio_qa):
    database = tmp_path / "audio-gate.sqlite3"
    output = tmp_path / "production"
    output.mkdir()
    metadata = {
        "audio_qa": audio_qa,
        "video_qa": {"technical_status": "passed", "findings": []},
    }
    create_production("audio-gate", 1, "fixture", output, metadata, database)
    update_production("audio-gate", "needs_review", metadata, database_path=database)
    application = create_app(database)
    route = next(
        route
        for route in application.routes
        if getattr(route, "path", None) == "/api/productions/{production_id}/review/{decision}"
    )

    with pytest.raises(HTTPException) as blocked:
        route.endpoint(
            "audio-gate",
            "approve",
            ProductionReviewDecision(confirm_review=True, reviewer="Audio reviewer"),
        )

    assert blocked.value.status_code == 409
    stored = get_production_by_id("audio-gate", database)
    assert stored["status"] == "needs_review"
    assert stored["metadata"] == metadata


def test_needs_review_audio_requires_separate_recorded_human_decision(tmp_path):
    database = tmp_path / "audio-human-review.sqlite3"
    output = tmp_path / "production"
    output.mkdir()
    metadata = {
        "audio_qa": {"technical_status": "needs_review", "findings": ["Approximate analysis."]},
        "video_qa": {"technical_status": "passed", "findings": []},
    }
    create_production("audio-human-review", 1, "fixture", output, metadata, database)
    update_production("audio-human-review", "needs_review", metadata, database_path=database)
    application = create_app(database)
    audio_route = next(
        route
        for route in application.routes
        if getattr(route, "path", None) == "/api/productions/{production_id}/audio-review/{decision}"
    )
    production_route = next(
        route
        for route in application.routes
        if getattr(route, "path", None) == "/api/productions/{production_id}/review/{decision}"
    )
    body = ProductionReviewDecision(confirm_review=True, reviewer="Audio reviewer")

    with pytest.raises(HTTPException) as unreviewed:
        production_route.endpoint("audio-human-review", "approve", body)
    assert unreviewed.value.status_code == 409
    assert get_production_by_id("audio-human-review", database)["metadata"] == metadata

    audio_decision = audio_route.endpoint("audio-human-review", "approve", body)
    assert audio_decision["status"] == "needs_review"
    assert audio_decision["audio_review_decision"]["decision"] == "approve"
    assert audio_decision["audio_review_decision"]["reviewer"] == "Audio reviewer"
    assert audio_decision["audio_review_decision"]["reviewed_at"]
    assert get_production_by_id("audio-human-review", database)["metadata"]["audio_qa"]["technical_status"] == "needs_review"

    approved = production_route.endpoint("audio-human-review", "approve", body)
    assert approved["status"] == "completed"
    assert approved["review_decision"]["reviewed_at"]
    with pytest.raises(HTTPException) as duplicate_audio_decision:
        audio_route.endpoint("audio-human-review", "reject", body)
    assert duplicate_audio_decision.value.status_code == 409


@pytest.mark.parametrize(
    "audio_review_decision",
    [
        None,
        {"decision": "approve", "reviewer": "Audio reviewer", "confirmed": True},
        {"decision": "approve", "reviewer": " ", "confirmed": True, "reviewed_at": "2026-10-02T00:00:00+00:00"},
        {"decision": "approve", "reviewer": "Audio reviewer", "confirmed": False, "reviewed_at": "2026-10-02T00:00:00+00:00"},
        {"decision": "approve", "reviewer": "Audio reviewer", "confirmed": True, "reviewed_at": "not-a-timestamp"},
        {"decision": "reject", "reviewer": "Audio reviewer", "confirmed": True, "reviewed_at": "2026-10-02T00:00:00+00:00"},
    ],
)
def test_malformed_audio_review_decisions_do_not_authorize_production(tmp_path, audio_review_decision):
    database = tmp_path / "malformed-audio-review.sqlite3"
    output = tmp_path / "production"
    output.mkdir()
    metadata = {
        "audio_qa": {"technical_status": "needs_review", "findings": []},
        "video_qa": {"technical_status": "passed", "findings": []},
        "audio_review_decision": audio_review_decision,
    }
    create_production("malformed-audio-review", 1, "fixture", output, metadata, database)
    update_production("malformed-audio-review", "needs_review", metadata, database_path=database)
    application = create_app(database)
    route = next(
        route
        for route in application.routes
        if getattr(route, "path", None) == "/api/productions/{production_id}/review/{decision}"
    )

    with pytest.raises(HTTPException) as blocked:
        route.endpoint(
            "malformed-audio-review",
            "approve",
            ProductionReviewDecision(confirm_review=True, reviewer="Production reviewer"),
        )

    assert blocked.value.status_code == 409
    stored = get_production_by_id("malformed-audio-review", database)
    assert stored["status"] == "needs_review"
    assert stored["metadata"] == metadata


def test_rejected_audio_review_cannot_be_approved_for_production(tmp_path):
    database = tmp_path / "audio-rejection.sqlite3"
    output = tmp_path / "production"
    output.mkdir()
    metadata = {
        "audio_qa": {"technical_status": "needs_review", "findings": []},
        "video_qa": {"technical_status": "passed", "findings": []},
    }
    create_production("audio-rejected", 1, "fixture", output, metadata, database)
    update_production("audio-rejected", "needs_review", metadata, database_path=database)
    application = create_app(database)
    audio_route = next(
        route
        for route in application.routes
        if getattr(route, "path", None) == "/api/productions/{production_id}/audio-review/{decision}"
    )
    production_route = next(
        route
        for route in application.routes
        if getattr(route, "path", None) == "/api/productions/{production_id}/review/{decision}"
    )
    body = ProductionReviewDecision(confirm_review=True, reviewer="Audio reviewer")

    rejected = audio_route.endpoint("audio-rejected", "reject", body)
    assert rejected["status"] == "rejected"
    with pytest.raises(HTTPException) as blocked:
        production_route.endpoint("audio-rejected", "approve", body)
    assert blocked.value.status_code == 409
    stored = get_production_by_id("audio-rejected", database)
    assert stored["status"] == "rejected"
    assert stored["metadata"]["audio_review_decision"]["decision"] == "reject"


def test_audio_review_api_requires_authentication(audio_review_api):
    client, _ = audio_review_api

    response = client.post(
        "/api/productions/audio-review-production/audio-review/approve",
        json=_audio_review_body(),
    )

    assert response.status_code == 401
    assert "audio-review-test-token" not in response.text


def test_audio_review_rejects_missing_confirmation(audio_review_api):
    client, database = audio_review_api

    response = client.post(
        "/api/productions/audio-review-production/audio-review/approve",
        headers=_audio_review_headers(),
        json=_audio_review_body(confirm=False),
    )

    assert response.status_code == 400
    assert get_production_by_id("audio-review-production", database)["metadata"].get(
        "audio_review_decision"
    ) is None


def test_audio_approval_records_a_separate_decision(audio_review_api):
    client, database = audio_review_api

    response = client.post(
        "/api/productions/audio-review-production/audio-review/approve",
        headers=_audio_review_headers(),
        json=_audio_review_body(),
    )

    assert response.status_code == 200
    decision = response.json()["audio_review_decision"]
    assert decision["decision"] == "approve"
    assert decision["reviewer"] == "Audio reviewer"
    assert decision["confirmed"] is True
    assert decision["reviewed_at"]
    stored = get_production_by_id("audio-review-production", database)
    assert stored["status"] == "needs_review"
    assert stored["metadata"]["audio_qa"]["technical_status"] == "needs_review"
    assert "review_decision" not in stored["metadata"]


def test_audio_rejection_remains_rejected(audio_review_api):
    client, database = audio_review_api

    response = client.post(
        "/api/productions/audio-review-production/audio-review/reject",
        headers=_audio_review_headers(),
        json=_audio_review_body(),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    stored = get_production_by_id("audio-review-production", database)
    assert stored["status"] == "rejected"
    assert stored["metadata"]["audio_review_decision"]["decision"] == "reject"


def test_repeated_audio_review_decision_is_blocked(audio_review_api):
    client, _ = audio_review_api
    url = "/api/productions/audio-review-production/audio-review/approve"

    assert client.post(url, headers=_audio_review_headers(), json=_audio_review_body()).status_code == 200
    repeated = client.post(
        "/api/productions/audio-review-production/audio-review/reject",
        headers=_audio_review_headers(),
        json=_audio_review_body(),
    )

    assert repeated.status_code == 409


def test_failed_audio_qa_cannot_be_overridden(audio_review_api):
    client, database = audio_review_api
    _create_audio_review_fixture(database, "failed-audio-production", "failed")

    response = client.post(
        "/api/productions/failed-audio-production/audio-review/approve",
        headers=_audio_review_headers(),
        json=_audio_review_body(),
    )

    assert response.status_code == 409
    stored = get_production_by_id("failed-audio-production", database)
    assert stored["metadata"]["audio_qa"]["technical_status"] == "failed"
    assert "audio_review_decision" not in stored["metadata"]


def test_audio_approval_does_not_create_production_approval(audio_review_api):
    client, database = audio_review_api

    response = client.post(
        "/api/productions/audio-review-production/audio-review/approve",
        headers=_audio_review_headers(),
        json=_audio_review_body(),
    )

    assert response.status_code == 200
    production = get_production_by_id("audio-review-production", database)
    assert production["status"] == "needs_review"
    assert "review_decision" not in production["metadata"]


def test_audio_approval_does_not_create_youtube_upload(audio_review_api):
    client, database = audio_review_api

    response = client.post(
        "/api/productions/audio-review-production/audio-review/approve",
        headers=_audio_review_headers(),
        json=_audio_review_body(),
    )

    assert response.status_code == 200
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM youtube_uploads").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM publishing_approvals").fetchone()[0] == 0


def test_invalid_or_revoked_authentication_is_rejected_safely(audio_review_api, monkeypatch):
    client, _ = audio_review_api
    monkeypatch.setenv("MOTIVATIONOS_ADMIN_TOKEN", "replacement-test-token")

    response = client.get(
        "/api/productions/audio-review-production/review",
        headers=_audio_review_headers(),
    )

    assert response.status_code == 401
    assert "audio-review-test-token" not in response.text
    assert "replacement-test-token" not in response.text
