import re
from pathlib import Path

import pytest
from elevenlabs import ElevenLabs
from PIL import Image

from app import video_generator
from app.image_generator import create_image
from app.image_prompt_generator import LocalImagePromptGenerator
from app.text_generator import OllamaTextGenerator


def test_ollama_text_generator_returns_generated_dialogue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, str]:
            return {"response": "A generated story."}

    requests: list[tuple[str, dict[str, object], int]] = []

    def fake_post(
        url: str,
        json: dict[str, object],
        timeout: int,
    ) -> FakeResponse:
        requests.append((url, json, timeout))
        return FakeResponse()

    monkeypatch.setattr("app.text_generator.requests.post", fake_post)
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")
    generator = OllamaTextGenerator()

    dialogue = generator.generate_dialogue("A fox finds a hidden door.")

    assert dialogue == "A generated story."
    assert requests[0][0] == "http://localhost:11434/api/generate"
    assert requests[0][1]["model"] == "test-model"
    assert "A fox finds a hidden door." in requests[0][1]["prompt"]


def test_create_image_writes_repeatable_prompt_sized_png(tmp_path: Path) -> None:
    first_path = create_image("A quiet forest at sunrise", tmp_path / "first.png", 320, 180)
    second_path = create_image("A quiet forest at sunrise", tmp_path / "second.png", 320, 180)

    with Image.open(first_path) as image:
        assert image.size == (320, 180)
        assert image.format == "PNG"
    assert first_path.read_bytes() == second_path.read_bytes()


@pytest.mark.parametrize(
    ("prompt", "width", "height"),
    [("", 320, 180), ("a scene", 0, 180), ("a scene", 320, -1)],
)
def test_create_image_rejects_invalid_input(
    tmp_path: Path,
    prompt: str,
    width: int,
    height: int,
) -> None:
    with pytest.raises(ValueError):
        create_image(prompt, tmp_path / "invalid.png", width, height)


def test_local_prompt_generator_formats_story_segment() -> None:
    generator = LocalImagePromptGenerator()

    image_prompt = generator.generate_image_prompt("A fox finds a hidden door.")

    assert "A fox finds a hidden door." in image_prompt
    assert "16:9" in image_prompt


def test_ollama_text_generator_rejects_empty_video_idea() -> None:
    with pytest.raises(ValueError, match="Video idea cannot be empty"):
        OllamaTextGenerator().generate_dialogue("  ")


def test_create_video_stores_images_and_sends_generated_dialogue_to_speech(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    story = "A fox finds a hidden door."
    dialogue = "Narrator: A fox discovers a hidden door beneath the old tree."
    generated_prompts: list[str] = []
    image_prompts: list[str] = []
    image_paths: list[Path] = []
    speech_text: list[str] = []
    progress_events: list[str] = []

    class FakeImagePromptGenerator:
        def generate_image_prompt(self, story_segment: str) -> str:
            generated_prompts.append(story_segment)
            return "LLM-generated forest illustration"

    class FakeTextGenerator:
        def generate_dialogue(self, video_idea: str) -> str:
            assert video_idea == story
            return dialogue

    monkeypatch.setattr(video_generator.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(video_generator, "_create_title_card", lambda *_args: None)
    monkeypatch.setattr(
        video_generator,
        "_generate_narration",
        lambda text, _client, output_path, _cancel_requested=None: (
            speech_text.append(text),
            output_path.write_bytes(b"audio"),
        ),
    )
    monkeypatch.setattr(video_generator, "_assemble_video", lambda *_args: None)
    monkeypatch.setattr(
        video_generator,
        "create_image",
        lambda image_prompt, output_path: _write_fake_image(
            image_prompt,
            output_path,
            image_prompts,
            image_paths,
        ),
    )

    video_path = video_generator.create_video(
        prompt=story,
        client=ElevenLabs(api_key="test-key"),
        output_dir=tmp_path,
        image_prompt_generator=FakeImagePromptGenerator(),
        text_generator=FakeTextGenerator(),
        progress_callback=lambda stage, _message: progress_events.append(stage),
    )

    assert generated_prompts == [dialogue]
    assert image_prompts == ["LLM-generated forest illustration"]
    assert speech_text == [dialogue]
    assert progress_events == ["dialogue", "images", "images", "speech", "video"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", video_path.parent.parent.name)
    assert video_path.parent.parent.parent == tmp_path
    assert image_paths[0].parent == video_path.parent / "images"
    assert image_paths[0].name == "shot_001.png"
    assert image_paths[0].read_bytes() == b"image"
    assert (video_path.parent / "dialogue.txt").read_text(encoding="utf-8").strip() == dialogue
    assert (video_path.parent / "narration.mp3").read_bytes() == b"audio"


def test_video_assembly_terminates_ffmpeg_when_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card_path = tmp_path / "card.png"
    audio_path = tmp_path / "narration.mp3"
    card_path.write_bytes(b"image")
    audio_path.write_bytes(b"audio")
    monkeypatch.setattr(video_generator.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(video_generator, "_audio_duration", lambda _path: 1.0)

    class FakeProcess:
        returncode = None
        terminated = False

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = -15

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            return "", ""

    process = FakeProcess()
    monkeypatch.setattr(video_generator.subprocess, "Popen", lambda *_args, **_kwargs: process)
    cancellation_checks = iter((False, False, True))

    with pytest.raises(video_generator.VideoGenerationCancelled):
        video_generator._assemble_video(
            [card_path],
            audio_path,
            tmp_path / "video.mp4",
            lambda: next(cancellation_checks),
        )

    assert process.terminated


def _write_fake_image(
    image_prompt: str,
    output_path: Path,
    image_prompts: list[str],
    image_paths: list[Path],
) -> None:
    image_prompts.append(image_prompt)
    image_paths.append(output_path)
    output_path.write_bytes(b"image")
