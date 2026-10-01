"""Independent technical checks for rendered Short videos."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.video.video_assembler import HEIGHT, WIDTH


def run_video_quality_assurance(
    video_path: str | Path,
    *,
    ffprobe: str = "ffprobe",
    ffmpeg: str = "ffmpeg",
    min_duration_seconds: float = 1.0,
    max_duration_seconds: float = 60.0,
) -> dict[str, Any]:
    """Return explicit pass/fail findings; unavailable tools fail closed."""
    path = Path(video_path)
    findings: list[str] = []
    if not path.is_file():
        return _report("failed", ["Video file is missing."])
    try:
        size = path.stat().st_size
    except OSError:
        return _report("failed", ["Video file could not be inspected."])
    if size == 0:
        return _report("failed", ["Video file is empty."])
    probe_bin, decode_bin = shutil.which(ffprobe), shutil.which(ffmpeg)
    if not probe_bin or not decode_bin:
        absent = [name for name, found in ((ffprobe, probe_bin), (ffmpeg, decode_bin)) if not found]
        return _report("failed", [f"Required media tool unavailable: {', '.join(absent)}."])
    try:
        probe = subprocess.run([probe_bin, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], check=True, capture_output=True, text=True, timeout=30)
        metadata = json.loads(probe.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return _report("failed", ["FFprobe could not inspect the video media."])
    streams = metadata.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = _positive_duration(metadata.get("format", {}).get("duration"))
    if not video:
        findings.append("Video stream is missing.")
    if not audio:
        findings.append("Audio stream is missing.")
    dimensions = [video.get("width"), video.get("height")] if video else None
    if video and dimensions != [WIDTH, HEIGHT]:
        findings.append(f"Portrait dimensions must be {WIDTH}x{HEIGHT}; found {dimensions[0]}x{dimensions[1]}.")
    if duration is None or duration <= 0:
        findings.append("Video duration is unavailable or nonpositive.")
    elif not min_duration_seconds <= duration <= max_duration_seconds:
        findings.append(f"Video duration must be between {min_duration_seconds:g} and {max_duration_seconds:g} seconds.")
    try:
        decoded = subprocess.run([decode_bin, "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True, timeout=90)
        if decoded.returncode != 0 or decoded.stderr.strip():
            findings.append("FFmpeg reported decode errors in the video.")
    except (OSError, subprocess.SubprocessError):
        findings.append("FFmpeg could not decode the video.")
    report = _report("failed" if findings else "passed", findings or ["Video passed technical checks."])
    report.update({"file_size_bytes": size, "duration_seconds": duration, "width": dimensions[0] if dimensions else None, "height": dimensions[1] if dimensions else None, "video_stream": bool(video), "audio_stream": bool(audio)})
    return report


def _positive_duration(value: Any) -> float | None:
    try:
        result = float(value)
        return result if result > 0 else None
    except (TypeError, ValueError):
        return None


def _report(status: str, findings: list[str]) -> dict[str, Any]:
    return {"technical_status": status, "findings": findings}
