"""Offline tests for approved-script audio generation and validation."""

import asyncio
import logging
import sqlite3
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agents.quality_agent import evaluate_script
from app.agents.script_agent import MockScriptGenerator
from app.audio.generation import (
    AudioGenerationError,
    AudioProviderUnavailableError,
    EdgeTTSAudioAdapter,
    MockAudioProvider,
    build_audio_provider,
    generate_audio_for_approved_script,
    validate_audio_file,
)
from app.database.db import get_script_by_id, review_script, save_script
from app.schemas.script_schema import ScriptRequest
from app.video.tts_engine import EdgeTTSProvider, TTSError, build_tts_provider


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


def _mock_edge_tts(monkeypatch, save_audio) -> list[dict]:
    calls = []

    class FakeCommunicate:
        def __init__(self, text, voice, **options):
            calls.append({"text": text, "voice": voice, "options": options})

        async def save(self, path):
            await save_audio(Path(path))

    monkeypatch.setitem(sys.modules, "edge_tts", SimpleNamespace(Communicate=FakeCommunicate))
    return calls


def _make_mp3(tmp_path: Path) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg is required for this mocked MP3 response fixture.")
    audio_path = tmp_path / "provider-response.mp3"
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
    return audio_path.read_bytes()


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
    monkeypatch.setenv("MOTIVATIONOS_TTS_PROVIDER", "elevenlabs")
    with pytest.raises(AudioProviderUnavailableError, match="No audio provider selected"):
        build_audio_provider()
    with pytest.raises(AudioProviderUnavailableError, match="unavailable"):
        build_audio_provider("elevenlabs")


def test_legacy_tts_provider_requires_explicit_selection(monkeypatch) -> None:
    monkeypatch.setenv("MOTIVATIONOS_TTS_PROVIDER", "")
    with pytest.raises(TTSError, match="must explicitly select"):
        build_tts_provider()


