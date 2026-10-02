"""Production pipeline integration tests; providers/assets are explicitly test-only."""

import base64
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from PIL import Image
import pytest

from app.agents.quality_agent import evaluate_script
from app.agents.script_agent import MockScriptGenerator
from app.api import create_app
from app.database.db import get_production_by_id, review_script, save_script
from app.graph.workflow import build_workflow
from app.schemas.script_schema import ProductionMetadata, ScriptRequest
from app.video.production_service import ProductionService
from app.video.subtitle_engine import generate_srt
from app.video.tts_engine import EdgeTTSProvider, ElevenLabsTTSProvider, TTSError, build_tts_provider
from app.video.video_assembler import VideoAssembler, VideoAssemblyError
from app.video.visual_asset_manager import LicensedLocalAssetManager, VisualAssetError
from app.youtube.publisher import YouTubePublishError, YouTubePublisher
from app.youtube.repository import initialize_youtube_database


class HFMetadataFixture:
    """Explicit offline fixture standing in for HF only inside integration tests."""

    provider_name = "test-only HF fixture"
    model = "test/model"

    def generate_structured(self, response_model, system_prompt, user_prompt, **kwargs):
        assert response_model is ProductionMetadata
        return ProductionMetadata(
            title="Bas Ek Page: A Discipline Story",
            description="A student chooses one small action and repeats it.",
            hashtags=["#Discipline", "#Hinglish"],
        )


class ToneTTSFixture:
    """Generate test-only tone audio so rendering uses real decodable media."""

    provider_name = "test-only FFmpeg tone, not speech"
    voice = "fixture"

    def __init__(self):
        self.calls = 0

    def synthesize(self, script, output_path):
        self.calls += 1
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
                "-c:a", "libmp3lame", "-q:a", "7", str(output),
            ],
            check=True,
            capture_output=True,
        )
        from app.video.tts_engine import validate_audio

        return validate_audio(output, self.provider_name, self.voice)


class ProductionServiceFixture:
    def __init__(self):
        self.calls = []

    def produce_script_record(self, script_id):
        self.calls.append(("produce", script_id))
        return {"id": "test-production", "script_id": script_id, "status": "queued"}

    def resume(self, production_id):
        self.calls.append(("resume", production_id))
        return {"id": production_id, "script_id": 1, "status": "completed"}


def make_approved_script(database_path: Path) -> int:
    script = MockScriptGenerator().generate(ScriptRequest(topic="discipline"))
    evaluation = evaluate_script(script)
    record = save_script(script, evaluation=evaluation, database_path=database_path)
    return review_script(record["id"], "approved", "test-reviewer", database_path)["id"]


def make_asset_library(path: Path, scene_count: int = 5) -> None:
    path.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for scene_number in range(1, scene_count + 1):
        name = f"scene_{scene_number:02d}.png"
        Image.new("RGB", (640, 360), color=(scene_number * 25, 60, 110)).save(path / name)
        manifest[name] = {
            "source": f"Test-only generated fixture {scene_number}",
            "license": "CC0-1.0 test fixture",
            "rights_holder": "MotivationOS test suite",
            "attribution": "Not production footage",
        }
    (path / "LICENSES.json").write_text(json.dumps(manifest), encoding="utf-8")


class FakeTTSHTTPResponse:
    status_code = 200

    def __init__(self, audio, alignment):
        self._audio = audio
        self._alignment = alignment
        self.content = b"application/json"

    def json(self):
        return {
            "audio_base64": base64.b64encode(self._audio).decode("ascii"),
            "normalized_alignment": self._alignment,
        }

    def raise_for_status(self):
        return None


class FakeTTSHTTPClient:
    def __init__(self, response):
        self.response = response
        self.call = None

    def post(self, *args, **kwargs):
        self.call = (args, kwargs)
        return self.response


def make_test_mp3(path: Path) -> bytes:
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:a", "libmp3lame", "-q:a", "7", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path.read_bytes()


def test_subtitle_engine_writes_utf8_phrase_srt_with_approximate_timing(tmp_path) -> None:
    script = MockScriptGenerator().generate(ScriptRequest(topic="discipline"))
    output = tmp_path / "captions.srt"

    result = generate_srt(script, 32.4, output, max_words_per_phrase=5)

    content = output.read_text(encoding="utf-8")
    assert result.timing_method.startswith("approximate")
    assert result.phrase_count > len(script.scenes)
    assert "Subah alarm" in content
    assert "-->" in content


def test_visual_asset_manager_requires_license_provenance(tmp_path) -> None:
    script = MockScriptGenerator().generate(ScriptRequest(topic="discipline"))
    library = tmp_path / "assets"
    library.mkdir()
    Image.new("RGB", (640, 360)).save(library / "scene_01.png")
    (library / "LICENSES.json").write_text("{}", encoding="utf-8")

    try:
        LicensedLocalAssetManager(library).acquire(script.scenes[:1], tmp_path / "output")
    except VisualAssetError as error:
        assert "source, license, and rights_holder" in str(error)
    else:
        raise AssertionError("Visuals without license provenance must be rejected.")


