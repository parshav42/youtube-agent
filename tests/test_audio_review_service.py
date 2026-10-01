"""Offline tests for generation-to-QA human review orchestration."""

import sqlite3
import wave
from pathlib import Path

import pytest

from app.agents.quality_agent import evaluate_script
from app.agents.script_agent import MockScriptGenerator
from app.audio.generation import AudioGenerationError, MockAudioProvider
from app.audio.quality import AudioQAThresholds
from app.audio.review_service import generate_audio_for_human_review
from app.database.db import get_script_by_id, review_script, save_script
from app.schemas.script_schema import ScriptRequest


def _save_script(database_path: Path, *, approved: bool) -> dict:
    script = MockScriptGenerator().generate(ScriptRequest(topic="human audio review"))
    evaluation = evaluate_script(script)
    record = save_script(script, evaluation=evaluation, database_path=database_path)
    if approved:
        review_script(record["id"], "approved", "audio-review-test", database_path)
    return get_script_by_id(record["id"], database_path)


class SilentAudioProvider:
    provider_name = "test-only silent WAV fixture"

    def generate(self, approved_script_text: str, output_path: str | Path) -> Path:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(destination), "wb") as audio_file:
            audio_file.setnchannels(1)
            audio_file.setsampwidth(2)
            audio_file.setframerate(8000)
            audio_file.writeframes(b"\x00\x00" * 4000)
        return destination


class InvalidAudioProvider:
    provider_name = "test-only invalid fixture"

    def generate(self, approved_script_text: str, output_path: str | Path) -> Path:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"not a WAV file")
        return destination


class FailingAudioProvider:
    provider_name = "test-only failing fixture"

    def generate(self, approved_script_text: str, output_path: str | Path) -> Path:
        raise AudioGenerationError("test provider failure")


def _assert_no_production_or_upload_rows(database_path: Path) -> None:
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM productions").fetchone()[0] == 0
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if "youtube_uploads" in tables:
            assert connection.execute("SELECT COUNT(*) FROM youtube_uploads").fetchone()[0] == 0


def test_approved_generation_runs_qa_and_preserves_review_state(tmp_path) -> None:
    database_path = tmp_path / "scripts.sqlite3"
    record = _save_script(database_path, approved=True)
    output_directory = tmp_path / "review-audio"

    result = generate_audio_for_human_review(
        record["id"],
        database_path,
        output_directory,
        "script-review.wav",
        MockAudioProvider(),
        target_duration_seconds=0.5,
    )

    assert result.script_id == record["id"]
    assert Path(result.generation.path) == output_directory / "script-review.wav"
    assert result.generation.provider == "deterministic mock tone (not speech)"
    assert result.quality.file_valid is True
    assert result.quality.detected_format == "wav"
    assert result.technical_status == "passed"
    after = get_script_by_id(record["id"], database_path)
    assert after["status"] == "approved"
    assert after["reviewed_at"] == record["reviewed_at"]
    assert after["reviewed_by"] == record["reviewed_by"]
    _assert_no_production_or_upload_rows(database_path)


def test_unapproved_script_is_rejected_before_provider_call(tmp_path) -> None:
    database_path = tmp_path / "scripts.sqlite3"
    record = _save_script(database_path, approved=False)
    output_directory = tmp_path / "review-audio"

    with pytest.raises(AudioGenerationError, match="Explicit human approval"):
        generate_audio_for_human_review(
            record["id"],
            database_path,
            output_directory,
            "blocked.wav",
            FailingAudioProvider(),
        )

    assert not output_directory.exists()
    assert get_script_by_id(record["id"], database_path)["status"] == "needs_review"
    _assert_no_production_or_upload_rows(database_path)


def test_invalid_generated_audio_is_rejected_and_removed(tmp_path) -> None:
    database_path = tmp_path / "scripts.sqlite3"
    record = _save_script(database_path, approved=True)
    output_directory = tmp_path / "review-audio"

    with pytest.raises(AudioGenerationError, match="not a valid WAV"):
        generate_audio_for_human_review(
            record["id"],
            database_path,
            output_directory,
            "invalid.wav",
            InvalidAudioProvider(),
        )

    assert not (output_directory / "invalid.wav").exists()


def test_qa_needs_review_result_is_returned_without_approval_transition(tmp_path) -> None:
    database_path = tmp_path / "scripts.sqlite3"
    record = _save_script(database_path, approved=True)

    result = generate_audio_for_human_review(
        record["id"], database_path, tmp_path / "review-audio", "silent.wav", SilentAudioProvider()
    )

    assert result.quality.silence_detected is True
    assert result.technical_status == "needs_review"
    assert get_script_by_id(record["id"], database_path)["status"] == "approved"
    _assert_no_production_or_upload_rows(database_path)


def test_qa_failed_result_is_returned_without_deleting_generated_audio(tmp_path) -> None:
    database_path = tmp_path / "scripts.sqlite3"
    record = _save_script(database_path, approved=True)
    output_directory = tmp_path / "review-audio"
    audio_filename = "small-limit.wav"

    result = generate_audio_for_human_review(
        record["id"],
        database_path,
        output_directory,
        audio_filename,
        MockAudioProvider(),
        thresholds=AudioQAThresholds(max_file_size_bytes=1),
    )

    assert result.quality.file_valid is False
    assert result.technical_status == "failed"
    assert (output_directory / audio_filename).is_file()
    assert get_script_by_id(record["id"], database_path)["status"] == "approved"
    _assert_no_production_or_upload_rows(database_path)


def test_unavailable_qa_result_is_returned_as_needs_review(tmp_path) -> None:
    database_path = tmp_path / "scripts.sqlite3"
    record = _save_script(database_path, approved=True)

    result = generate_audio_for_human_review(
        record["id"],
        database_path,
        tmp_path / "review-audio",
        "analysis-limited.wav",
        MockAudioProvider(),
        thresholds=AudioQAThresholds(max_signal_analysis_seconds=0.1),
    )

    assert result.quality.silence_check_status == "unavailable"
    assert result.quality.clipping_check_status == "unavailable"
    assert result.technical_status == "needs_review"
    _assert_no_production_or_upload_rows(database_path)


def test_provider_failure_propagates_without_approval_or_production_side_effects(tmp_path) -> None:
    database_path = tmp_path / "scripts.sqlite3"
    record = _save_script(database_path, approved=True)
    output_directory = tmp_path / "review-audio"

    with pytest.raises(AudioGenerationError, match="provider failure"):
        generate_audio_for_human_review(
            record["id"],
            database_path,
            output_directory,
            "failure.wav",
            FailingAudioProvider(),
        )

    assert not (output_directory / "failure.wav").exists()
    assert get_script_by_id(record["id"], database_path)["status"] == "approved"
    _assert_no_production_or_upload_rows(database_path)


def test_output_filename_cannot_escape_explicit_directory(tmp_path) -> None:
    database_path = tmp_path / "scripts.sqlite3"
    record = _save_script(database_path, approved=True)

    with pytest.raises(AudioGenerationError, match="simple filename"):
        generate_audio_for_human_review(
            record["id"], database_path, tmp_path / "review-audio", "../outside.wav", MockAudioProvider()
        )

    assert not (tmp_path / "outside.wav").exists()