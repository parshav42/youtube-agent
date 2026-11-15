from pathlib import Path
from typing import Any

import pytest

from app import main as app_main


def test_cli_saves_generated_video_to_supabase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    (project_root / "app").mkdir(parents=True)
    monkeypatch.setattr(app_main, "__file__", str(project_root / "app" / "main.py"))
    monkeypatch.setattr(app_main, "load_dotenv", lambda *_args: None)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("HF_TOKEN", "test-token")
    monkeypatch.setattr("builtins.input", lambda _prompt: "A story")
    monkeypatch.setattr(app_main, "ElevenLabs", lambda **_kwargs: object())

    saved_jobs: list[dict[str, Any]] = []

    class FakeStore:
        def save_job(self, job: dict[str, Any]) -> None:
            saved_jobs.append(dict(job))

        def upload_assets(self, job_id: str, run_dir: Path) -> dict[str, str]:
            return {
                path.relative_to(run_dir).as_posix(): (
                    f"supabase://video-assets/{job_id}/{path.relative_to(run_dir).as_posix()}"
                )
                for path in run_dir.rglob("*")
                if path.is_file()
            }

    store = FakeStore()
    monkeypatch.setattr(
        app_main.SupabaseStore,
        "from_env",
        classmethod(lambda cls: store),
    )

    def fake_create_video(
        *,
        prompt: str,
        client: object,
        output_dir: Path,
        image_prompt_generator: object,
        text_generator: object,
        progress_callback: Any,
    ) -> Path:
        assert prompt == "A story"
        assert client is not None
        run_dir = output_dir / "2026-01-01" / "a-story"
        (run_dir / "images").mkdir(parents=True)
        video_path = run_dir / "a-story.mp4"
        video_path.write_bytes(b"video")
        (run_dir / "dialogue.txt").write_text("Dialogue", encoding="utf-8")
        (run_dir / "narration.mp3").write_bytes(b"audio")
        (run_dir / "images" / "shot_001.png").write_bytes(b"image")
        progress_callback("video", "Assembling video")
        return video_path

    monkeypatch.setattr(app_main, "create_video", fake_create_video)

    app_main.main()

    assert len(saved_jobs) == 3
    completed_job = saved_jobs[-1]
    assert completed_job["idea"] == "A story"
    assert completed_job["status"] == "complete"
    assert completed_job["result"]["videoUrl"].startswith("supabase://video-assets/")
    assert completed_job["result"]["imageUrls"]