def test_video_assembler_refuses_existing_output_before_rendering(tmp_path) -> None:
    script = MockScriptGenerator().generate(ScriptRequest(topic="discipline"))
    assets = []
    for scene in script.scenes:
        asset_path = tmp_path / f"scene_{scene.scene_number}.png"
        asset_path.write_bytes(b"synthetic fixture")
        assets.append({"scene_number": scene.scene_number, "path": str(asset_path)})
    audio_path = tmp_path / "narration.wav"
    subtitle_path = tmp_path / "captions.srt"
    output_path = tmp_path / "final.mp4"
    audio_path.write_bytes(b"synthetic fixture")
    subtitle_path.write_text("synthetic fixture", encoding="utf-8")
    output_path.write_bytes(b"preserve this file")
    assembler = VideoAssembler()
    assembler.ensure_available = lambda: None

    with pytest.raises(VideoAssemblyError, match="refusing to overwrite"):
        assembler.assemble(
            script.scenes, assets, audio_path, subtitle_path, output_path
        )

    assert output_path.read_bytes() == b"preserve this file"


def test_langgraph_routes_video_production_and_resume_inputs(tmp_path) -> None:
    service = ProductionServiceFixture()
    graph = build_workflow(
        database_path=str(tmp_path / "routing.sqlite3"), production_service=service
    )

    produced = graph.invoke({"production_script_id": 9})
    resumed = graph.invoke({"resume_production_id": "prior-run"})

    assert produced["production_result"]["script_id"] == 9
    assert resumed["production_result"]["status"] == "completed"
    assert service.calls == [("produce", 9), ("resume", "prior-run")]


def test_elevenlabs_uses_credentials_and_actual_word_alignment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOTIVATIONOS_TTS_PROVIDER", "elevenlabs")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(TTSError, match="ELEVENLABS_API_KEY"):
        build_tts_provider()

    script = MockScriptGenerator().generate(ScriptRequest(topic="discipline"))
    chars = list(script.narration)
    starts = [index * 0.01 for index in range(len(chars))]
    ends = [(index + 1) * 0.01 for index in range(len(chars))]
    audio_bytes = make_test_mp3(tmp_path / "fixture.mp3")
    client = FakeTTSHTTPClient(
        FakeTTSHTTPResponse(
            audio_bytes,
            {
                "characters": chars,
                "character_start_times_seconds": starts,
                "character_end_times_seconds": ends,
            },
        )
    )
    provider = ElevenLabsTTSProvider("test-secret", "authorized-voice", http_client=client)

    audio = provider.synthesize(script, tmp_path / "narration.mp3")
    subtitles = generate_srt(
        script,
        audio.duration_seconds,
        tmp_path / "aligned.srt",
        word_timings=audio.word_timings,
    )

    assert audio.provider == "ElevenLabs Text to Speech"
    assert audio.word_timings
    assert "with-timestamps" in client.call[0][0]
    assert client.call[1]["headers"]["xi-api-key"] == "test-secret"
    assert subtitles.timing_method.startswith("provider character-level")


@pytest.mark.parametrize("provider_name", ["edge", "elevenlabs"])
def test_tts_provider_refuses_existing_output_without_calling_service(
    provider_name, tmp_path, monkeypatch
) -> None:
    script = MockScriptGenerator().generate(ScriptRequest(topic="discipline"))
    output_path = tmp_path / "existing.mp3"
    output_path.write_bytes(b"preserve this audio")

    class EdgeTTSModule:
        class Communicate:
            def __init__(self, *args, **kwargs):
                pytest.fail("Edge TTS must not be called for an existing output.")

    class HTTPClient:
        def post(self, *args, **kwargs):
            pytest.fail("ElevenLabs must not be called for an existing output.")

    if provider_name == "edge":
        monkeypatch.setitem(sys.modules, "edge_tts", EdgeTTSModule)
        provider = EdgeTTSProvider()
    else:
        provider = ElevenLabsTTSProvider("fixture-key", "fixture-voice", http_client=HTTPClient())

    with pytest.raises(TTSError, match="refusing to overwrite"):
        provider.synthesize(script, output_path)

    assert output_path.read_bytes() == b"preserve this audio"


