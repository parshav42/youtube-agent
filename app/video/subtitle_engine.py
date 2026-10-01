"""UTF-8 phrase-level SRT captions with explicit approximate timing."""

import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

from app.schemas.script_schema import MotivationalScript


@dataclass(frozen=True)
class SubtitleMetadata:
    path: str
    timing_method: str
    phrase_count: int
    safe_margin_note: str


def generate_srt(
    script: MotivationalScript,
    audio_duration_seconds: float,
    output_path: str | Path,
    max_words_per_phrase: int = 7,
    word_timings: list[dict[str, float | str]] | None = None,
) -> SubtitleMetadata:
    """Split actual scene narration into readable phrases and distribute by word count."""
    if audio_duration_seconds <= 0:
        raise ValueError("Audio duration must be greater than zero to time subtitles.")
    if not 1 <= max_words_per_phrase <= 12:
        raise ValueError("max_words_per_phrase must be between 1 and 12.")

    scene_weights = [max(1, scene.duration_seconds) for scene in script.scenes]
    total_weight = sum(scene_weights)
    entries: list[tuple[float, float, str]] = []
    has_alignment = bool(word_timings) and len(word_timings) == len(script.narration.split())
    global_word_index = 0
    scene_start = 0.0
    for scene, scene_weight in zip(script.scenes, scene_weights, strict=True):
        scene_duration = audio_duration_seconds * scene_weight / total_weight
        phrases = _phrases(scene.narration, max_words_per_phrase)
        word_total = sum(len(phrase.split()) for phrase in phrases)
        phrase_start = scene_start
        for phrase in phrases:
            phrase_duration = scene_duration * len(phrase.split()) / word_total
            phrase_word_count = len(phrase.split())
            if has_alignment:
                aligned = word_timings[
                    global_word_index : global_word_index + phrase_word_count
                ]
                start = float(aligned[0]["start"])
                end = float(aligned[-1]["end"])
                if end > start:
                    entries.append((start, end, phrase))
                else:
                    entries.append((phrase_start, phrase_start + phrase_duration, phrase))
            else:
                entries.append((phrase_start, phrase_start + phrase_duration, phrase))
            global_word_index += phrase_word_count
            phrase_start += phrase_duration
        scene_start += scene_duration

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    srt = "\n\n".join(
        f"{index}\n{_timestamp(start)} --> {_timestamp(end)}\n{_caption_lines(text)}"
        for index, (start, end, text) in enumerate(entries, start=1)
    )
    output.write_text(srt + "\n", encoding="utf-8")
    if not output.is_file() or output.stat().st_size == 0:
        raise OSError(f"Subtitle file was not written: {output}")
    return SubtitleMetadata(
        path=str(output.resolve()),
        timing_method=(
            "provider character-level speech alignment grouped into phrase captions"
            if has_alignment
            else "approximate scene-plan timing distributed by narration word count; not forced aligned"
        ),
        phrase_count=len(entries),
        safe_margin_note="Burned captions use centered alignment and 100px horizontal / 150px bottom margins.",
    )


def _phrases(text: str, max_words: int) -> list[str]:
    words = re.findall(r"\S+", text.strip())
    return [" ".join(words[index : index + max_words]) for index in range(0, len(words), max_words)]


def _timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{whole_seconds:02},{millis:03}"


def _caption_lines(text: str) -> str:
    return "\n".join(textwrap.wrap(text, width=34, break_long_words=False))