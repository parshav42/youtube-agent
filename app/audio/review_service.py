"""Opt-in orchestration for approved-script audio generation and human review."""

from dataclasses import dataclass
from pathlib import Path

from app.audio.generation import (
    AudioGenerationError,
    AudioGenerationResult,
    AudioProvider,
    generate_audio_for_approved_script,
)
from app.audio.quality import (
    DEFAULT_AUDIO_QA_THRESHOLDS,
    AudioQAReport,
    AudioQAThresholds,
    run_audio_quality_assurance,
)


@dataclass(frozen=True)
class AudioReviewResult:
    """Generated asset metadata and its read-only QA report for human review."""

    script_id: int
    generation: AudioGenerationResult
    quality: AudioQAReport

    @property
    def technical_status(self) -> str:
        """Expose the QA engine's status without inventing a separate decision."""
        return self.quality.technical_status


def generate_audio_for_human_review(
    script_id: int,
    database_path: str | Path,
    output_directory: str | Path,
    output_filename: str,
    provider: AudioProvider,
    *,
    target_duration_seconds: float | None = None,
    thresholds: AudioQAThresholds = DEFAULT_AUDIO_QA_THRESHOLDS,
) -> AudioReviewResult:
    """Generate and analyze audio for an approved script without recording a decision.

    The caller explicitly supplies its database, destination directory/name, and provider.
    No production job is created, and the script's review fields are only read.
    """
    if (
        not isinstance(output_filename, str)
        or not output_filename.strip()
        or output_filename in {".", ".."}
        or Path(output_filename).name != output_filename
        or "/" in output_filename
        or "\\" in output_filename
    ):
        raise AudioGenerationError("output_filename must be a simple filename, not a path.")

    destination = Path(output_directory) / output_filename
    if destination.exists() or destination.is_symlink():
        raise AudioGenerationError("Audio output already exists; refusing to overwrite it.")

    generation = generate_audio_for_approved_script(
        script_id,
        database_path,
        destination,
        provider,
    )
    quality = run_audio_quality_assurance(
        generation.path,
        target_duration_seconds,
        thresholds=thresholds,
    )
    return AudioReviewResult(
        script_id=script_id,
        generation=generation,
        quality=quality,
    )