def test_production_failure_persists_audio_and_resume_renders_playable_mp4(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "production.sqlite3"
    monkeypatch.setenv("MOTIVATIONOS_ADMIN_TOKEN", "temporary-review-token")
    monkeypatch.setenv("MOTIVATIONOS_SCHEDULER_ENABLED", "false")
    script = MockScriptGenerator().generate(ScriptRequest(topic="discipline"))
    evaluation = evaluate_script(script)
    script_record = save_script(script, evaluation=evaluation, database_path=database_path)
    script_id = script_record["id"]
    library = tmp_path / "licensed-assets"
    output_root = tmp_path / "productions"
    tts = ToneTTSFixture()
    service = ProductionService(
        database_path,
        output_root,
        llm_provider=HFMetadataFixture(),
        tts_provider=tts,
        asset_manager=LicensedLocalAssetManager(library),
    )

    with pytest.raises(ValueError, match="explicit human approval"):
        service.produce_script_record(script_id)
    assert not output_root.exists()
    review_script(script_id, "approved", "temporary-reviewer", database_path)

    failed = service.produce_script_record(script_id)

    assert failed["status"] == "failed"
    assert failed["metadata"]["audio"]["path"].endswith("narration.mp3")
    assert "Licensed visual library unavailable" in failed["error"]
    make_asset_library(library)

    completed = service.resume(failed["id"])

    assert completed["status"] == "needs_review"
    assert tts.calls == 1
    video = completed["metadata"]["video"]
    assert Path(video["path"]).is_file()
    assert (video["width"], video["height"]) == (1080, 1920)
    assert video["video_codec"] == "h264"
    assert video["audio_codec"] == "aac"
    assert video["duration_seconds"] > 0
    assert abs(video["duration_seconds"] - completed["metadata"]["audio"]["duration_seconds"]) < 0.15
    assert completed["metadata"]["subtitles"]["timing_method"].startswith("approximate")
    assert len(completed["metadata"]["visual_assets"]) == 5
    assert completed["metadata"]["audio_qa"]["technical_status"] == "needs_review"
    assert completed["metadata"]["video_qa"]["technical_status"] == "passed"
    assert get_production_by_id(failed["id"], database_path)["status"] == "needs_review"

    application = create_app(database_path)
    route = next(route for route in application.routes if getattr(route, "path", None) == "/api/productions/{production_id}/review")
    detail = route.endpoint(failed["id"])
    assert detail["status"] == "needs_review"
    assert detail["title"] == "Bas Ek Page: A Discipline Story"
    assert detail["description"]
    assert detail["audio_qa"]["technical_status"] == "needs_review"
    assert detail["video_qa"]["technical_status"] == "passed"
    assert detail["script"]["narration"] == script.narration
    assert not {"access_token", "refresh_token", "client_secret"}.intersection(detail)
    require_admin = route.dependant.dependencies[0].call
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials
    with pytest.raises(HTTPException) as unauthorized:
        require_admin(credentials=None)
    assert unauthorized.value.status_code == 401
    assert require_admin(credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials="temporary-review-token")) is None
    script_route = next(route for route in application.routes if getattr(route, "path", None) == "/api/scripts/{script_id}/review")
    assert script_route.endpoint(script_id)["script"] == script.model_dump(mode="json")

    video_route = next(route for route in application.routes if getattr(route, "path", None) == "/api/productions/{production_id}/video")
    preview = video_route.endpoint(failed["id"])
    assert preview.media_type == "video/mp4"
    assert Path(preview.path).read_bytes() == Path(video["path"]).read_bytes()
    page_route = next(route for route in application.routes if getattr(route, "path", None) == "/production-review")
    page = page_route.endpoint()
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    review_html = Path(page.path).read_text(encoding="utf-8")
    review_js = (Path(__file__).resolve().parents[1] / "app" / "static" / "production-review.js").read_text(encoding="utf-8")
    assert "Final video review" in review_html
    assert ".textContent =" in review_js
    assert ".innerHTML" not in review_js
    assert "/upload" not in review_html + review_js
    with pytest.raises(YouTubePublishError, match="Only completed productions"):
        YouTubePublisher(database_path).request_review(failed["id"])
    initialize_youtube_database(database_path)
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT COUNT(*) FROM youtube_uploads").fetchone()[0] == 0


def test_audio_qa_failure_blocks_visual_acquisition_and_rendering(tmp_path, monkeypatch) -> None:
    from app.audio.quality import run_audio_quality_assurance
    import app.video.production_service as production_module

    database_path = tmp_path / "audio-qa-block.sqlite3"
    script_id = make_approved_script(database_path)
    tts = ToneTTSFixture()
    missing_audio_report = run_audio_quality_assurance(tmp_path / "missing-audio.mp3")
    monkeypatch.setattr(
        production_module, "run_audio_quality_assurance", lambda *args, **kwargs: missing_audio_report
    )

    class AssetManager:
        def acquire(self, *args, **kwargs):
            pytest.fail("Visual acquisition must not run after failed audio QA.")

    service = ProductionService(
        database_path,
        tmp_path / "productions",
        llm_provider=HFMetadataFixture(),
        tts_provider=tts,
        asset_manager=AssetManager(),
    )
    result = service.produce_script_record(script_id)

    assert result["status"] == "failed"
    assert result["metadata"]["audio_qa"]["technical_status"] == "failed"
    assert "Audio QA did not pass" in result["error"]
    assert not (Path(result["output_dir"]) / "final.mp4").exists()


def test_missing_hf_configuration_is_persisted_without_fake_audio(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "huggingface")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HF_MODEL", raising=False)
    database_path = tmp_path / "no-hf.sqlite3"
    script_id = make_approved_script(database_path)
    tts = ToneTTSFixture()
    service = ProductionService(database_path, tmp_path / "productions", tts_provider=tts)

    result = service.produce_script_record(script_id)

    assert result["status"] == "failed"
    assert "HF_TOKEN" in result["error"]
    assert tts.calls == 0
    assert "audio" not in result["metadata"]
