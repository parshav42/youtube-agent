"""Offline signal-level quality checks for generated audio assets.

These checks assess technical properties only; they do not judge speech quality.
"""

import math
import shutil
import struct
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from app.audio.generation import (
    AudioGenerationError,
    AudioProviderUnavailableError,
    DEFAULT_MAX_AUDIO_BYTES,
    validate_audio_file,
)

DEFAULT_MIN_AUDIO_DURATION_SECONDS = 0.1
DEFAULT_TARGET_DURATION_TOLERANCE_SECONDS = 2.0
DEFAULT_SILENCE_THRESHOLD_DBFS = -50.0
DEFAULT_SILENCE_MIN_DURATION_SECONDS = 0.25
DEFAULT_CLIPPING_PEAK_THRESHOLD = 0.99
DEFAULT_ANALYSIS_SAMPLE_RATE = 8000
DEFAULT_ANALYSIS_WINDOW_SECONDS = 0.01
DEFAULT_MAX_SIGNAL_ANALYSIS_SECONDS = 120.0
DEFAULT_FFMPEG_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class AudioQAThresholds:
    """Configurable technical thresholds; see module defaults above."""

    max_file_size_bytes: int = DEFAULT_MAX_AUDIO_BYTES
    min_audio_duration_seconds: float = DEFAULT_MIN_AUDIO_DURATION_SECONDS
    target_duration_tolerance_seconds: float = DEFAULT_TARGET_DURATION_TOLERANCE_SECONDS
    silence_threshold_dbfs: float = DEFAULT_SILENCE_THRESHOLD_DBFS
    silence_min_duration_seconds: float = DEFAULT_SILENCE_MIN_DURATION_SECONDS
    clipping_peak_threshold: float = DEFAULT_CLIPPING_PEAK_THRESHOLD
    analysis_sample_rate: int = DEFAULT_ANALYSIS_SAMPLE_RATE
    analysis_window_seconds: float = DEFAULT_ANALYSIS_WINDOW_SECONDS
    max_signal_analysis_seconds: float = DEFAULT_MAX_SIGNAL_ANALYSIS_SECONDS
    ffmpeg_timeout_seconds: int = DEFAULT_FFMPEG_TIMEOUT_SECONDS

    def validate(self) -> None:
        if (
            not isinstance(self.max_file_size_bytes, int)
            or isinstance(self.max_file_size_bytes, bool)
            or self.max_file_size_bytes < 1
        ):
            raise ValueError("max_file_size_bytes must be positive.")
        if not math.isfinite(self.min_audio_duration_seconds) or self.min_audio_duration_seconds <= 0:
            raise ValueError("min_audio_duration_seconds must be positive.")
        if (
            not math.isfinite(self.target_duration_tolerance_seconds)
            or self.target_duration_tolerance_seconds < 0
        ):
            raise ValueError("target_duration_tolerance_seconds cannot be negative.")
        if not math.isfinite(self.silence_threshold_dbfs) or self.silence_threshold_dbfs >= 0:
            raise ValueError("silence_threshold_dbfs must be below 0 dBFS.")
        if (
            not math.isfinite(self.silence_min_duration_seconds)
            or self.silence_min_duration_seconds <= 0
        ):
            raise ValueError("silence_min_duration_seconds must be positive.")
        if (
            not math.isfinite(self.clipping_peak_threshold)
            or not 0 < self.clipping_peak_threshold <= 1
        ):
            raise ValueError("clipping_peak_threshold must be in (0, 1].")
        if (
            not isinstance(self.analysis_sample_rate, int)
            or isinstance(self.analysis_sample_rate, bool)
            or self.analysis_sample_rate < 1
        ):
            raise ValueError("analysis_sample_rate must be positive.")
        if not math.isfinite(self.analysis_window_seconds) or self.analysis_window_seconds <= 0:
            raise ValueError("analysis_window_seconds must be positive.")
        if not math.isfinite(self.max_signal_analysis_seconds) or self.max_signal_analysis_seconds <= 0:
            raise ValueError("max_signal_analysis_seconds must be positive.")
        if (
            not isinstance(self.ffmpeg_timeout_seconds, int)
            or isinstance(self.ffmpeg_timeout_seconds, bool)
            or self.ffmpeg_timeout_seconds < 1
        ):
            raise ValueError("ffmpeg_timeout_seconds must be positive.")


@dataclass(frozen=True)
class SilenceSegment:
    start_seconds: float
    end_seconds: float
    duration_seconds: float


