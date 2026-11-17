import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from elevenlabs import ElevenLabs

if __package__:
    from .huggingface_media import HuggingFaceMediaGenerator
    from .image_prompt_generator import ImagePromptGenerator, LocalImagePromptGenerator
    from .local_media_generator import LocalMediaGenerator
    from .text_generator import TextGenerator, create_text_generator
else:
    from huggingface_media import HuggingFaceMediaGenerator
    from image_prompt_generator import ImagePromptGenerator, LocalImagePromptGenerator
    from local_media_generator import LocalMediaGenerator
    from text_generator import TextGenerator, create_text_generator

VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720
VIDEO_FPS = 24
WORDS_PER_CARD = 16


class VideoGenerationCancelled(RuntimeError):
    pass


def _check_cancelled(cancel_requested: Callable[[], bool] | None) -> None:
    if cancel_requested is not None and cancel_requested():
        raise VideoGenerationCancelled("Video generation was stopped.")


def _narration_cards(text: str) -> list[str]:
    words = text.split()
    if not words:
        raise ValueError("Video narration cannot be empty.")
    return [
        " ".join(words[index : index + WORDS_PER_CARD])
        for index in range(0, len(words), WORDS_PER_CARD)
    ]


def _generate_narration(
    text: str,
    client: ElevenLabs | None,
    output_path: Path,
    cancel_requested: Callable[[], bool] | None = None,
) -> None:
    _check_cancelled(cancel_requested)
    tts_provider = os.getenv("TTS_PROVIDER", "elevenlabs").strip().lower()
    if tts_provider == "local":
        _generate_local_narration(text, output_path, cancel_requested)
        return
    if tts_provider != "elevenlabs":
        raise ValueError("TTS_PROVIDER must be 'elevenlabs' or 'local'.")
    if client is None:
        raise RuntimeError("ElevenLabs client is required for hosted narration.")

    voice_id = os.getenv("ELEVENLABS_VOICE_ID")
    if not voice_id:
        voices = client.voices.get_all().voices
        if not voices:
            raise RuntimeError(
                "No ElevenLabs voices are available for this account. "
                "Set ELEVENLABS_VOICE_ID in .env to a voice ID you can use."
            )
        voice_id = voices[0].voice_id

    audio_chunks = client.text_to_speech.convert(
        voice_id=voice_id,
        text=text,
        model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128",
    )
    with output_path.open("wb") as audio_file:
        for chunk in audio_chunks:
            _check_cancelled(cancel_requested)
            audio_file.write(chunk)
    if output_path.stat().st_size == 0:
        raise RuntimeError("ElevenLabs returned an empty narration audio file.")


def _generate_local_narration(
    text: str,
    output_path: Path,
    cancel_requested: Callable[[], bool] | None = None,
) -> None:
    tts = shutil.which("espeak-ng") or shutil.which("espeak")
    if not tts:
        raise RuntimeError(
            "Local narration requires espeak-ng or espeak. Install one and try again."
        )
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required. Install FFmpeg and try again.")

    try:
        speech = subprocess.run(
            [tts, "--stdout", text],
            check=True,
            capture_output=True,
            timeout=120,
        )
    except subprocess.CalledProcessError as error:
        raise RuntimeError("Local text-to-speech generation failed.") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Local text-to-speech generation timed out.") from error
    _check_cancelled(cancel_requested)
    if not speech.stdout:
        raise RuntimeError("Local text-to-speech returned empty audio.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "pipe:0",
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(output_path),
            ],
            input=speech.stdout,
            check=True,
            capture_output=True,
            timeout=120,
        )
    except subprocess.CalledProcessError as error:
        detail = error.stderr.decode(errors="replace").strip()[-1000:] if error.stderr else ""
        message = "FFmpeg could not encode local narration."
        if detail:
            message = f"{message} {detail}"
        raise RuntimeError(message) from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("FFmpeg timed out encoding local narration.") from error
    _check_cancelled(cancel_requested)
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError("Local narration did not create an audio file.")


