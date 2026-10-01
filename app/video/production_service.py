"""Run and resume production for approved Day 2 scripts."""

import json
import uuid
from pathlib import Path
from typing import Any

from app.agents.metadata_agent import generate_metadata
from app.config import get_database_path, get_production_output_path
from app.database.db import (
    create_production,
    get_production_by_id,
    get_script_by_id,
    update_production,
)
from app.providers.huggingface_llm import HuggingFaceLLMProvider
from app.schemas.script_schema import MotivationalScript
from app.video.subtitle_engine import generate_srt
from app.video.tts_engine import build_tts_provider, validate_audio
from app.video.video_assembler import VideoAssembler
from app.video.visual_asset_manager import LicensedLocalAssetManager


class ProductionService:
    """Coordinate real providers and persist every production stage and outcome."""

    def __init__(
        self,
        database_path: str | Path | None = None,
        output_root: str | Path | None = None,
        *,
        llm_provider: HuggingFaceLLMProvider | None = None,
        tts_provider: Any | None = None,
        asset_manager: Any | None = None,
        assembler: VideoAssembler | None = None,
    ) -> None:
        self.database_path = database_path or get_database_path()
        self.output_root = Path(output_root) if output_root else get_production_output_path()
        self.llm_provider = llm_provider
        self.tts_provider = tts_provider
        self.asset_manager = asset_manager
        self.assembler = assembler or VideoAssembler()

    def produce_script_record(self, script_id: int) -> dict[str, Any]:
        """Create a production from one quality-approved, persisted Day 2 script."""
        script_record = get_script_by_id(script_id, self.database_path)
        if script_record is None:
            raise ValueError(f"Script record {script_id} was not found.")
        if script_record["status"] != "completed":
            raise ValueError(
                f"Script record {script_id} is {script_record['status']!r}; only completed scripts can be produced."
            )
        script = MotivationalScript.model_validate(script_record["script"])
        production_id = uuid.uuid4().hex
        output_dir = self.output_root / production_id
        production = create_production(
            production_id,
            script_id,
            script.topic,
            output_dir,
            metadata={"script_record_id": script_id, "language": script.language},
            database_path=self.database_path,
        )
        return self._run(production, script)

    def resume(self, production_id: str) -> dict[str, Any]:
        """Resume a failed production, validating and reusing completed intermediates."""
        production = get_production_by_id(production_id, self.database_path)
        if production is None:
            raise ValueError(f"Production {production_id} was not found.")
        if production["status"] != "failed":
            raise ValueError(
                f"Production {production_id} has status {production['status']!r}; only failed runs can resume."
            )
        script_record = get_script_by_id(production["script_id"], self.database_path)
        if script_record is None or script_record["status"] != "completed":
            raise ValueError("The source approved script record is no longer available.")
        script = MotivationalScript.model_validate(script_record["script"])
        return self._run(production, script)

    def _run(
        self, production: dict[str, Any], script: MotivationalScript
    ) -> dict[str, Any]:
        production_id = production["id"]
        output_dir = Path(production["output_dir"])
        metadata = dict(production.get("metadata", {}))
        metadata["script_record_id"] = production["script_id"]
        metadata["script_topic"] = script.topic
        metadata["target_duration_seconds"] = script.target_duration_seconds
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            self.assembler.ensure_available()
            llm_provider = self.llm_provider or HuggingFaceLLMProvider.from_environment()
            metadata_output = generate_metadata(script, llm_provider)
            metadata["model_provider"] = llm_provider.provider_name
            metadata["model_identifier"] = llm_provider.model
            metadata["youtube_metadata"] = metadata_output.model_dump(mode="json")
            self._update(production_id, "queued", metadata)

            audio_file = output_dir / "assets" / "narration.mp3"
            saved_audio = metadata.get("audio")
            if saved_audio and Path(saved_audio.get("path", "")).is_file():
                audio = validate_audio(
                    saved_audio["path"],
                    saved_audio.get("provider", "unknown"),
                    saved_audio.get("voice", "unknown"),
                    word_timings=saved_audio.get("word_timings"),
                    alignment_source=saved_audio.get("alignment_source"),
                )
            else:
                self._update(production_id, "generating_audio", metadata)
                tts_provider = self.tts_provider or build_tts_provider()
                audio = tts_provider.synthesize(script, audio_file)
            metadata["audio"] = {
                "path": audio.path,
                "provider": audio.provider,
                "voice": audio.voice,
                "duration_seconds": audio.duration_seconds,
                "codec": audio.codec,
                "word_timings": audio.word_timings,
                "alignment_source": audio.alignment_source,
            }
            self._update(production_id, "generating_audio", metadata)

            self._update(production_id, "acquiring_visuals", metadata)
            asset_manager = self.asset_manager or LicensedLocalAssetManager()
            visuals = asset_manager.acquire(script.scenes, output_dir / "assets" / "visuals")
            metadata["visual_assets"] = visuals
            self._update(production_id, "acquiring_visuals", metadata)

            self._update(production_id, "generating_subtitles", metadata)
            subtitles = generate_srt(
                script,
                audio.duration_seconds,
                output_dir / "assets" / "captions.srt",
                word_timings=audio.word_timings,
            )
            metadata["subtitles"] = {
                "path": subtitles.path,
                "timing_method": subtitles.timing_method,
                "phrase_count": subtitles.phrase_count,
                "safe_margin_note": subtitles.safe_margin_note,
            }
            self._update(production_id, "generating_subtitles", metadata)

            self._update(production_id, "rendering", metadata)
            video = self.assembler.assemble(
                script.scenes,
                visuals,
                audio.path,
                subtitles.path,
                output_dir / "final.mp4",
            )
            metadata["video"] = video
            self._update(production_id, "rendering", metadata)
            self._write_metadata(output_dir, production_id, "completed", metadata, None)
            return self._update(production_id, "completed", metadata)
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            try:
                self._write_metadata(output_dir, production_id, "failed", metadata, message)
            except OSError:
                pass
            return self._update(production_id, "failed", metadata, message)

    def _update(
        self,
        production_id: str,
        status: str,
        metadata: dict[str, Any],
        error: str | None = None,
    ) -> dict[str, Any]:
        return update_production(
            production_id,
            status,
            metadata,
            error=error,
            database_path=self.database_path,
        )

    @staticmethod
    def _write_metadata(
        output_dir: Path,
        production_id: str,
        status: str,
        metadata: dict[str, Any],
        error: str | None,
    ) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "production_id": production_id,
            "status": status,
            "metadata": metadata,
            "error": error,
        }
        temporary = output_dir / "metadata.json.tmp"
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output_dir / "metadata.json")