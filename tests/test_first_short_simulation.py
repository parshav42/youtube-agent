"""Isolated first-Short integration simulation; all external providers are mocked."""

import json
import math
import wave
from pathlib import Path
from types import SimpleNamespace

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from PIL import Image

from app.agents.quality_agent import evaluate_script
from app.agents.script_agent import MockScriptGenerator
from app.audio.generation import AudioProvider, MockAudioProvider
from app.audio.review_service import generate_audio_for_human_review
from app.database.db import (
    create_production,
    get_script_by_id,
    review_script,
    save_script,
    update_production,
)
from app.schemas.script_schema import ProductionMetadata, ScriptRequest
from app.api import create_app
from app.video.subtitle_engine import generate_srt
from app.video.video_assembler import VideoAssembler, validate_video
from app.video.visual_asset_manager import LicensedLocalAssetManager
from app.youtube.publisher import YouTubePublisher
from app.youtube.repository import get_upload


class SyntheticAudioProvider:
    """Write a short synthetic WAV long enough to exercise video rendering."""

    provider_name = "test-only synthetic tone (not speech)"

    def generate(self, approved_script_text: str, output_path: str | Path) -> Path:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        sample_rate = 8000
        frame_count = sample_rate * 5
        samples = (
            int(6000 * math.sin(2 * math.pi * 440 * index / sample_rate))
            for index in range(frame_count)
        )
        with wave.open(str(destination), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes(b"".join(sample.to_bytes(2, "little", signed=True) for sample in samples))
        return destination


def _make_synthetic_asset_library(path: Path, scene_count: int) -> None:
    path.mkdir(parents=True)
    manifest = {}
    for scene_number in range(1, scene_count + 1):
        filename = f"scene_{scene_number:02d}.png"
        Image.new("RGB", (640, 640), color=(scene_number * 25, 70, 120)).save(path / filename)
        manifest[filename] = {
            "source": "Synthetic integration-test image",
            "license": "Test fixture; not production media",
            "rights_holder": "MotivationOS test suite",
        }
    (path / "LICENSES.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_first_short_flow_is_isolated_and_requires_explicit_upload_approval(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "isolated.sqlite3"
    output_root = tmp_path / "production"
    output_root.mkdir()
    monkeypatch.setenv("MOTIVATIONOS_ADMIN_TOKEN", "test-admin-token")

    script = MockScriptGenerator().generate(ScriptRequest(topic="synthetic short"))
    evaluation = evaluate_script(script)
    assert evaluation.passed is True
    saved_script = save_script(script, evaluation=evaluation, database_path=database_path)

    rejected_audio = generate_audio_for_human_review(
        saved_script["id"], database_path, tmp_path / "audio", "blocked.wav", MockAudioProvider()
    )
    assert rejected_audio.workflow_status == "blocked"
    assert rejected_audio.generation_status == "not_run"

    encryption_key = Fernet.generate_key()
    cipher = Fernet(encryption_key)

    class OAuthFixture:
        def authorized_credentials(self):
            return object()

        def encrypt_persistent_secret(self, value):
            return cipher.encrypt(value.encode()).decode()

        def decrypt_persistent_secret(self, value):
            return cipher.decrypt(value.encode()).decode()

    class UploadRequest:
        def __init__(self, should_fail: bool) -> None:
            self.should_fail = should_fail
            self.resumable_uri = None

        def next_chunk(self, num_retries):
            if self.should_fail:
                self.resumable_uri = "https://upload.example.test/session/synthetic"
                raise RuntimeError("simulated network interruption")
            assert self.resumable_uri == "https://upload.example.test/session/synthetic"
            return SimpleNamespace(progress=lambda: 1.0), {
                "id": "mock-youtube-video-id",
                "status": {"privacyStatus": "private"},
            }

    requests = [UploadRequest(should_fail=True), UploadRequest(should_fail=False)]
    submitted_bodies = []

    class Videos:
        def insert(self, **kwargs):
            submitted_bodies.append(kwargs["body"])
            return requests.pop(0)

    api_builds = []

    def build_mock_youtube_api(credentials):
        api_builds.append(credentials)
        return SimpleNamespace(videos=lambda: Videos())

    publisher = YouTubePublisher(
        database_path,
        oauth=OAuthFixture(),
        api_builder=build_mock_youtube_api,
        media_upload_factory=lambda path: object(),
    )
    application = create_app(database_path, publisher=publisher)

    with TestClient(application) as client:
        headers = {"Authorization": "Bearer test-admin-token"}
        assert client.get("/api/scripts/review").status_code == 401
        queue = client.get("/api/scripts/review", headers=headers)
        assert queue.status_code == 200
        assert [record["id"] for record in queue.json()] == [saved_script["id"]]
        detail = client.get(
            f"/api/scripts/{saved_script['id']}/review", headers=headers
        )
        assert detail.status_code == 200
        assert detail.json()["evaluation"]["passed"] is True

        approved = client.post(
            f"/api/scripts/{saved_script['id']}/approve",
            headers=headers,
            json={"confirm_review": True, "reviewer": "synthetic-test-reviewer"},
        )
        assert approved.status_code == 200
        assert client.post(
            f"/api/scripts/{saved_script['id']}/approve",
            headers=headers,
            json={"confirm_review": True, "reviewer": "synthetic-test-reviewer"},
        ).status_code == 409

        audio_result = generate_audio_for_human_review(
            saved_script["id"],
            database_path,
            tmp_path / "audio",
            "narration.wav",
            SyntheticAudioProvider(),
            target_duration_seconds=5,
        )
        assert audio_result.workflow_status == "completed"
        assert audio_result.quality is not None
        assert audio_result.quality.technical_status == "passed"
        assert get_script_by_id(saved_script["id"], database_path)["status"] == "approved"

        asset_library = tmp_path / "synthetic-assets"
        _make_synthetic_asset_library(asset_library, len(script.scenes))
        production_assets = output_root / "assets"
        visuals = LicensedLocalAssetManager(asset_library).acquire(
            script.scenes, production_assets / "visuals"
        )
        subtitle_path = production_assets / "captions.srt"
        subtitles = generate_srt(script, 5, subtitle_path)
        video = VideoAssembler().assemble(
            script.scenes,
            visuals,
            audio_result.generation.path,
            subtitles.path,
            output_root / "final.mp4",
        )
        verified_video = validate_video(video["path"])
        assert (verified_video["width"], verified_video["height"]) == (1080, 1920)
        assert verified_video["audio_stream"] and verified_video["video_stream"]
        assert abs(verified_video["duration_seconds"] - 5) < 0.2

        publishing_metadata = ProductionMetadata(
            title="One Small Synthetic Step",
            description="An original test fixture about starting today.",
            hashtags=["#Discipline"],
            tags=["discipline"],
            language="hi",
        )
        production_id = "synthetic-first-short"
        persisted_metadata = {
            "script_record_id": saved_script["id"],
            "language": script.language,
            "audio": {"path": audio_result.generation.path},
            "video": verified_video,
            "youtube_metadata": publishing_metadata.model_dump(mode="json"),
        }
        create_production(
            production_id,
            saved_script["id"],
            script.topic,
            output_root,
            persisted_metadata,
            database_path,
        )
        update_production(
            production_id, "completed", persisted_metadata, database_path=database_path
        )
        (output_root / "metadata.json").write_text(
            json.dumps(
                {"production_id": production_id, "status": "completed", "metadata": persisted_metadata}
            ),
            encoding="utf-8",
        )

        review = client.post(
            f"/api/publishing/{production_id}/review",
            headers=headers,
            json={"visibility": "private"},
        )
        assert review.status_code == 200
        assert review.json()["approval"]["state"] == "awaiting_review"
        assert review.json()["video"]["metadata"]["title"] == "One Small Synthetic Step"
        assert client.post(
            f"/api/publishing/{production_id}/upload", headers=headers
        ).status_code == 409

        approval = client.post(
            f"/api/publishing/{production_id}/approve",
            headers=headers,
            json={"confirm_approval": True, "visibility": "private"},
        )
        assert approval.status_code == 200
        assert approval.json()["state"] == "approved"

        failed_upload = client.post(
            f"/api/publishing/{production_id}/upload", headers=headers
        )
        assert failed_upload.status_code == 409
        saved_upload = get_upload(production_id, database_path)
        assert saved_upload["status"] == "failed"
        assert saved_upload["resumable_uri"]
        assert "synthetic" not in saved_upload["resumable_uri"]

        retried_upload = client.post(
            f"/api/publishing/{production_id}/upload", headers=headers
        )
        assert retried_upload.status_code == 200, retried_upload.text
        assert retried_upload.json()["status"] == "uploaded_private"
        assert retried_upload.json()["video_id"] == "mock-youtube-video-id"
        assert submitted_bodies[-1]["status"]["privacyStatus"] == "private"
        assert production_id in submitted_bodies[-1]["snippet"]["description"]

        duplicate_upload = client.post(
            f"/api/publishing/{production_id}/upload", headers=headers
        )
        assert duplicate_upload.status_code == 409
        assert len(api_builds) == 2
        assert len(submitted_bodies) == 2

    import sqlite3

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []