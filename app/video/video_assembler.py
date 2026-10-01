"""FFmpeg vertical video assembly and FFprobe-based output validation."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.schemas.script_schema import ScriptScene

WIDTH = 1080
HEIGHT = 1920
FRAME_RATE = 30
TRANSITION_SECONDS = 0.4


class VideoAssemblyError(RuntimeError):
    """Raised when FFmpeg cannot render or the final MP4 fails validation."""


class VideoAssembler:
    """Render still-image scene assets, narration, and burned-in SRT to H.264 MP4."""

    def __init__(self, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> None:
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    def ensure_available(self) -> None:
        missing = [binary for binary in (self.ffmpeg, self.ffprobe) if shutil.which(binary) is None]
        if missing:
            raise VideoAssemblyError(
                f"Required media tool(s) unavailable: {', '.join(missing)}. Install FFmpeg and FFprobe."
            )

    def assemble(
        self,
        scenes: list[ScriptScene],
        visual_assets: list[dict[str, Any]],
        audio_path: str | Path,
        subtitle_path: str | Path,
        output_path: str | Path,
    ) -> dict[str, Any]:
        self.ensure_available()
        if not scenes or len(scenes) != len(visual_assets):
            raise VideoAssemblyError("Each planned scene must have exactly one acquired visual asset.")
        for scene, asset in zip(scenes, visual_assets, strict=True):
            if asset.get("scene_number") != scene.scene_number or not Path(asset.get("path", "")).is_file():
                raise VideoAssemblyError(f"Visual asset missing or mismatched for scene {scene.scene_number}.")
        audio = Path(audio_path)
        subtitles = Path(subtitle_path)
        output = Path(output_path)
        if output.exists() or output.is_symlink():
            raise VideoAssemblyError("Video output already exists; refusing to overwrite it.")
        if not audio.is_file() or not subtitles.is_file():
            raise VideoAssemblyError("Narration audio and generated subtitles must exist before rendering.")
        output.parent.mkdir(parents=True, exist_ok=True)
        audio_info = probe_media(audio, self.ffprobe)
        audio_stream = next(
            (stream for stream in audio_info["streams"] if stream.get("codec_type") == "audio"),
            None,
        )
        audio_duration = float(
            (audio_stream or {}).get("duration") or audio_info["format"].get("duration") or 0
        )
        if audio_stream is None or audio_duration <= 0:
            raise VideoAssemblyError("Narration file contains no readable audio stream.")
        if audio_duration <= TRANSITION_SECONDS * (len(scenes) - 1) + 1:
            raise VideoAssemblyError("Narration is too short to render the requested scene transitions.")

        transition_overlap = min(TRANSITION_SECONDS, audio_duration / (len(scenes) * 3))
        target_video_duration = audio_duration
        input_timeline_duration = target_video_duration + transition_overlap * (len(scenes) - 1)
        weight_sum = sum(scene.duration_seconds for scene in scenes)
        input_durations = [
            input_timeline_duration * scene.duration_seconds / weight_sum for scene in scenes
        ]
        arguments: list[str] = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-n"]
        for asset, duration in zip(visual_assets, input_durations, strict=True):
            arguments.extend(
                ["-loop", "1", "-framerate", str(FRAME_RATE), "-t", f"{duration:.6f}", "-i", asset["path"]]
            )
        audio_input_index = len(scenes)
        arguments.extend(["-i", str(audio)])

        filters = []
        for index, duration in enumerate(input_durations):
            filters.append(
                f"[{index}:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
                f"crop={WIDTH}:{HEIGHT},fps={FRAME_RATE},setsar=1,settb=AVTB,"
                f"format=yuv420p,trim=duration={duration:.6f},setpts=PTS-STARTPTS[v{index}]"
            )
        active_label = "v0"
        elapsed_input = input_durations[0]
        for index in range(1, len(scenes)):
            next_label = f"vx{index}"
            offset = elapsed_input - transition_overlap
            transition = _transition_name(scenes[index - 1].transition)
            filters.append(
                f"[{active_label}][v{index}]xfade=transition={transition}:"
                f"duration={transition_overlap:.6f}:offset={offset:.6f}[{next_label}]"
            )
            elapsed_input += input_durations[index] - transition_overlap
            active_label = next_label
        escaped_subtitle_path = _escape_filter_path(str(subtitles.resolve()))
        filters.append(
            f"[{active_label}]subtitles=filename='{escaped_subtitle_path}':"
            "force_style='FontName=Noto Sans Devanagari,FontSize=18,PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00101010,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,"
            "MarginV=150,MarginL=100,MarginR=100'[vout]"
        )
        arguments.extend(
            [
                "-filter_complex", ";".join(filters),
                "-map", "[vout]", "-map", f"{audio_input_index}:a:0",
                "-t", f"{target_video_duration:.6f}",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                "-pix_fmt", "yuv420p", "-r", str(FRAME_RATE),
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                "-shortest", "-movflags", "+faststart", str(output),
            ]
        )
        try:
            result = subprocess.run(
                arguments,
                check=True,
                capture_output=True,
                text=True,
                timeout=max(180, round(audio_duration * 15)),
            )
        except (OSError, subprocess.SubprocessError) as error:
            detail = getattr(error, "stderr", "") or str(error)
            raise VideoAssemblyError(f"FFmpeg render failed: {detail[-3000:]}") from error
        if result.stderr.strip():
            raise VideoAssemblyError(f"FFmpeg reported a render warning: {result.stderr[-2000:]}")
        return validate_video(output, self.ffprobe)


def probe_media(path: str | Path, ffprobe: str = "ffprobe") -> dict[str, Any]:
    """Return FFprobe stream and container metadata or raise on corrupt media."""
    media_path = Path(path)
    if not media_path.is_file() or media_path.stat().st_size == 0:
        raise VideoAssemblyError(f"Media file is missing or empty: {media_path}")
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(media_path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        data = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise VideoAssemblyError(f"FFprobe could not read {media_path}: {error}") from error
    if not data.get("streams"):
        raise VideoAssemblyError(f"Media contains no readable streams: {media_path}")
    return data


def validate_video(
    path: str | Path,
    ffprobe: str = "ffprobe",
    ffmpeg: str = "ffmpeg",
) -> dict[str, Any]:
    """Require a playable 1080x1920 H.264/AAC MP4 with positive duration."""
    media_path = Path(path)
    if media_path.suffix.lower() != ".mp4" or media_path.stat().st_size < 4096:
        raise VideoAssemblyError("Rendered MP4 is missing, too small, or has the wrong extension.")
    metadata = probe_media(media_path, ffprobe)
    video = next(
        (stream for stream in metadata["streams"] if stream.get("codec_type") == "video"), None
    )
    audio = next(
        (stream for stream in metadata["streams"] if stream.get("codec_type") == "audio"), None
    )
    duration = float(metadata["format"].get("duration") or 0)
    if video is None or audio is None or duration <= 0:
        raise VideoAssemblyError("Final MP4 must contain video and audio streams with positive duration.")
    if (video.get("width"), video.get("height")) != (WIDTH, HEIGHT):
        raise VideoAssemblyError(
            f"Final video must be {WIDTH}x{HEIGHT}; got {video.get('width')}x{video.get('height')}."
        )
    if video.get("codec_name") != "h264" or audio.get("codec_name") != "aac":
        raise VideoAssemblyError("Final MP4 codecs must be H.264 video and AAC audio.")
    frame_rate = _fraction(video.get("avg_frame_rate", "0/1"))
    if round(frame_rate) != FRAME_RATE:
        raise VideoAssemblyError(f"Expected {FRAME_RATE} FPS output; got {frame_rate:.3f} FPS.")
    try:
        decode = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(media_path), "-f", "null", "-"],
            check=True,
            capture_output=True,
            text=True,
            timeout=max(60, round(duration * 5)),
        )
    except (OSError, subprocess.SubprocessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        raise VideoAssemblyError(f"FFmpeg could not decode the complete output MP4: {detail[-2000:]}") from error
    if decode.stderr.strip():
        raise VideoAssemblyError(f"FFmpeg reported decode errors in output MP4: {decode.stderr[-2000:]}")
    return {
        "path": str(media_path.resolve()),
        "size_bytes": media_path.stat().st_size,
        "container": metadata["format"].get("format_name", "unknown"),
        "duration_seconds": duration,
        "width": video["width"],
        "height": video["height"],
        "frame_rate": frame_rate,
        "video_codec": video["codec_name"],
        "audio_codec": audio["codec_name"],
        "audio_stream": True,
        "video_stream": True,
    }


def _fraction(value: str) -> float:
    try:
        numerator, denominator = value.split("/", maxsplit=1)
        return float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _transition_name(value: str) -> str:
    normalized = value.lower()
    if "dissolve" in normalized:
        return "dissolve"
    if "black" in normalized:
        return "fadeblack"
    if "wipe" in normalized:
        return "wipeleft"
    if "push" in normalized or "slide" in normalized:
        return "smoothleft"
    if "match" in normalized:
        return "fade"
    if "cut" in normalized:
        return "fade"
    return "fade"


def _escape_filter_path(path: str) -> str:
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")