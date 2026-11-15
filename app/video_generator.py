import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from textwrap import wrap

from elevenlabs import ElevenLabs
from PIL import Image, ImageDraw, ImageFont

if __package__:
    from .image_generator import create_image
    from .image_prompt_generator import ImagePromptGenerator, LocalImagePromptGenerator
    from .text_generator import HuggingFaceTextGenerator, TextGenerator
else:
    from image_generator import create_image
    from image_prompt_generator import ImagePromptGenerator, LocalImagePromptGenerator
    from text_generator import HuggingFaceTextGenerator, TextGenerator

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


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    lines: list[str] = []
    current_line = ""
    for word in text.split():
        candidate = f"{current_line} {word}".strip()
        if current_line and draw.textlength(candidate, font=font) > max_width:
            lines.append(current_line)
            current_line = word
        else:
            current_line = candidate
    if current_line:
        lines.append(current_line)
    return lines


def _create_title_card(text: str, output_path: Path, background_path: Path) -> None:
    image = Image.open(background_path).convert("RGBA")
    image = image.resize((VIDEO_WIDTH, VIDEO_HEIGHT))
    image = Image.alpha_composite(
        image,
        Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (8, 13, 22, 105)),
    )
    draw = ImageDraw.Draw(image)
    accent_font = ImageFont.truetype("DejaVuSans.ttf", 22)
    body_font = ImageFont.truetype("DejaVuSans.ttf", 48)
    draw.rounded_rectangle((76, 80, 1204, 640), radius=28, fill=(25, 38, 58, 205))
    draw.rounded_rectangle((120, 132, 176, 140), radius=4, fill="#f2a65a")
    draw.text((120, 158), "YOUR STORY", font=accent_font, fill="#f2a65a")

    lines = _wrap_text(draw, text, body_font, max_width=1000)
    line_height = 66
    block_height = len(lines) * line_height
    y = (VIDEO_HEIGHT - block_height) // 2 + 28
    for line in lines:
        line_width = draw.textlength(line, font=body_font)
        draw.text(
            ((VIDEO_WIDTH - line_width) / 2, y),
            line,
            font=body_font,
            fill="#f5f7fa",
        )
        y += line_height
    image.save(output_path, format="PNG")


def _generate_narration(
    text: str,
    client: ElevenLabs,
    output_path: Path,
    cancel_requested: Callable[[], bool] | None = None,
) -> None:
    _check_cancelled(cancel_requested)
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
    card_paths: list[Path],
    audio_path: Path,
    output_path: Path,
    cancel_requested: Callable[[], bool] | None = None,
) -> None:
    _check_cancelled(cancel_requested)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required. Install FFmpeg and try again.")
    if not card_paths:
        raise ValueError("At least one title card is needed to create the video.")

    duration = _audio_duration(audio_path)
    _check_cancelled(cancel_requested)
    card_duration = duration / len(card_paths)
    concat_path = card_paths[0].parent / "cards.txt"
    with concat_path.open("w", encoding="utf-8") as concat_file:
        for card_path in card_paths:
            concat_file.write(f"file '{card_path.name}'\n")
            concat_file.write(f"duration {card_duration:.6f}\n")
        concat_file.write(f"file '{card_paths[-1].name}'\n")

    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-i",
        str(audio_path),
        "-vf",
        f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT},fps={VIDEO_FPS},format=yuv420p",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
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
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=concat_path.parent,
    )
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


def create_video(
    prompt: str,
    client: ElevenLabs,
    output_dir: Path,
    image_prompt_generator: ImagePromptGenerator | None = None,
    text_generator: TextGenerator | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> Path:
    video_idea = prompt.strip()
    if not video_idea:
        raise ValueError("Video idea cannot be empty.")
    if image_prompt_generator is None:
        image_prompt_generator = LocalImagePromptGenerator()
    if text_generator is None:
        text_generator = HuggingFaceTextGenerator()

    _check_cancelled(cancel_requested)
    if progress_callback is not None:
        progress_callback("dialogue", "Writing the script with Hugging Face...")
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

    print("Creating title cards...", flush=True)
    if progress_callback is not None:
        progress_callback("images", "Preparing illustrated shots...")
    with tempfile.TemporaryDirectory(prefix="video-cards-") as temp_dir:
        work_dir = Path(temp_dir)
        cards = _narration_cards(narration)
        card_paths = []
        for index, card in enumerate(cards, start=1):
            _check_cancelled(cancel_requested)
            if progress_callback is not None:
                progress_callback("images", f"Creating shot {index} of {len(cards)}...")
            image_path = images_dir / f"shot_{index:03}.png"
            card_path = work_dir / f"card_{index:03}.png"
            image_prompt = image_prompt_generator.generate_image_prompt(card)
            create_image(image_prompt, image_path)
            _check_cancelled(cancel_requested)
            _create_title_card(card, card_path, image_path)
            card_paths.append(card_path)

        print("Creating narration with ElevenLabs...", flush=True)
        if progress_callback is not None:
            progress_callback("speech", "Generating speech with ElevenLabs...")
        _generate_narration(narration, client, audio_path, cancel_requested)

        print("Combining title cards and narration into an MP4...", flush=True)
        _check_cancelled(cancel_requested)
        if progress_callback is not None:
            progress_callback("video", "Combining images and speech into the video...")
        _assemble_video(card_paths, audio_path, output_path, cancel_requested)

    _check_cancelled(cancel_requested)
    return output_path
