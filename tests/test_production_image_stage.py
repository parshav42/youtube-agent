"""Pollinations image acquisition integration tests using local providers."""

from dataclasses import dataclass, field
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from app import main as cli
from app.agents.quality_agent import evaluate_script
from app.agents.script_agent import MockScriptGenerator
from app.database.db import review_script, save_script
from app.schemas.script_schema import ProductionMetadata, ScriptRequest
from app.video.image_generator import ImageGenerationError
from app.video.production_service import ProductionService
from app.video.tts_engine import AudioMetadata
from app.video.visual_asset_manager import LicensedLocalAssetManager, VisualAssetError


def _png_bytes():
    image = Image.effect_noise((400, 400), 90).convert("RGB")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class StubImageProvider:
    provider_name = "Pollinations AI test stub"
    model = "flux-test"

    def __init__(self):
        self.prompts = []

    def generate(self, prompt, output_path):
        self.prompts.append(prompt)
        Path(output_path).with_suffix(".png").write_bytes(_png_bytes())


class MetadataProvider:
    provider_name = "metadata test stub"
    model = "offline"

    def generate_structured(self, *args, **kwargs):
        return ProductionMetadata(
            title="A small promise",
            description="An original story.",
            hashtags=["#Discipline"],
        )


class TTSProvider:
    provider_name = "audio test stub"
    voice = "offline"

    def synthesize(self, script, output_path):
        return AudioMetadata(
            path=str(output_path),
            provider=self.provider_name,
            voice=self.voice,
            duration_seconds=float(script.target_duration_seconds),
            codec="mp3",
        )


class RetryingAssembler:
    def __init__(self):
        self.calls = 0

    def ensure_available(self):
        return None

    def assemble(self, scenes, visuals, audio_path, subtitle_path, output_path):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("intentional first render interruption")
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"offline video fixture")
        return {"path": str(output_path), "duration_seconds": 35.0}


@dataclass
class AudioQA:
    technical_status: str = "passed"
    findings: list[str] = field(default_factory=list)


def _approved_script(database_path):
    script = MockScriptGenerator().generate(ScriptRequest(topic="discipline"))
    evaluation = evaluate_script(script)
    record = save_script(script, evaluation=evaluation, database_path=database_path)
    review_script(record["id"], "approved", "reviewer", database_path)
    return script


