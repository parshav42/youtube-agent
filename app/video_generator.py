import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from textwrap import wrap

from elevenlabs import ElevenLabs
from PIL import Image, ImageDraw, ImageFont

VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720
VIDEO_FPS = 24
WORDS_PER_CARD = 16


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


def _create_title_card(text: str, output_path: Path) -> None:
    image = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), color="#101827")
    draw = ImageDraw.Draw(image)
    accent_font = ImageFont.truetype("DejaVuSans.ttf", 22)
    body_font = ImageFont.truetype("DejaVuSans.ttf", 48)
    draw.rounded_rectangle((76, 80, 1204, 640), radius=28, fill="#19263a")
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
) -> None:
    voice_id = os.getenv("ELEVENLABS_VOICE_ID")
    if not voice_id:
        voices = client.voices.get_all()
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
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required. Install FFmpeg and try again.")
    if not card_paths:
        raise ValueError("At least one title card is needed to create the video.")

    duration = _audio_duration(audio_path)
    card_duration = duration / len(card_paths)
    concat_path = audio_path.parent / "cards.txt"
    with concat_path.open("w", encoding="utf-8") as concat_file:
        for card_path in card_paths:
            concat_file.write(f"file '{card_path.name}'\n")
            concat_file.write(f"duration {card_duration:.6f}\n")
        concat_file.write(f"file '{card_paths[-1].name}'\n")

    try:
        subprocess.run(
            [
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
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
            cwd=audio_path.parent,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        stderr = getattr(error, "stderr", None)
        detail = stderr.strip()[-1200:] if isinstance(stderr, str) else str(error)
        raise RuntimeError(f"FFmpeg could not create the video: {detail}") from error
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError("FFmpeg finished without creating a video file.")


def create_video(
    prompt: str,
    client: ElevenLabs,
    output_dir: Path,
) -> Path:
    narration = prompt.strip()
    if not narration:
        raise ValueError("Video narration cannot be empty.")

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("FFmpeg and ffprobe are required. Install FFmpeg first.")

    output_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", narration.lower()).strip("-")[:48]
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_path = output_dir / f"{slug or 'narrated-video'}-{timestamp}.mp4"

    print("Creating title cards...", flush=True)
    with tempfile.TemporaryDirectory(prefix="video-cards-") as temp_dir:
        work_dir = Path(temp_dir)
        cards = _narration_cards(narration)
        card_paths = []
        for index, card in enumerate(cards, start=1):
            card_path = work_dir / f"card_{index:03}.png"
            _create_title_card(card, card_path)
            card_paths.append(card_path)

        print("Creating narration with ElevenLabs...", flush=True)
        audio_path = work_dir / "narration.mp3"
        _generate_narration(narration, client, audio_path)

        print("Combining title cards and narration into an MP4...", flush=True)
        _assemble_video(card_paths, audio_path, output_path)

    return output_path
