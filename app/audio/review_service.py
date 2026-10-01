"""Opt-in orchestration for approved-script audio generation and human review."""

from dataclasses import dataclass
import math
from pathlib import Path

from app.audio.generation import (
    ApprovedScriptNotFoundError,
    AudioGenerationError,
    AudioGenerationResult,
    AudioProvider,
    AudioProviderTimeoutError,
    AudioProviderUnavailableError,
    ScriptApprovalRequiredError,
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
    """Structured generation, QA, and orchestration outcome for human review."""

    script_id: int
    provider_type: str
    workflow_status: str
    generation_status: str
    generation: AudioGenerationResult | None
    quality: AudioQAReport | None
    error: str | None = None

    @property
    def qa_status(self) -> str:
        """Return the QA engine status, or not_run when no report was produced."""
        return self.quality.technical_status if self.quality else "not_run"

    @property
    def technical_status(self) -> str:
        """Backward-compatible alias for the QA engine's status."""
        return self.qa_status

    @property
    def findings(self) -> tuple[str, ...]:
        if self.quality is not None:
            return self.quality.findings
        return (self.error,) if self.error else ()


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

    Expected workflow failures are returned as safe structured outcomes; validation of
    the caller-supplied path/QA settings is reported as blocked before provider invocation.
    """
    provider_type = type(provider).__name__
    if (
        not isinstance(output_filename, str)
        or not output_filename.strip()
        or output_filename in {".", ".."}
        or Path(output_filename).name != output_filename
        or "/" in output_filename
        or "\\" in output_filename
    ):
        return _result(
            script_id,
            provider_type,
            workflow_status="blocked",
            generation_status="not_run",
            error="Choose a simple output filename within the supplied directory.",
        )

    try:
        thresholds.validate()
        if target_duration_seconds is not None and (
            isinstance(target_duration_seconds, bool)
            or not isinstance(target_duration_seconds, (int, float))
            or not math.isfinite(target_duration_seconds)
            or target_duration_seconds <= 0
        ):
            raise ValueError("invalid target duration")
        destination = Path(output_directory) / output_filename
        output_exists = destination.exists() or destination.is_symlink()
    except (OSError, TypeError, ValueError, AttributeError):
        return _result(
            script_id,
            provider_type,
            workflow_status="blocked",
            generation_status="not_run",
            error="Audio output or QA settings could not be validated.",
        )

    if output_exists:
        return _result(
            script_id,
            provider_type,
            workflow_status="blocked",
            generation_status="not_run",
            error="Audio output already exists; choose a new filename.",
        )

    try:
        generation = generate_audio_for_approved_script(
            script_id,
            database_path,
            destination,
            provider,
        )
    except ApprovedScriptNotFoundError:
        return _result(
            script_id,
            provider_type,
            workflow_status="blocked",
            generation_status="not_run",
            error="Script was not found.",
        )
    except ScriptApprovalRequiredError:
        return _result(
            script_id,
            provider_type,
            workflow_status="blocked",
            generation_status="not_run",
            error="Explicit human script approval is required before audio generation.",
        )
    except AudioProviderUnavailableError:
        return _result(
            script_id,
            provider_type,
            workflow_status="failed",
            generation_status="unavailable",
            error="The selected audio provider or required media validator is unavailable.",
        )
    except AudioProviderTimeoutError:
        return _result(
            script_id,
            provider_type,
            workflow_status="failed",
            generation_status="timed_out",
            error="The selected audio provider timed out.",
        )
    except AudioGenerationError:
        return _result(
            script_id,
            provider_type,
            workflow_status="failed",
            generation_status="failed",
            error="Audio generation or file validation failed.",
        )
    except Exception:
        return _result(
            script_id,
            provider_type,
            workflow_status="failed",
            generation_status="failed",
            error="Audio generation could not access a required local resource.",
        )

    try:
        quality = run_audio_quality_assurance(
            generation.path,
            target_duration_seconds,
            thresholds=thresholds,
        )
    except Exception:
        return _result(
            script_id,
            provider_type,
            workflow_status="failed",
            generation_status="generated",
            generation=generation,
            error="Audio was generated, but QA could not complete; human review is required.",
        )
    return _result(
        script_id,
        provider_type,
        workflow_status="completed",
        generation_status="generated",
        generation=generation,
        quality=quality,
    )


def _result(
    script_id: int,
    provider_type: str,
    *,
    workflow_status: str,
    generation_status: str,
    generation: AudioGenerationResult | None = None,
    quality: AudioQAReport | None = None,
    error: str | None = None,
) -> AudioReviewResult:
    return AudioReviewResult(
        script_id=script_id,
        provider_type=provider_type,
        workflow_status=workflow_status,
        generation_status=generation_status,
        generation=generation,
        quality=quality,
        error=error,
    )