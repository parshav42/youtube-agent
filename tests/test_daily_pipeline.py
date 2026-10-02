"""Focused safety tests for the end-to-end manual upload pipeline."""

import json
from pathlib import Path

import pytest

from app.agents.script_agent import MockScriptGenerator
from app.daily_pipeline import DailyPipelineError, _validate_provenance, run_daily_pipeline
from app.schemas.script_schema import ScriptRequest


def _pipeline_fakes(monkeypatch, tmp_path, *, auto_scripts=True, auto_productions=True,
                    qa="passed", provenance=True, scores=(85,)):
    import app.daily_pipeline as pipeline

    library = tmp_path / "licensed"
    library.mkdir()
    script = MockScriptGenerator().generate(ScriptRequest(topic="discipline"))
    for scene in script.scenes:
        image = library / f"scene_{scene.scene_number:02d}.jpg"
        image.write_bytes(b"image")
    manifest = {p.name: {"source": "test", "license": "test license", "rights_holder": "test"}
                for p in library.glob("scene_*.jpg")}
    if provenance:
        (library / "LICENSES.json").write_text(json.dumps(manifest))
    else:
        (library / "LICENSES.json").write_text("{}")
    output = tmp_path / "production"
    output.mkdir()
    video = output / "final.mp4"
    video.write_bytes(b"rendered video")
    (output / "metadata.json").write_text(json.dumps({"production_id": "prod-1", "metadata": {}}))
    script_records = []
    for i, score in enumerate(scores, 1):
        script_records.append({"id": i, "script": script.model_dump(mode="json"),
                               "quality_score": score / 10,
                               "evaluation": {"passed": True}, "status": "needs_review"})
    calls = {"draft": 0, "deleted": [], "package": 0}

    class Workflow:
        def invoke(self, state):
            if "script_request" in state:
                index = calls["draft"]
                calls["draft"] += 1
                return {"script_record": script_records[min(index, len(script_records)-1)],
                        "script_json_path": str(tmp_path / f"draft-{index}.json")}
            return {"production_result": {"id": "prod-1", "status": "ready_for_manual_upload"}}

    monkeypatch.setattr(pipeline, "build_workflow", lambda **kwargs: Workflow())
    monkeypatch.setattr(pipeline, "get_daily_pipeline_settings", lambda: {
        "auto_approve_scripts": auto_scripts, "auto_approve_productions": auto_productions,
        "min_script_quality_score": 75, "max_script_retries": max(0, len(scores)-1), "niche": "", "cron": ""})
    monkeypatch.setattr(pipeline, "get_topic", lambda: "discipline")
    monkeypatch.setattr(pipeline, "get_quality_threshold", lambda: 7.5)
    monkeypatch.setattr(pipeline, "get_licensed_asset_path", lambda: library)
    monkeypatch.setattr(pipeline, "build_image_provider", lambda: object())
    monkeypatch.setattr(pipeline, "generate_scene_images", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "delete_unreviewed_script", lambda i, db: calls["deleted"].append(i))
    monkeypatch.setattr(pipeline, "review_script", lambda i, *a: {
        **script_records[i-1], "reviewed_at": "2026-01-01T00:00:00+00:00", "reviewed_by": "auto_pipeline"})
    metadata = {"audio_qa": {"technical_status": qa, "quality_score": 9},
                "video_qa": {"technical_status": "passed", "quality_score": 9},
                "video": {"path": str(video)}, "youtube_metadata": {
                    "title": "Test title", "description": "Test description", "tags": ["focus"]}}
    production = {"id": "prod-1", "script_id": 1, "status": "ready_for_manual_upload",
                  "output_dir": str(output), "metadata": metadata, "error": None}
    monkeypatch.setattr(pipeline, "get_production_by_id", lambda *a: production)
    monkeypatch.setattr(pipeline, "update_production", lambda *a, **k: {**production, "status": "ready_for_manual_upload"})
    import app.youtube.manual_upload as manual_upload
    monkeypatch.setattr(manual_upload, "get_production_by_id", lambda *a: production)
    actual_export = pipeline.export_upload_package
    def tracked_export(*args):
        calls["package"] += 1
        return actual_export(*args)
    monkeypatch.setattr(pipeline, "export_upload_package", tracked_export)
    return calls, script


