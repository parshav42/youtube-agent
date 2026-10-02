"""Run and resume production for approved Day 2 scripts."""

import json
import uuid
from pathlib import Path
from typing import Any

from app.agents.metadata_agent import generate_metadata
from app.config import get_database_path, get_production_output_path
from app.audio.quality import run_audio_quality_assurance
from app.database.db import (
    create_production,
    get_production_by_id,
    get_script_by_id,
    update_production,
)
from app.providers.huggingface_llm import HuggingFaceLLMProvider
from app.providers.llm_factory import build_llm_provider
from app.schemas.script_schema import MotivationalScript, ScriptEvaluation
from app.video.subtitle_engine import generate_srt
from app.video.tts_engine import build_tts_provider, validate_audio
from app.video.video_assembler import VideoAssembler
from app.video.quality import run_video_quality_assurance
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
        """Create a production only from an explicitly human-approved script."""
        script_record = self._load_approved_script(script_id)
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
        script_record = self._load_approved_script(production["script_id"])
        script = MotivationalScript.model_validate(script_record["script"])
        return self._run(production, script)

    def _load_approved_script(self, script_id: int) -> dict[str, Any]:
        script_record = get_script_by_id(script_id, self.database_path)
        if script_record is None:
            raise ValueError(f"Script record {script_id} was not found.")
        if script_record["status"] != "approved":
            status = script_record["status"]
            if status == "completed":
                raise ValueError(
                    f"Script record {script_id} has legacy status 'completed'; explicit human approval is required."
                )
            raise ValueError(
                f"Script record {script_id} is {status!r}; explicit human approval is required before production."
            )
        if not script_record["reviewed_at"] or not script_record["reviewed_by"]:
            raise ValueError("Approved script is missing its human-review audit metadata.")
        try:
            evaluation = ScriptEvaluation.model_validate(script_record["evaluation"])
        except Exception as error:
            raise ValueError("Approved script has no valid quality evaluation.") from error
        if not evaluation.passed or not all(
            value
            for name, value in evaluation.model_dump().items()
            if name not in {"quality_score", "threshold", "passed", "feedback"}
        ):
            raise ValueError("Approved script does not have a passing quality evaluation.")
        return script_record

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
            llm_provider = self.llm_provider or build_llm_provider()
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
            audio_qa = run_audio_quality_assurance(
                audio.path, target_duration_seconds=script.target_duration_seconds
            )
            metadata["audio_qa"] = _serialize_audio_qa(audio_qa)
            if audio_qa.technical_status == "failed":
                raise RuntimeError(
                    "Audio QA did not pass; video rendering is blocked ("
                    + "; ".join(audio_qa.findings)
                    + ")."
                )
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
            video_qa = run_video_quality_assurance(video["path"])
            metadata["video_qa"] = video_qa
            if video_qa["technical_status"] != "passed":
                raise RuntimeError(
                    "Video QA did not pass; production cannot be approved ("
                    + "; ".join(video_qa["findings"])
                    + ")."
                )
            self._update(production_id, "rendering", metadata)
            status = "needs_review" if audio_qa.technical_status == "needs_review" else "completed"
            self._write_metadata(output_dir, production_id, status, metadata, None)
            return self._update(production_id, status, metadata)
        except Exception as error:
            message = _safe_error(error)
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


def _serialize_audio_qa(report: Any) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(report)


def _safe_error(error: Exception) -> str:
    """Persist concise diagnostics with credential-like values redacted."""
    import os
    import re

    message = f"{type(error).__name__}: {error}"
    for key in ("HF_TOKEN", "ELEVENLABS_API_KEY", "MOTIVATIONOS_ADMIN_TOKEN"):
        secret = os.getenv(key)
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return re.sub(r"(?i)(token|api[_ -]?key|authorization)(\s*[:=]\s*)[^\s,;]+", r"\1\2[REDACTED]", message)[:3000]