@dataclass(frozen=True)
class AudioQAReport:
    file_valid: bool | None
    detected_format: str | None
    duration_seconds: float | None
    file_size_bytes: int | None
    signal_analysis_approximate: bool | None
    silence_check_status: str
    silence_detected: bool | None
    silence_segments: tuple[SilenceSegment, ...]
    clipping_check_status: str
    clipping_detected: bool | None
    clipped_sample_count: int | None
    peak_level: float | None
    duration_deviation_seconds: float | None
    within_target_duration: bool | None
    technical_status: str
    findings: tuple[str, ...]


@dataclass(frozen=True)
class _SignalMetrics:
    silence_segments: tuple[SilenceSegment, ...]
    clipped_sample_count: int
    peak_level: float
    analyzed_duration_seconds: float
    approximate: bool


_TECHNICAL_LIMITATION = (
    "Signal-level checks do not verify pronunciation, factual correctness, "
    "or voice naturalness."
)


def run_audio_quality_assurance(
    audio_path: str | Path,
    target_duration_seconds: float | None = None,
    *,
    thresholds: AudioQAThresholds = AudioQAThresholds(),
) -> AudioQAReport:
    """Analyze one local audio file without modifying it or accessing a database."""
    thresholds.validate()
    if target_duration_seconds is not None and (
        not math.isfinite(target_duration_seconds) or target_duration_seconds <= 0
    ):
        raise ValueError("target_duration_seconds must be a positive finite number.")

    path = Path(audio_path)
    try:
        if not path.is_file():
            return _failed_report(None, None, ["Audio file is missing."])
        size_bytes = path.stat().st_size
    except OSError:
        return _failed_report(None, None, ["Audio file could not be inspected."])
    if size_bytes == 0:
        return _failed_report(False, size_bytes, ["Audio file is empty."])
    if size_bytes > thresholds.max_file_size_bytes:
        return _failed_report(
            False,
            size_bytes,
            [f"Audio file exceeds the {thresholds.max_file_size_bytes}-byte size limit."],
        )

    try:
        audio = validate_audio_file(path, max_size_bytes=thresholds.max_file_size_bytes)
    except AudioProviderUnavailableError as error:
        return AudioQAReport(
            file_valid=None,
            detected_format=None,
            duration_seconds=None,
            file_size_bytes=size_bytes,
            signal_analysis_approximate=None,
            silence_check_status="unavailable",
            silence_detected=None,
            silence_segments=(),
            clipping_check_status="unavailable",
            clipping_detected=None,
            clipped_sample_count=None,
            peak_level=None,
            duration_deviation_seconds=None,
            within_target_duration=None,
            technical_status="needs_review",
            findings=(
                f"File validation unavailable: {error}",
                "Signal checks were not run because the audio format could not be validated.",
                _TECHNICAL_LIMITATION,
            ),
        )
    except AudioGenerationError as error:
        return _failed_report(False, size_bytes, [str(error)])

    findings: list[str] = []
    duration = audio.duration_seconds
    deviation: float | None = None
    within_target: bool | None = None
    if duration is None:
        findings.append("Audio duration is unavailable from the installed validator.")
    elif duration < thresholds.min_audio_duration_seconds:
        findings.append(
            f"Audio is shorter than the {thresholds.min_audio_duration_seconds:g}-second minimum."
        )
    if target_duration_seconds is not None:
        if duration is None:
            findings.append("Target-duration comparison is unavailable because duration was not measured.")
        else:
            deviation = round(duration - target_duration_seconds, 6)
            tolerance = thresholds.target_duration_tolerance_seconds
            within_target = abs(deviation) <= tolerance
            if not within_target:
                findings.append(
                    f"Duration differs from the {target_duration_seconds:g}-second target "
                    f"by {deviation:+g} seconds (tolerance: +/-{tolerance:g} seconds)."
                )

    if duration is not None and duration > thresholds.max_signal_analysis_seconds:
        analysis_error: str | None = (
            "Signal checks unavailable: audio exceeds the configured complete-analysis duration limit."
        )
        metrics = None
    else:
        try:
            metrics = _analyze_signal(path, audio.format, thresholds)
            analysis_error = None
        except (AudioGenerationError, OSError, subprocess.SubprocessError, wave.Error) as error:
            metrics = None
            analysis_error = f"Signal checks unavailable: {error}"

    if metrics is None:
        signal_analysis_approximate = None
        silence_status = "unavailable"
        silence_detected = None
        silence_segments: tuple[SilenceSegment, ...] = ()
        clipping_status = "unavailable"
        clipping_detected = None
        clipped_sample_count = None
        peak_level = None
        findings.append(analysis_error or "Signal checks unavailable.")
    else:
        signal_analysis_approximate = metrics.approximate
        check_status = "checked_approximate" if metrics.approximate else "checked"
        silence_status = check_status
        silence_segments = metrics.silence_segments
        silence_detected = bool(silence_segments)
        clipping_status = check_status
        clipped_sample_count = metrics.clipped_sample_count
        clipping_detected = clipped_sample_count > 0
        peak_level = round(metrics.peak_level, 6)
        if silence_detected:
            findings.append(
                f"Detected {len(silence_segments)} low-level silence segment(s) "
                f"of at least {thresholds.silence_min_duration_seconds:g} seconds."
            )
        if clipping_detected:
            findings.append(
                f"Detected {clipped_sample_count} sample(s) at or above the "
                f"{thresholds.clipping_peak_threshold:g} full-scale clipping threshold."
            )
        if metrics.approximate:
            findings.append(
                "Compressed-audio signal metrics use FFmpeg mono downmix and 8 kHz resampling; results are approximate."
            )

    if not findings:
        findings.append("Audio passed the configured technical checks.")
    findings.append(_TECHNICAL_LIMITATION)
    needs_review = bool(
        duration is None
        or duration < thresholds.min_audio_duration_seconds
        or (target_duration_seconds is not None and within_target is not True)
        or metrics is None
        or (metrics is not None and metrics.approximate)
        or silence_detected
        or clipping_detected
    )
    return AudioQAReport(
        file_valid=True,
        detected_format=audio.format,
        duration_seconds=duration,
        file_size_bytes=size_bytes,
        signal_analysis_approximate=signal_analysis_approximate,
        silence_check_status=silence_status,
        silence_detected=silence_detected,
        silence_segments=silence_segments,
        clipping_check_status=clipping_status,
        clipping_detected=clipping_detected,
        clipped_sample_count=clipped_sample_count,
        peak_level=peak_level,
        duration_deviation_seconds=deviation,
        within_target_duration=within_target,
        technical_status="needs_review" if needs_review else "passed",
        findings=tuple(findings),
    )


