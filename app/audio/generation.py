"""Provider-independent audio generation and validation primitives.

The mock provider writes a deterministic tone for tests; it does not generate speech.
"""

import hashlib
import json
import math
import os
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from app.database.db import get_script_by_id
from app.schemas.script_schema import MotivationalScript, ScriptEvaluation

DEFAULT_MAX_AUDIO_BYTES = 25 * 1024 * 1024
SUPPORTED_AUDIO_FORMATS = frozenset({".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"})


class AudioGenerationError(RuntimeError):
    """Raised when audio cannot be safely generated or validated."""


class AudioProviderUnavailableError(AudioGenerationError):
    """Raised when the requested provider is not implemented or unavailable."""


class AudioProvider(Protocol):
    """Provider contract for audio generated from approved script text."""

    provider_name: str

    def generate(self, approved_script_text: str, output_path: str | Path) -> Path:
        """Write audio without replacing an existing file and return its path."""


@dataclass(frozen=True)
class AudioGenerationResult:
    path: str
    provider: str
    format: str
    size_bytes: int
    duration_seconds: float | None
    codec: str


class MockAudioProvider:
    """Write a deterministic tone WAV fixture; this is not real speech."""

    provider_name = "deterministic mock tone (not speech)"
    sample_rate = 8000
    duration_seconds = 0.5

    def generate(self, approved_script_text: str, output_path: str | Path) -> Path:
        if not isinstance(approved_script_text, str) or not approved_script_text.strip():
            raise AudioGenerationError("Approved script text must not be empty.")
        destination = Path(output_path)
        if destination.suffix.lower() != ".wav":
            raise AudioGenerationError("The mock audio provider only writes .wav files.")
        if destination.exists():
            raise AudioGenerationError("Audio output already exists; refusing to overwrite it.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        seed = hashlib.sha256(approved_script_text.encode("utf-8")).digest()
        frequency = 220 + int.from_bytes(seed[:2], "big") % 440
        frame_count = int(self.sample_rate * self.duration_seconds)
        try:
            with wave.open(str(destination), "wb") as audio_file:
                audio_file.setnchannels(1)
                audio_file.setsampwidth(2)
                audio_file.setframerate(self.sample_rate)
                samples = bytearray()
                for frame in range(frame_count):
                    sample = int(8000 * math.sin(2 * math.pi * frequency * frame / self.sample_rate))
                    samples.extend(sample.to_bytes(2, byteorder="little", signed=True))
                audio_file.writeframes(samples)
        except (OSError, wave.Error) as error:
            raise AudioGenerationError("Unable to write the mock WAV audio file.") from error
        return destination


def build_audio_provider(provider_name: str | None = None) -> AudioProvider:
    """Return only an explicitly selected provider; never select a real TTS service."""
    selected = (
        provider_name
        if provider_name is not None
        else os.getenv("MOTIVATIONOS_AUDIO_PROVIDER", "")
    ).strip().lower()
    if selected == "mock":
        return MockAudioProvider()
    if not selected:
        raise AudioProviderUnavailableError(
            "No audio provider selected. Explicitly choose 'mock' or provide an implemented provider."
        )
    raise AudioProviderUnavailableError(
        "The requested audio provider is unavailable in the safe audio module."
    )


def validate_audio_file(
    path: str | Path,
    *,
    provider: str = "unknown",
    max_size_bytes: int = DEFAULT_MAX_AUDIO_BYTES,
) -> AudioGenerationResult:
    """Reject missing, empty, unsupported, oversized, or invalid audio files."""
    if not isinstance(max_size_bytes, int) or isinstance(max_size_bytes, bool) or max_size_bytes < 1:
        raise ValueError("max_size_bytes must be a positive integer.")
    audio_path = Path(path)
    if not audio_path.is_file():
        raise AudioGenerationError("Audio file is missing.")
    try:
        size_bytes = audio_path.stat().st_size
    except OSError as error:
        raise AudioGenerationError("Audio file could not be inspected.") from error
    if size_bytes == 0:
        raise AudioGenerationError("Audio file is empty.")
    if size_bytes > max_size_bytes:
        raise AudioGenerationError(
            f"Audio file exceeds the configured {max_size_bytes}-byte size limit."
        )
    audio_format = audio_path.suffix.lower()
    if audio_format not in SUPPORTED_AUDIO_FORMATS:
        raise AudioGenerationError(f"Unsupported audio format: {audio_format or 'unknown'}.")
    if audio_format == ".wav":
        duration, codec = _inspect_wav(audio_path)
    else:
        duration, codec = _inspect_with_ffprobe(audio_path)
    return AudioGenerationResult(
        path=str(audio_path.resolve()),
        provider=provider,
        format=audio_format.removeprefix("."),
        size_bytes=size_bytes,
        duration_seconds=duration,
        codec=codec,
    )


def _inspect_wav(audio_path: Path) -> tuple[float, str]:
    try:
        with wave.open(str(audio_path), "rb") as audio_file:
            frames = audio_file.getnframes()
            frame_rate = audio_file.getframerate()
            channels = audio_file.getnchannels()
            sample_width = audio_file.getsampwidth()
            if audio_file.getcomptype() != "NONE" or min(frames, frame_rate, channels, sample_width) <= 0:
                raise AudioGenerationError("WAV must contain uncompressed audio with a positive duration.")
            raw_frames = audio_file.readframes(frames)
            if len(raw_frames) != frames * channels * sample_width:
                raise AudioGenerationError("WAV audio data is truncated.")
            duration = frames / frame_rate
    except (OSError, EOFError, wave.Error) as error:
        raise AudioGenerationError("Audio file is not a valid WAV stream.") from error
    if not math.isfinite(duration) or duration <= 0:
        raise AudioGenerationError("Audio duration must be positive when measurable.")
    return duration, f"pcm_{sample_width * 8}bit"


def _inspect_with_ffprobe(audio_path: Path) -> tuple[float | None, str]:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise AudioProviderUnavailableError(
            "FFprobe is required to validate this compressed audio format."
        )
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,codec_name,duration:format=format_name,duration",
                "-of",
                "json",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        metadata = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise AudioGenerationError("Audio file could not be validated by FFprobe.") from error
    stream = next(
        (entry for entry in metadata.get("streams", []) if entry.get("codec_type") == "audio"),
        None,
    )
    if stream is None:
        raise AudioGenerationError("File does not contain a readable audio stream.")
    containers = {
        ".aac": {"aac"},
        ".flac": {"flac"},
        ".m4a": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
        ".mp3": {"mp3"},
        ".ogg": {"ogg"},
    }
    actual_containers = set(
        metadata.get("format", {}).get("format_name", "").split(",")
    )
    if not actual_containers.intersection(containers[audio_path.suffix.lower()]):
        raise AudioGenerationError("Audio container does not match its file extension.")
    duration_value = stream.get("duration") or metadata.get("format", {}).get("duration")
    duration: float | None = None
    if duration_value not in (None, "", "N/A"):
        try:
            duration = float(duration_value)
        except (TypeError, ValueError) as error:
            raise AudioGenerationError("Audio duration could not be interpreted.") from error
        if not math.isfinite(duration) or duration <= 0:
            raise AudioGenerationError("Audio duration must be positive when measurable.")
    return duration, str(stream.get("codec_name") or "unknown")


def generate_audio_for_approved_script(
    script_id: int,
    database_path: str | Path,
    output_path: str | Path,
    provider: AudioProvider,
    *,
    max_size_bytes: int = DEFAULT_MAX_AUDIO_BYTES,
) -> AudioGenerationResult:
    """Generate audio only for an explicitly reviewed, quality-passing script.

    The database path is required so callers cannot silently target the project DB.
    This function reads script/review state but never updates it or creates production rows.
    """
    record = get_script_by_id(script_id, database_path)
    if record is None:
        raise AudioGenerationError(f"Script record {script_id} was not found.")
    if record["status"] != "approved":
        raise AudioGenerationError("Explicit human approval is required before audio generation.")
    if not record["reviewed_at"] or not record["reviewed_by"]:
        raise AudioGenerationError("Approved script is missing its human-review audit metadata.")
    try:
        evaluation = ScriptEvaluation.model_validate(record["evaluation"])
        script = MotivationalScript.model_validate(record["script"])
    except (ValidationError, TypeError, ValueError) as error:
        raise AudioGenerationError("Approved script or quality evaluation is invalid.") from error
    if not evaluation.passed or not all(
        value
        for name, value in evaluation.model_dump().items()
        if name not in {"quality_score", "threshold", "passed", "feedback"}
    ):
        raise AudioGenerationError("Approved script does not have a passing quality evaluation.")
    if not script.narration.strip():
        raise AudioGenerationError("Approved script narration is empty.")

    destination = Path(output_path)
    if destination.exists():
        raise AudioGenerationError("Audio output already exists; refusing to overwrite it.")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise AudioGenerationError("Could not prepare the audio output directory.") from error
    try:
        generated_path = Path(provider.generate(script.narration, destination))
        if generated_path.resolve() != destination.resolve():
            raise AudioGenerationError("Audio provider returned an unexpected output path.")
        return validate_audio_file(
            generated_path,
            provider=provider.provider_name,
            max_size_bytes=max_size_bytes,
        )
    except AudioGenerationError:
        if destination.is_file():
            destination.unlink()
        raise
    except Exception as error:
        if destination.is_file():
            destination.unlink()
        raise AudioGenerationError("Audio provider failed to generate a valid file.") from error