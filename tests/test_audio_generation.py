"""Offline tests for approved-script audio generation and validation."""

import sqlite3
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from app.agents.quality_agent import evaluate_script
from app.agents.script_agent import MockScriptGenerator
from app.audio.generation import (
    AudioGenerationError,
    AudioProviderUnavailableError,
    MockAudioProvider,
    build_audio_provider,
    generate_audio_for_approved_script,
    validate_audio_file,
)
from app.database.db import get_script_by_id, review_script, save_script
from app.schemas.script_schema import ScriptRequest


def _save_script(database_path: Path, *, approved: bool) -> dict:
    script = MockScriptGenerator().generate(ScriptRequest(topic="audio safety"))
    evaluation = evaluate_script(script)
    record = save_script(script, evaluation=evaluation, database_path=database_path)
    if approved:
        review_script(record["id"], "approved", "audio-test-reviewer", database_path)
    return get_script_by_id(record["id"], database_path)


def _write_wav(path: Path, *, frames: int = 100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio_file:
        audio_file.setnchannels(1)
        audio_file.setsampwidth(2)
        audio_file.setframerate(8000)
        audio_file.writeframes(b"\x01\x00" * frames)


def test_mock_provider_writes_labeled_deterministic_tone(tmp_path) -> None:
    provider = build_audio_provider("mock")
    first = provider.generate("Approved sample narration", tmp_path / "first.wav")
    second = provider.generate("Approved sample narration", tmp_path / "second.wav")

    assert isinstance(provider, MockAudioProvider)
    assert first.read_bytes() == second.read_bytes()
    result = validate_audio_file(first, provider=provider.provider_name)
    assert result.format == "wav"
    assert result.duration_seconds == pytest.approx(0.5)
    assert result.provider == "deterministic mock tone (not speech)"
    assert result.size_bytes > 0


def test_mock_provider_rejects_empty_text_and_refuses_overwrite(tmp_path) -> None:
    provider = MockAudioProvider()
    with pytest.raises(AudioGenerationError, match="must not be empty"):
        provider.generate("  ", tmp_path / "empty.wav")

    output = tmp_path / "existing.wav"
    output.write_bytes(b"keep this user data")
    with pytest.raises(AudioGenerationError, match="refusing to overwrite"):
        provider.generate("narration", output)
    assert output.read_bytes() == b"keep this user data"


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("missing.wav", "missing"),
        ("empty.wav", "empty"),
        ("invalid.txt", "Unsupported audio format"),
        ("invalid.wav", "not a valid WAV"),
    ],
)
def test_validator_rejects_missing_empty_and_unsupported_files(
    tmp_path, path: str, message: str
) -> None:
    audio_path = tmp_path / path
    if path == "empty.wav":
        audio_path.write_bytes(b"")
    elif path == "invalid.txt":
        audio_path.write_bytes(b"not audio")
    elif path == "invalid.wav":
        audio_path.write_bytes(b"not a RIFF WAV file")

    with pytest.raises(AudioGenerationError, match=message):
        validate_audio_file(audio_path)


def test_validator_rejects_oversized_and_zero_duration_audio(tmp_path) -> None:
    oversized = tmp_path / "oversized.wav"
    _write_wav(oversized)
    with pytest.raises(AudioGenerationError, match="exceeds the configured"):
        validate_audio_file(oversized, max_size_bytes=oversized.stat().st_size - 1)

    zero_duration = tmp_path / "zero-duration.wav"
    _write_wav(zero_duration, frames=0)
    with pytest.raises(AudioGenerationError, match="positive duration"):
        validate_audio_file(zero_duration)


def test_validator_accepts_supported_compressed_audio_with_matching_container(tmp_path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg is required for this compressed-format fixture.")
    audio_path = tmp_path / "tone.mp3"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.5",
            "-codec:a",
            "libmp3lame",
            str(audio_path),
        ],
        check=True,
        capture_output=True,
    )

    result = validate_audio_file(audio_path)

    assert result.format == "mp3"
    assert result.duration_seconds is not None and result.duration_seconds > 0


def test_unavailable_provider_fails_without_falling_back_to_real_tts(monkeypatch) -> None:
    monkeypatch.delenv("MOTIVATIONOS_AUDIO_PROVIDER", raising=False)
    with pytest.raises(AudioProviderUnavailableError, match="No audio provider selected"):
        build_audio_provider()
    with pytest.raises(AudioProviderUnavailableError, match="unavailable"):
        build_audio_provider("elevenlabs")


def test_unapproved_script_cannot_generate_audio_or_change_review_state(tmp_path) -> None:
    database_path = tmp_path / "scripts.sqlite3"
    record = _save_script(database_path, approved=False)
    output = tmp_path / "audio" / "blocked.wav"
    provider = MockAudioProvider()

    with pytest.raises(AudioGenerationError, match="Explicit human approval"):
        generate_audio_for_approved_script(
            record["id"], database_path, output, provider
        )

    assert not output.exists()
    assert get_script_by_id(record["id"], database_path)["status"] == "needs_review"


def test_approved_generation_does_not_change_review_or_create_production_rows(tmp_path) -> None:
    database_path = tmp_path / "scripts.sqlite3"
    record = _save_script(database_path, approved=True)
    output = tmp_path / "audio" / "approved.wav"
    result = generate_audio_for_approved_script(
        record["id"], database_path, output, MockAudioProvider()
    )
    after = get_script_by_id(record["id"], database_path)

    assert Path(result.path) == output
    assert result.provider == "deterministic mock tone (not speech)"
    assert after["status"] == "approved"
    assert after["reviewed_at"] == record["reviewed_at"]
    assert after["reviewed_by"] == record["reviewed_by"]
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM productions").fetchone()[0] == 0
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if "youtube_uploads" in tables:
            assert connection.execute("SELECT COUNT(*) FROM youtube_uploads").fetchone()[0] == 0


def test_invalid_provider_output_is_removed_after_validation_failure(tmp_path) -> None:
    class InvalidAudioProvider:
        provider_name = "test invalid provider"

        def generate(self, approved_script_text: str, output_path: str | Path) -> Path:
            Path(output_path).write_text("not audio", encoding="utf-8")
            return Path(output_path)

    database_path = tmp_path / "scripts.sqlite3"
    record = _save_script(database_path, approved=True)
    output = tmp_path / "audio" / "invalid.wav"

    with pytest.raises(AudioGenerationError, match="not a valid WAV"):
        generate_audio_for_approved_script(
            record["id"], database_path, output, InvalidAudioProvider()
        )
    assert not output.exists()