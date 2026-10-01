"""Offline tests for deterministic signal-level audio QA."""

import math
import shutil
import sqlite3
import subprocess
import wave
from pathlib import Path

import pytest

import app.audio.quality as audio_quality
from app.audio.generation import AudioProviderUnavailableError
from app.audio.quality import (
    AudioQAThresholds,
    run_audio_quality_assurance,
)


def _write_pcm_wav(path: Path, samples: list[int], sample_rate: int = 8000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio_file:
        audio_file.setnchannels(1)
        audio_file.setsampwidth(2)
        audio_file.setframerate(sample_rate)
        audio_file.writeframes(b"".join(sample.to_bytes(2, "little", signed=True) for sample in samples))


def _tone_samples(duration_seconds: float, sample_rate: int = 8000) -> list[int]:
    return [
        int(6000 * math.sin(2 * math.pi * 440 * index / sample_rate))
        for index in range(round(duration_seconds * sample_rate))
    ]


def _write_mp3(path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg is required for this compressed-audio fixture.")
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
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def test_valid_wav_reports_measurements_and_passes_technical_checks(tmp_path) -> None:
    audio_path = tmp_path / "clean-tone.wav"
    _write_pcm_wav(audio_path, _tone_samples(0.5))

    report = run_audio_quality_assurance(audio_path, target_duration_seconds=0.5)

    assert report.file_valid is True
    assert report.detected_format == "wav"
    assert report.duration_seconds == pytest.approx(0.5)
    assert report.file_size_bytes == audio_path.stat().st_size
    assert report.silence_check_status == "checked"
    assert report.silence_detected is False
    assert report.clipping_check_status == "checked"
    assert report.clipping_detected is False
    assert report.duration_deviation_seconds == 0
    assert report.within_target_duration is True
    assert report.technical_status == "passed"
    assert any("do not verify pronunciation" in finding for finding in report.findings)


def test_valid_mp3_uses_ffmpeg_sample_analysis_when_available(tmp_path) -> None:
    audio_path = tmp_path / "clean-tone.mp3"
    _write_mp3(audio_path)

    report = run_audio_quality_assurance(audio_path)

    assert report.file_valid is True
    assert report.detected_format == "mp3"
    assert report.duration_seconds is not None and report.duration_seconds > 0
    assert report.signal_analysis_approximate is True
    assert report.silence_check_status == "checked_approximate"
    assert report.clipping_check_status == "checked_approximate"
    assert report.technical_status == "needs_review"


@pytest.mark.parametrize(
    ("filename", "contents", "expected_finding"),
    [
        ("missing.wav", None, "missing"),
        ("empty.wav", b"", "empty"),
        ("broken.wav", b"not a RIFF WAV file", "not a valid WAV"),
        ("unsupported.xyz", b"audio-like bytes", "Unsupported audio format"),
    ],
)
def test_missing_empty_corrupt_and_unsupported_files_fail(
    tmp_path, filename: str, contents: bytes | None, expected_finding: str
) -> None:
    audio_path = tmp_path / filename
    if contents is not None:
        audio_path.write_bytes(contents)

    report = run_audio_quality_assurance(audio_path)

    assert report.file_valid is False
    assert report.technical_status == "failed"
    assert report.silence_check_status == "not_run"
    assert any(expected_finding in finding for finding in report.findings)


def test_too_short_positive_audio_requires_review(tmp_path) -> None:
    audio_path = tmp_path / "short.wav"
    _write_pcm_wav(audio_path, _tone_samples(0.05))

    report = run_audio_quality_assurance(audio_path)

    assert report.file_valid is True
    assert report.duration_seconds == pytest.approx(0.05)
    assert report.technical_status == "needs_review"
    assert any("shorter than" in finding for finding in report.findings)


def test_duration_outside_target_tolerance_requires_review(tmp_path) -> None:
    audio_path = tmp_path / "duration.wav"
    _write_pcm_wav(audio_path, _tone_samples(0.5))
    thresholds = AudioQAThresholds(target_duration_tolerance_seconds=0.1)

    report = run_audio_quality_assurance(
        audio_path, target_duration_seconds=2.0, thresholds=thresholds
    )

    assert report.duration_deviation_seconds == pytest.approx(-1.5)
    assert report.within_target_duration is False
    assert report.technical_status == "needs_review"
    assert any("Duration differs" in finding for finding in report.findings)


def test_long_silence_is_reported_without_repairing_audio(tmp_path) -> None:
    audio_path = tmp_path / "silence.wav"
    _write_pcm_wav(audio_path, [0] * 4000)
    before = audio_path.read_bytes()

    report = run_audio_quality_assurance(audio_path)

    assert report.silence_detected is True
    assert len(report.silence_segments) == 1
    assert report.silence_segments[0].duration_seconds == pytest.approx(0.5)
    assert report.technical_status == "needs_review"
    assert audio_path.read_bytes() == before


def test_entirely_silent_short_clip_is_still_flagged(tmp_path) -> None:
    audio_path = tmp_path / "short-silence.wav"
    _write_pcm_wav(audio_path, [0] * 800)

    report = run_audio_quality_assurance(audio_path)

    assert report.duration_seconds == pytest.approx(0.1)
    assert report.silence_detected is True
    assert report.technical_status == "needs_review"


def test_clipping_is_reported_from_sample_peaks(tmp_path) -> None:
    audio_path = tmp_path / "clipped.wav"
    _write_pcm_wav(audio_path, [32767] * 4000)

    report = run_audio_quality_assurance(audio_path)

    assert report.clipping_detected is True
    assert report.clipped_sample_count == 4000
    assert report.peak_level == pytest.approx(32767 / 32768, abs=1e-6)
    assert report.technical_status == "needs_review"


def test_oversized_file_fails_with_configured_limit(tmp_path) -> None:
    audio_path = tmp_path / "oversized.wav"
    _write_pcm_wav(audio_path, _tone_samples(0.5))
    thresholds = AudioQAThresholds(max_file_size_bytes=audio_path.stat().st_size - 1)

    report = run_audio_quality_assurance(audio_path, thresholds=thresholds)

    assert report.file_valid is False
    assert report.technical_status == "failed"
    assert any("size limit" in finding for finding in report.findings)


def test_unavailable_signal_capability_is_not_reported_as_passed(tmp_path, monkeypatch) -> None:
    audio_path = tmp_path / "analysis-unavailable.mp3"
    _write_mp3(audio_path)

    def unavailable(*args, **kwargs):
        raise AudioProviderUnavailableError("FFmpeg is unavailable for this check.")

    monkeypatch.setattr(audio_quality, "_analyze_compressed", unavailable)
    report = run_audio_quality_assurance(audio_path)

    assert report.file_valid is True
    assert report.silence_check_status == "unavailable"
    assert report.silence_detected is None
    assert report.clipping_check_status == "unavailable"
    assert report.clipping_detected is None
    assert report.technical_status == "needs_review"
    assert any("unavailable" in finding for finding in report.findings)


def test_repeated_analysis_is_deterministic(tmp_path) -> None:
    audio_path = tmp_path / "repeatable.wav"
    _write_pcm_wav(audio_path, _tone_samples(0.5))

    first = run_audio_quality_assurance(audio_path, target_duration_seconds=0.5)
    second = run_audio_quality_assurance(audio_path, target_duration_seconds=0.5)

    assert first == second


def test_qa_does_not_modify_temporary_database_or_create_production_artifacts(tmp_path) -> None:
    database_path = tmp_path / "sentinel.sqlite3"
    audio_path = tmp_path / "input.wav"
    _write_pcm_wav(audio_path, _tone_samples(0.5))
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE productions (id TEXT PRIMARY KEY)")
        connection.execute("CREATE TABLE youtube_uploads (id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO productions VALUES ('keep-production')")
        connection.execute("INSERT INTO youtube_uploads VALUES ('keep-upload')")

    with sqlite3.connect(database_path) as connection:
        before = (
            connection.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall(),
            connection.execute("SELECT * FROM productions").fetchall(),
            connection.execute("SELECT * FROM youtube_uploads").fetchall(),
        )
    report = run_audio_quality_assurance(audio_path)
    with sqlite3.connect(database_path) as connection:
        after = (
            connection.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall(),
            connection.execute("SELECT * FROM productions").fetchall(),
            connection.execute("SELECT * FROM youtube_uploads").fetchall(),
        )

    assert report.file_valid is True
    assert before == after
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "input.wav",
        "sentinel.sqlite3",
    ]