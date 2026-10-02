"""Automatic approval stays fail-closed across every configured gate."""

import hashlib
import json

import pytest

from app.youtube.auto_publish import evaluate_auto_approval_gates


@pytest.fixture
def gate_records(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    (library / "LICENSES.json").write_text(
        json.dumps(
            {
                "scene_01.jpg": {
                    "source": "Original",
                    "license": "Owned",
                    "rights_holder": "MotivationOS",
                }
            }
        ),
        encoding="utf-8",
    )
    production_root = tmp_path / "production"
    production_root.mkdir()
    scene_path = production_root / "scene_01.jpg"
    scene_path.write_bytes(b"licensed scene")
    video_path = production_root / "final.mp4"
    video_path.write_bytes(b"validated video bytes")
    checks = (
        "language_consistency",
        "story_coherence",
        "originality_safeguards",
        "duration_feasibility",
        "repetition",
        "motivational_message",
        "scene_completeness",
        "caption_scene_alignment",
        "structural_labels_absent",
        "unsupported_factual_assertions",
        "unsupported_guarantees",
    )
    script = {
        "script": {"topic": "Gate fixture"},
        "quality_score": 90.0,
        "evaluation": {"passed": True, **dict.fromkeys(checks, True)},
    }
    production = {
        "status": "completed",
        "output_dir": str(production_root),
        "metadata": {
            "audio_qa": {"technical_status": "passed"},
            "video_qa": {"technical_status": "passed"},
            "visual_assets": [
                {
                    "path": str(scene_path),
                    "source": "Original",
                    "license": "Owned",
                    "rights_holder": "MotivationOS",
                }
            ],
            "video": {"path": str(video_path)},
            "production_sha256": hashlib.sha256(video_path.read_bytes()).hexdigest(),
        },
    }
    return library, script, production, video_path


def _evaluate(library, script, production):
    return evaluate_auto_approval_gates(
        script,
        production,
        licensed_asset_directory=library,
        min_script_quality_score=80.0,
        min_audio_quality_score=8.0,
        min_video_quality_score=8.0,
    )


def test_all_approval_gates_pass_and_record_video_hash(gate_records):
    library, script, production, video_path = gate_records

    result = _evaluate(library, script, production)

    assert result["eligible"] is True
    assert result["reasons"] == []
    assert result["production_sha256"] == hashlib.sha256(video_path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("change", "expected_reason"),
    [
        ("script_score", "script_quality_below_threshold"),
        ("policy", "script_policy_or_quality_flags"),
        ("audio", "audio_qa_failed_or_below_threshold"),
        ("video", "video_qa_failed_or_below_threshold"),
        ("provenance", "scene_asset_provenance_incomplete"),
        ("hash", "production_hash_unavailable"),
    ],
)
def test_any_failed_gate_blocks_auto_approval(gate_records, change, expected_reason):
    library, script, production, _ = gate_records
    if change == "script_score":
        script["quality_score"] = 79.0
    elif change == "policy":
        script["evaluation"]["unsupported_guarantees"] = False
    elif change == "audio":
        production["metadata"]["audio_qa"]["technical_status"] = "needs_review"
    elif change == "video":
        production["metadata"]["video_qa"]["technical_status"] = "failed"
    elif change == "provenance":
        production["metadata"]["visual_assets"][0]["license"] = ""
    elif change == "hash":
        production["metadata"]["video"]["path"] = str(library / "missing.mp4")

    result = _evaluate(library, script, production)

    assert result["eligible"] is False
    assert expected_reason in result["reasons"]


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
        "quality_score": 95.0,
        "threshold": 80.0,
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
        "min_script_quality_score": 80.0,
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