def test_missing_license_provenance_fails_closed(tmp_path) -> None:
    library = tmp_path / "licensed"
    library.mkdir()
    (library / "scene_01.jpg").write_bytes(b"image")
    (library / "LICENSES.json").write_text(json.dumps({
        "scene_01.jpg": {"source": "provider"}
    }))

    error = _validate_provenance(library, {"scenes": [{"scene_number": 1}]})

    assert "source/license/rights_holder" in error


def test_invalid_or_missing_license_manifest_fails_closed(tmp_path) -> None:
    assert "Missing or unreadable" in _validate_provenance(
        tmp_path, {"scenes": [{"scene_number": 1}]}
    )
    (tmp_path / "LICENSES.json").write_text("[]")
    assert _validate_provenance(tmp_path, {"scenes": [{"scene_number": 1}]}) == "LICENSES.json must be an object."


def test_pipeline_error_retains_resume_ids() -> None:
    error = DailyPipelineError("video QA", "technical check failed", script_id=4, production_id="p-4")

    assert error.step == "video QA"
    assert error.script_id == 4
    assert error.production_id == "p-4"


def test_full_pipeline_happy_path_exports_package(monkeypatch, tmp_path) -> None:
    calls, _ = _pipeline_fakes(monkeypatch, tmp_path)
    result = run_daily_pipeline(tmp_path / "db.sqlite3")
    assert result["package_path"].endswith("upload_package")
    assert result["production_id"] == "prod-1"
    assert calls["package"] == 1
    package = Path(result["package_path"])
    assert {"final.mp4", "title.txt", "description.txt", "tags.txt", "README.txt"} <= {
        item.name for item in package.iterdir()
    }


def test_low_score_retries_with_fresh_topic_then_succeeds(monkeypatch, tmp_path) -> None:
    calls, _ = _pipeline_fakes(monkeypatch, tmp_path, scores=(70, 85))
    run_daily_pipeline(tmp_path / "db.sqlite3")
    assert calls["draft"] == 2
    assert calls["deleted"] == [1]


def test_exhausted_script_retries_leave_no_drafts_or_package(monkeypatch, tmp_path) -> None:
    calls, _ = _pipeline_fakes(monkeypatch, tmp_path, scores=(70, 71, 72))
    with pytest.raises(DailyPipelineError, match="All 3 script attempts"):
        run_daily_pipeline(tmp_path / "db.sqlite3")
    assert calls["deleted"] == [1, 2, 3]
    assert calls["package"] == 0


def test_script_auto_approval_disabled_stops_with_manual_gate(monkeypatch, tmp_path) -> None:
    _pipeline_fakes(monkeypatch, tmp_path, auto_scripts=False)
    with pytest.raises(DailyPipelineError, match="Manual approval required for script"):
        run_daily_pipeline(tmp_path / "db.sqlite3")


def test_production_auto_approval_disabled_stops_with_manual_gate(monkeypatch, tmp_path) -> None:
    calls, _ = _pipeline_fakes(monkeypatch, tmp_path, auto_productions=False)
    with pytest.raises(DailyPipelineError, match="Manual approval required for production"):
        run_daily_pipeline(tmp_path / "db.sqlite3")
    assert calls["package"] == 0


def test_failed_audio_qa_blocks_approval_and_export(monkeypatch, tmp_path) -> None:
    calls, _ = _pipeline_fakes(monkeypatch, tmp_path, qa="failed")
    with pytest.raises(DailyPipelineError, match="Audio QA did not explicitly pass"):
        run_daily_pipeline(tmp_path / "db.sqlite3")
    assert calls["package"] == 0


def test_missing_provenance_blocks_approval_and_export(monkeypatch, tmp_path) -> None:
    calls, _ = _pipeline_fakes(monkeypatch, tmp_path, provenance=False)
    with pytest.raises(DailyPipelineError, match="Missing complete"):
        run_daily_pipeline(tmp_path / "db.sqlite3")
    assert calls["package"] == 0