def _failed_report(
    file_size_bytes: int | None,
    duration_seconds: float | None,
    findings: list[str],
) -> AudioQAReport:
    findings.append(_TECHNICAL_LIMITATION)
    return AudioQAReport(
        file_valid=False,
        detected_format=None,
        duration_seconds=duration_seconds,
        file_size_bytes=file_size_bytes,
        signal_analysis_approximate=None,
        silence_check_status="not_run",
        silence_detected=None,
        silence_segments=(),
        clipping_check_status="not_run",
        clipping_detected=None,
        clipped_sample_count=None,
        peak_level=None,
        duration_deviation_seconds=None,
        within_target_duration=None,
        technical_status="failed",
        findings=tuple(findings),
    )


def _analyze_signal(
    path: Path, detected_format: str, thresholds: AudioQAThresholds
) -> _SignalMetrics:
    if detected_format == "wav":
        return _analyze_wav(path, thresholds)
    return _analyze_compressed(path, thresholds)


def _analyze_wav(path: Path, thresholds: AudioQAThresholds) -> _SignalMetrics:
    with wave.open(str(path), "rb") as audio_file:
        sample_rate = audio_file.getframerate()
        channels = audio_file.getnchannels()
        sample_width = audio_file.getsampwidth()
        if sample_width not in {1, 2, 3, 4}:
            raise AudioGenerationError("WAV sample width is unsupported for signal analysis.")

        def samples() -> Iterable[float]:
            while raw_frames := audio_file.readframes(4096):
                yield from _decode_wav_samples(raw_frames, sample_width)

        return _measure_samples(samples(), sample_rate, channels, thresholds)


def _decode_wav_samples(raw_frames: bytes, sample_width: int) -> Iterable[float]:
    if sample_width == 1:
        for sample in raw_frames:
            yield (sample - 128) / 128
    elif sample_width == 2:
        for (sample,) in struct.iter_unpack("<h", raw_frames):
            yield sample / 32768
    elif sample_width == 3:
        for offset in range(0, len(raw_frames), 3):
            value = int.from_bytes(raw_frames[offset : offset + 3], "little", signed=False)
            if value & 0x800000:
                value -= 1 << 24
            yield value / 8388608
    else:
        for (sample,) in struct.iter_unpack("<i", raw_frames):
            yield sample / 2147483648