def test_production_generates_images_manifest_and_reuses_them_on_resume(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "scripts.sqlite3"
    script = _approved_script(database_path)
    library = tmp_path / "licensed-assets"
    library.mkdir()
    manual_entry = {
        "source": "Local creator",
        "license": "CC-BY-4.0",
        "rights_holder": "Creator",
    }
    (library / "LICENSES.json").write_text(
        json.dumps({"manual_asset.jpg": manual_entry}), encoding="utf-8"
    )
    provider = StubImageProvider()
    assembler = RetryingAssembler()
    monkeypatch.setenv("IMAGE_PROVIDER", "pollinations")
    monkeypatch.setenv("MOTIVATIONOS_LICENSED_ASSET_DIR", str(library))
    monkeypatch.setattr(
        "app.video.production_service.build_image_provider", lambda: provider
    )
    monkeypatch.setattr(
        "app.video.production_service.run_audio_quality_assurance",
        lambda *args, **kwargs: AudioQA(),
    )
    monkeypatch.setattr(
        "app.video.production_service.run_video_quality_assurance",
        lambda path: {"technical_status": "passed", "findings": []},
    )
    service = ProductionService(
        database_path=database_path,
        output_root=tmp_path / "productions",
        llm_provider=MetadataProvider(),
        tts_provider=TTSProvider(),
        assembler=assembler,
    )

    first_attempt = service.produce_script_record(1)
    assert first_attempt["status"] == "failed"
    assert "intentional first render interruption" in first_attempt["error"]
    assert len(provider.prompts) == len(script.scenes)

    manifest_path = library / "LICENSES.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest) == len(script.scenes) + 1
    assert manifest["manual_asset.jpg"] == manual_entry
    for scene in script.scenes:
        filename = f"scene_{scene.scene_number:02d}.png"
        assert (library / filename).is_file()
        assert manifest[filename]["source"] == "Pollinations AI (generated)"
        assert manifest[filename]["prompt"] == scene.visual_description
        assert manifest[filename]["model"] == provider.model

    resumed = service.resume(first_attempt["id"])
    assert resumed["status"] == "completed"
    assert len(provider.prompts) == len(script.scenes)


def test_missing_visual_description_raises_clear_visual_asset_error(
    tmp_path, monkeypatch
):
    script = MockScriptGenerator().generate(ScriptRequest(topic="discipline"))
    scene = script.scenes[0].model_copy(update={"visual_description": ""})
    monkeypatch.setenv("IMAGE_PROVIDER", "pollinations")
    monkeypatch.setenv("MOTIVATIONOS_LICENSED_ASSET_DIR", str(tmp_path / "assets"))
    monkeypatch.setattr(
        "app.video.production_service.build_image_provider", StubImageProvider
    )
    service = ProductionService(database_path=tmp_path / "scripts.sqlite3")

    with pytest.raises(VisualAssetError, match="Scene 1.*missing visual_description"):
        service._acquire_visuals([scene], tmp_path / "output")


def test_asset_manager_accepts_documented_pollinations_provenance(tmp_path):
    library = tmp_path / "assets"
    library.mkdir()
    (library / "scene_01.png").write_bytes(_png_bytes())
    manifest = {
        "scene_01.png": {
            "source": "Pollinations AI (generated)",
            "license": "See https://pollinations.ai terms",
            "author": "Pollinations.AI",
            "url": "https://pollinations.ai",
            "model": "flux",
            "prompt": "Vertical image of a student studying.",
            "generated_at": "2026-10-02T00:00:00+00:00",
        }
    }
    (library / "LICENSES.json").write_text(json.dumps(manifest), encoding="utf-8")
    scene = MockScriptGenerator().generate(ScriptRequest(topic="discipline")).scenes[0]

    acquired = LicensedLocalAssetManager(library).acquire([scene], tmp_path / "output")

    assert acquired[0]["source"] == "Pollinations AI (generated)"
    assert acquired[0]["rights_holder"] == "Pollinations.AI"


def test_generate_images_cli_reports_partial_files_and_manifest(tmp_path, monkeypatch, capsys):
    database_path = tmp_path / "scripts.sqlite3"
    script = MockScriptGenerator().generate(ScriptRequest(topic="discipline"))
    record = save_script(
        script,
        evaluation=evaluate_script(script),
        database_path=database_path,
    )
    library = tmp_path / "assets"

    class PartialProvider:
        provider_name = "Pollinations AI test stub"
        model = "flux-test"

        def __init__(self):
            self.calls = 0

        def generate(self, prompt, output_path):
            self.calls += 1
            if self.calls == 2:
                raise ImageGenerationError("HTTP 402 access required", status_code=402)
            Path(output_path).with_suffix(".png").write_bytes(_png_bytes())

    monkeypatch.setenv("IMAGE_PROVIDER", "pollinations")
    monkeypatch.setattr(cli, "get_licensed_asset_path", lambda: library)
    monkeypatch.setattr(cli, "build_image_provider", PartialProvider)

    exit_code = cli.generate_images(record["id"], str(database_path))

    output = capsys.readouterr()
    assert exit_code == 1
    assert f"Wrote image: {library / 'scene_01.png'}" in output.out
    assert f"LICENSES.json: {library / 'LICENSES.json'}" in output.out
    assert "Scene 2 image generation failed" in output.err


def test_regenerate_image_cli_forces_only_the_requested_scene(tmp_path, monkeypatch, capsys):
    database_path = tmp_path / "scripts.sqlite3"
    script = MockScriptGenerator().generate(ScriptRequest(topic="discipline"))
    record = save_script(
        script,
        evaluation=evaluate_script(script),
        database_path=database_path,
    )
    library = tmp_path / "assets"
    library.mkdir()
    (library / "scene_02.png").write_bytes(_png_bytes())

    class RegenerationProvider:
        provider_name = "Pollinations AI test stub"
        model = "flux-test"

        def __init__(self):
            self.prompts = []

        def generate(self, prompt, output_path):
            self.prompts.append(prompt)
            Path(output_path).with_suffix(".png").write_bytes(_png_bytes())

    provider = RegenerationProvider()
    monkeypatch.setenv("IMAGE_PROVIDER", "pollinations")
    monkeypatch.setattr(cli, "get_licensed_asset_path", lambda: library)
    monkeypatch.setattr(cli, "build_image_provider", lambda: provider)

    exit_code = cli.generate_images(
        record["id"], str(database_path), scene_number=2, force=True
    )

    output = capsys.readouterr()
    assert exit_code == 0
    assert provider.prompts == [script.scenes[1].visual_description]
    assert f"Wrote image: {library / 'scene_02.png'}" in output.out
    assert "scene_01" not in output.out