def _audio_duration(audio_path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe is required. Install FFmpeg and try again.")
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        duration = float(result.stdout.strip())
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as error:
        raise RuntimeError("Could not read the generated narration duration.") from error
    if duration <= 0:
        raise RuntimeError("Generated narration has no duration.")
    return duration


def _assemble_video(
    video_paths: list[Path],
    audio_path: Path,
    output_path: Path,
    cancel_requested: Callable[[], bool] | None = None,
    captions: list[str] | None = None,
) -> None:
    _check_cancelled(cancel_requested)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required. Install FFmpeg and try again.")
    if not video_paths:
        raise ValueError("At least one generated video clip is needed.")
    if captions is not None and len(captions) != len(video_paths):
        raise ValueError("The number of captions must match the generated video clips.")

    duration = _audio_duration(audio_path)
    _check_cancelled(cancel_requested)
    with tempfile.TemporaryDirectory(
        prefix="video-assembly-", dir=output_path.parent
    ) as assembly_dir:
        work_dir = Path(assembly_dir)
        concat_path = work_dir / "clips.txt"
        with concat_path.open("w", encoding="utf-8") as concat_file:
            for video_path in video_paths:
                escaped_path = video_path.resolve().as_posix().replace("'", "'\\''")
                concat_file.write(f"file '{escaped_path}'\n")

        command = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-stream_loop",
            "-1",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-i",
            str(audio_path),
        ]
        video_filters = []
        if captions is not None:
            subtitle_path = work_dir / "captions.srt"
            subtitle_path.write_text(
                _subtitle_file(captions, duration),
                encoding="utf-8",
            )
            video_filters.append(
                f"subtitles={subtitle_path.name}:"
                "force_style='FontName=DejaVu Sans,FontSize=24,"
                "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
                "BorderStyle=3,Outline=2,Shadow=0,Alignment=2,MarginV=36'"
            )
        video_filters.extend(
            [
                f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=decrease",
                f"pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2",
                f"fps={VIDEO_FPS}",
                "format=yuv420p",
            ]
        )
        command.extend(
            [
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-vf",
                ",".join(video_filters),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-t",
                f"{duration:.6f}",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=work_dir,
        )
        _finish_ffmpeg(process, output_path, cancel_requested)


def _finish_ffmpeg(
    process: subprocess.Popen[str],
    output_path: Path,
    cancel_requested: Callable[[], bool] | None,
) -> None:
    deadline = time.monotonic() + 180
    try:
        while True:
            if cancel_requested is not None and cancel_requested():
                if process.poll() is None:
                    process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                raise VideoGenerationCancelled("Video generation was stopped.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                _, stderr = process.communicate()
                detail = stderr.strip()[-1200:] if stderr else "Timed out."
                raise RuntimeError(f"FFmpeg could not create the video: {detail}")
            try:
                _, stderr = process.communicate(timeout=min(0.25, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
    except VideoGenerationCancelled:
        raise
    if process.returncode:
        detail = stderr.strip()[-1200:] if stderr else f"FFmpeg exited with {process.returncode}."
        raise RuntimeError(f"FFmpeg could not create the video: {detail}")
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError("FFmpeg finished without creating a video file.")


def _subtitle_file(captions: list[str], duration: float) -> str:
    def timestamp(seconds: float) -> str:
        milliseconds = round(seconds * 1000)
        hours, milliseconds = divmod(milliseconds, 3_600_000)
        minutes, milliseconds = divmod(milliseconds, 60_000)
        whole_seconds, milliseconds = divmod(milliseconds, 1000)
        return f"{hours:02}:{minutes:02}:{whole_seconds:02},{milliseconds:03}"

    caption_duration = duration / len(captions)
    entries = []
    for index, caption in enumerate(captions):
        start = index * caption_duration
        end = (index + 1) * caption_duration
        safe_caption = caption.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        entries.append(
            f"{index + 1}\n{timestamp(start)} --> {timestamp(end)}\n{safe_caption}\n"
        )
    return "\n".join(entries)


def create_video(
    prompt: str,
    client: ElevenLabs | None,
    output_dir: Path,
    image_prompt_generator: ImagePromptGenerator | None = None,
    text_generator: TextGenerator | None = None,
    media_generator: HuggingFaceMediaGenerator | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> Path:
    video_idea = prompt.strip()
    if not video_idea:
        raise ValueError("Video idea cannot be empty.")
    if image_prompt_generator is None:
        image_prompt_generator = LocalImagePromptGenerator()
    if text_generator is None:
        text_generator = create_text_generator()
    if media_generator is None:
        media_provider = os.getenv("MEDIA_PROVIDER", "huggingface").strip().lower()
        if media_provider == "local":
            media_generator = LocalMediaGenerator()
        elif media_provider == "huggingface":
            media_generator = HuggingFaceMediaGenerator()
        else:
            raise ValueError("MEDIA_PROVIDER must be 'huggingface' or 'local'.")

    _check_cancelled(cancel_requested)
    if progress_callback is not None:
        progress_callback("dialogue", "Writing the script...")
    narration = text_generator.generate_dialogue(video_idea).strip()
    _check_cancelled(cancel_requested)
    if not narration:
        raise ValueError("Text generator returned empty dialogue.")

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("FFmpeg and ffprobe are required. Install FFmpeg first.")

    created_at = datetime.now()
    slug = re.sub(r"[^a-z0-9]+", "-", video_idea.lower()).strip("-")[:48]
    run_dir = (
        output_dir
        / created_at.strftime("%Y-%m-%d")
        / f"{slug or 'narrated-video'}-{created_at.strftime('%H%M%S-%f')}"
    )
    images_dir = run_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    output_path = run_dir / f"{slug or 'narrated-video'}.mp4"
    dialogue_path = run_dir / "dialogue.txt"
    dialogue_path.write_text(narration + "\n", encoding="utf-8")
    audio_path = run_dir / "narration.mp3"

    print("Generating animated scenes...", flush=True)
    if progress_callback is not None:
        progress_callback("images", "Preparing illustrated shots...")
    cards = _narration_cards(narration)
    video_paths = []
    for index, card in enumerate(cards, start=1):
        _check_cancelled(cancel_requested)
        if progress_callback is not None:
            progress_callback("images", f"Generating image {index} of {len(cards)}...")
        image_path = images_dir / f"shot_{index:03}.png"
        video_path = images_dir / f"shot_{index:03}.mp4"
        image_prompt = image_prompt_generator.generate_image_prompt(card)
        media_generator.generate_image(
            image_prompt,
            image_path,
            VIDEO_WIDTH,
            VIDEO_HEIGHT,
        )
        _check_cancelled(cancel_requested)
        if progress_callback is not None:
            progress_callback("video", f"Animating shot {index} of {len(cards)}...")
        media_generator.generate_video(image_path, image_prompt, video_path)
        video_paths.append(video_path)

    tts_provider = os.getenv("TTS_PROVIDER", "elevenlabs").strip().lower()
    if tts_provider not in {"elevenlabs", "local"}:
        raise ValueError("TTS_PROVIDER must be 'elevenlabs' or 'local'.")
    tts_label = "local text-to-speech" if tts_provider == "local" else "ElevenLabs"
    print(f"Creating narration with {tts_label}...", flush=True)
    if progress_callback is not None:
        progress_callback("speech", f"Generating speech with {tts_label}...")
    _generate_narration(narration, client, audio_path, cancel_requested)

    print("Combining animated scenes and narration into an MP4...", flush=True)
    _check_cancelled(cancel_requested)
    if progress_callback is not None:
        progress_callback("video", "Combining animated scenes and speech...")
    _assemble_video(
        video_paths,
        audio_path,
        output_path,
        cancel_requested,
        cards,
    )

    _check_cancelled(cancel_requested)
    return output_path
