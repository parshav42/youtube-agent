import hashlib
import json

from app.youtube.auto_publish import auto_approval_failures


def _gate_inputs(tmp_path):
    video_path = tmp_path / "final.mp4"
    video_path.write_bytes(b"fixture-video")
    library = tmp_path / "licensed-assets"
    library.mkdir()
    asset_path = library / "scene_01.jpg"
    asset_path.write_bytes(b"fixture-image")
    provenance = {"source": "creator record", "license": "CC BY 4.0", "rights_holder": "Creator"}
    (library / "LICENSES.json").write_text(json.dumps({asset_path.name: provenance}), encoding="utf-8")
    checks = {
        "quality_score": 9.5,
        "threshold": 8.0,
        "passed": True,
        "language_consistency": True,
        "story_coherence": True,
        "originality_safeguards": True,
        "duration_feasibility": True,
        "repetition": True,
        "motivational_message": True,
        "scene_completeness": True,
        "caption_scene_alignment": True,
        "structural_labels_absent": True,
        "unsupported_factual_assertions": True,
        "unsupported_guarantees": True,
    }
    script = {"script": {"topic": "Consistency"}, "evaluation": checks}
    production_metadata = {
        "audio_qa": {"technical_status": "passed", "quality_score": 9.0},
        "video_qa": {"technical_status": "passed", "quality_score": 9.0},
        "visual_assets": [{**provenance, "path": str(asset_path)}],
        "video": {"path": str(video_path)},
        "review_decision": {
            "video_sha256": hashlib.sha256(video_path.read_bytes()).hexdigest()
        },
    }
    production = {"metadata": production_metadata}
    settings = {
        "min_script_quality_score": 8.0,
        "min_audio_quality_score": 8.0,
        "min_video_quality_score": 8.0,
    }
    return script, production, library, settings


def test_all_auto_approval_gates_pass_with_provenance_and_hash(tmp_path):
    script, production, library, settings = _gate_inputs(tmp_path)

    assert auto_approval_failures(script, production, library, settings) == []


def test_policy_qa_license_and_hash_failures_are_reported(tmp_path):
    script, production, library, settings = _gate_inputs(tmp_path)
    script["evaluation"]["unsupported_guarantees"] = False
    production["metadata"]["audio_qa"]["technical_status"] = "needs_review"
    production["metadata"]["visual_assets"][0]["rights_holder"] = "Other"
    production["metadata"]["review_decision"]["video_sha256"] = "wrong-hash"

    failures = auto_approval_failures(script, production, library, settings)

    assert any("safety checks" in reason for reason in failures)
    assert any("audio QA" in reason for reason in failures)
    assert any("LICENSES.json" in reason for reason in failures)
    assert any("hash" in reason for reason in failures)