def test_edge_tts_adapter_uses_mock_response_and_preserves_approval_state(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "scripts.sqlite3"
    record = _save_script(database_path, approved=True)
    audio_bytes = _make_mp3(tmp_path)

    async def save_audio(path: Path) -> None:
        path.write_bytes(audio_bytes)

    calls = _mock_edge_tts(monkeypatch, save_audio)
    monkeypatch.setenv("MOTIVATIONOS_AUDIO_PROVIDER", "edge-tts")
    monkeypatch.setenv("MOTIVATIONOS_AUDIO_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("EDGE_TTS_VOICE", "mocked-voice")
    provider = build_audio_provider()
    output = tmp_path / "generated" / "narration.mp3"

    result = generate_audio_for_approved_script(
        record["id"], database_path, output, provider
    )

    assert isinstance(provider, EdgeTTSAudioAdapter)
    assert result.path == str(output.resolve())
    assert result.provider == "Microsoft Edge TTS"
    assert result.format == "mp3"
    assert result.duration_seconds is not None and result.duration_seconds > 0
    assert calls == [
        {
            "text": record["script"]["narration"],
            "voice": "mocked-voice",
            "options": {"connect_timeout": 10, "receive_timeout": 5},
        }
    ]
    after = get_script_by_id(record["id"], database_path)
    assert after["status"] == "approved"
    assert after["reviewed_at"] == record["reviewed_at"]
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM productions").fetchone()[0] == 0
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if "youtube_uploads" in tables:
            assert connection.execute("SELECT COUNT(*) FROM youtube_uploads").fetchone()[0] == 0


def test_existing_edge_synthesize_script_interface_remains_compatible(
    tmp_path, monkeypatch
) -> None:
    script = MockScriptGenerator().generate(ScriptRequest(topic="audio safety"))
    audio_bytes = _make_mp3(tmp_path)

    async def save_audio(path: Path) -> None:
        path.write_bytes(audio_bytes)

    calls = _mock_edge_tts(monkeypatch, save_audio)
    provider = EdgeTTSProvider(voice="mocked-voice", timeout_seconds=5)
    output = tmp_path / "existing-api.mp3"

    result = provider.synthesize(script, output)

    assert result.path == str(output.resolve())
    assert result.provider == "Microsoft Edge TTS"
    assert result.duration_seconds > 0
    assert calls[0]["text"] == script.narration


def test_edge_tts_requires_explicit_voice_configuration_and_valid_timeout(monkeypatch) -> None:
    monkeypatch.setenv("MOTIVATIONOS_AUDIO_PROVIDER", "edge-tts")
    monkeypatch.setenv("EDGE_TTS_VOICE", "")
    with pytest.raises(AudioGenerationError, match="EDGE_TTS_VOICE"):
        build_audio_provider()

    monkeypatch.setenv("EDGE_TTS_VOICE", "mocked-voice")
    monkeypatch.setenv("MOTIVATIONOS_AUDIO_TIMEOUT_SECONDS", "invalid")
    with pytest.raises(AudioGenerationError, match="MOTIVATIONOS_AUDIO_TIMEOUT_SECONDS"):
        build_audio_provider()


def test_edge_tts_timeout_is_reported_without_leaking_provider_error(tmp_path, monkeypatch) -> None:
    async def never_finishes(path: Path) -> None:
        await asyncio.sleep(1)

    _mock_edge_tts(monkeypatch, never_finishes)
    provider = EdgeTTSAudioAdapter("mocked-voice", timeout_seconds=0.01)
    output = tmp_path / "timeout.mp3"

    with pytest.raises(AudioGenerationError, match="request timed out"):
        provider.generate("approved text", output)
    assert not output.exists()


def test_edge_tts_provider_error_is_sanitized_and_not_logged(tmp_path, monkeypatch, caplog) -> None:
    secret_marker = "mock-secret-marker"

    async def fail_provider(path: Path) -> None:
        raise RuntimeError(secret_marker)

    _mock_edge_tts(monkeypatch, fail_provider)
    provider = EdgeTTSAudioAdapter("mocked-voice", timeout_seconds=1)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(AudioGenerationError, match="request failed") as raised:
        provider.generate("approved text", tmp_path / "failure.mp3")

    assert secret_marker not in str(raised.value)
    assert secret_marker not in caplog.text


@pytest.mark.parametrize("response", [b"", b"invalid audio response"])
def test_edge_tts_rejects_empty_or_invalid_audio_response(
    tmp_path, monkeypatch, response: bytes
) -> None:
    async def write_response(path: Path) -> None:
        path.write_bytes(response)

    _mock_edge_tts(monkeypatch, write_response)
    provider = EdgeTTSAudioAdapter("mocked-voice", timeout_seconds=1)
    output = tmp_path / "response.mp3"

    with pytest.raises(AudioGenerationError):
        provider.generate("approved text", output)
    assert not output.exists()


def test_edge_tts_oversized_response_is_rejected_by_approved_script_service(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "scripts.sqlite3"
    record = _save_script(database_path, approved=True)
    audio_bytes = _make_mp3(tmp_path)

    async def save_audio(path: Path) -> None:
        path.write_bytes(audio_bytes)

    _mock_edge_tts(monkeypatch, save_audio)
    provider = EdgeTTSAudioAdapter("mocked-voice", timeout_seconds=1)
    output = tmp_path / "oversized.mp3"

    with pytest.raises(AudioGenerationError, match="exceeds the configured"):
        generate_audio_for_approved_script(
            record["id"], database_path, output, provider,
            max_size_bytes=len(audio_bytes) - 1,
        )
    assert not output.exists()


def test_unapproved_script_does_not_invoke_edge_provider(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "scripts.sqlite3"
    record = _save_script(database_path, approved=False)
    calls = _mock_edge_tts(monkeypatch, lambda path: pytest.fail("provider must not be called"))
    provider = EdgeTTSAudioAdapter("mocked-voice", timeout_seconds=1)
    output = tmp_path / "blocked.mp3"

    with pytest.raises(AudioGenerationError, match="Explicit human approval"):
        generate_audio_for_approved_script(
            record["id"], database_path, output, provider
        )

    assert calls == []
    assert not output.exists()


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