def _analyze_compressed(path: Path, thresholds: AudioQAThresholds) -> _SignalMetrics:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise AudioProviderUnavailableError("FFmpeg is unavailable for compressed-audio signal analysis.")
    try:
        result = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-i",
                str(path),
                "-t",
                str(thresholds.max_signal_analysis_seconds),
                "-map",
                "0:a:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(thresholds.analysis_sample_rate),
                "-f",
                "f32le",
                "pipe:1",
            ],
            check=True,
            capture_output=True,
            timeout=thresholds.ffmpeg_timeout_seconds,
        )
    except FileNotFoundError as error:
        raise AudioProviderUnavailableError("FFmpeg is unavailable for compressed-audio signal analysis.") from error
    except subprocess.TimeoutExpired as error:
        raise AudioProviderUnavailableError("FFmpeg signal analysis exceeded its time limit.") from error
    except subprocess.CalledProcessError as error:
        raise AudioGenerationError("FFmpeg could not decode samples for signal analysis.") from error
    raw_samples = result.stdout
    if not raw_samples or len(raw_samples) % 4:
        raise AudioGenerationError("FFmpeg returned no complete PCM samples for signal analysis.")
    max_output_bytes = int(
        thresholds.max_signal_analysis_seconds * thresholds.analysis_sample_rate * 4
    ) + 4
    if len(raw_samples) > max_output_bytes:
        raise AudioProviderUnavailableError("Decoded audio exceeded the configured analysis buffer limit.")
    samples = (sample[0] for sample in struct.iter_unpack("<f", raw_samples))
    metrics = _measure_samples(
        samples,
        thresholds.analysis_sample_rate,
        1,
        thresholds,
        approximate=True,
    )
    if metrics.analyzed_duration_seconds >= thresholds.max_signal_analysis_seconds:
        raise AudioProviderUnavailableError(
            "Signal checks unavailable: decoded audio reached the analysis duration limit."
        )
    return metrics


def _measure_samples(
    samples: Iterable[float],
    sample_rate: int,
    channels: int,
    thresholds: AudioQAThresholds,
    *,
    approximate: bool = False,
) -> _SignalMetrics:
    if sample_rate < 1 or channels < 1:
        raise AudioGenerationError("Audio sample rate or channel count is invalid.")
    samples_per_second = sample_rate * channels
    window_samples = max(
        channels,
        round(sample_rate * thresholds.analysis_window_seconds) * channels,
    )
    silence_amplitude = 10 ** (thresholds.silence_threshold_dbfs / 20)
    sample_index = 0
    window_start = 0
    window_count = 0
    window_square_sum = 0.0
    silence_start: int | None = None
    silence_end: int | None = None
    silence_segments: list[SilenceSegment] = []
    clipped_sample_count = 0
    peak_level = 0.0

    def finish_silence(end_index: int, *, final: bool = False) -> None:
        nonlocal silence_start, silence_end
        if silence_start is not None:
            actual_end = silence_end if silence_end is not None else end_index
            duration = (actual_end - silence_start) / samples_per_second
            is_entire_file_silent = (
                final and silence_start == 0 and actual_end == end_index
            )
            if duration >= thresholds.silence_min_duration_seconds or is_entire_file_silent:
                start_seconds = round(silence_start / samples_per_second, 6)
                end_seconds = round(actual_end / samples_per_second, 6)
                silence_segments.append(
                    SilenceSegment(
                        start_seconds,
                        end_seconds,
                        round(end_seconds - start_seconds, 6),
                    )
                )
        silence_start = None
        silence_end = None

    def finish_window(end_index: int) -> None:
        nonlocal window_start, window_count, window_square_sum, silence_start, silence_end
        if window_count == 0:
            return
        rms = math.sqrt(window_square_sum / window_count)
        if rms <= silence_amplitude:
            if silence_start is None:
                silence_start = window_start
            silence_end = end_index
        else:
            finish_silence(window_start)
        window_start = end_index
        window_count = 0
        window_square_sum = 0.0

    for sample in samples:
        value = float(sample)
        if not math.isfinite(value):
            raise AudioGenerationError("Decoded audio contains non-finite samples.")
        magnitude = abs(value)
        peak_level = max(peak_level, magnitude)
        if magnitude >= thresholds.clipping_peak_threshold:
            clipped_sample_count += 1
        window_square_sum += value * value
        window_count += 1
        sample_index += 1
        if window_count >= window_samples:
            finish_window(sample_index)
    if sample_index == 0:
        raise AudioGenerationError("No measurable audio samples were decoded.")
    finish_window(sample_index)
    finish_silence(sample_index, final=True)
    return _SignalMetrics(
        silence_segments=tuple(silence_segments),
        clipped_sample_count=clipped_sample_count,
        peak_level=peak_level,
        analyzed_duration_seconds=sample_index / samples_per_second,
        approximate=approximate,
    )


DEFAULT_AUDIO_QA_THRESHOLDS = AudioQAThresholds()