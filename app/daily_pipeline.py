"""One-command script-to-manual-upload-package orchestration."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import (
    get_daily_pipeline_settings, get_database_path, get_licensed_asset_path,
    get_quality_threshold, get_topic,
)
from app.database.db import (
    delete_unreviewed_script, get_production_by_id,
    review_script, update_production,
)
from app.graph.workflow import build_workflow
from app.schemas.script_schema import MotivationalScript, ScriptRequest
from app.video.image_generator import build_image_provider, generate_scene_images
from app.video.review_gates import video_sha256
from app.youtube.manual_upload import export_upload_package

logger = logging.getLogger(__name__)


class DailyPipelineError(RuntimeError):
    def __init__(self, step: str, message: str, *, script_id: int | None = None,
                 production_id: str | None = None):
        super().__init__(message)
        self.step, self.script_id, self.production_id = step, script_id, production_id


def run_daily_pipeline(database_path: str | Path | None = None) -> dict[str, Any]:
    """Run existing generation, image, render, approval and export flows."""
    from app.database.db import initialize_database
    db_path = Path(database_path) if database_path else get_database_path()
    initialize_database(db_path)
    settings = get_daily_pipeline_settings()
    niche = str(settings["niche"])
    topic = niche or get_topic()
    workflow = build_workflow(database_path=str(db_path), quality_threshold=get_quality_threshold())
    record = None
    score100 = None
    for attempt in range(int(settings["max_script_retries"]) + 1):
        candidate = topic if attempt == 0 else f"{topic}: fresh angle {attempt + 1}"
        try:
            state = workflow.invoke({
                "script_request": ScriptRequest(topic=candidate), "errors": [],
                "revision_attempts": 0, "require_human_review": True,
            })
        except Exception as error:
            raise DailyPipelineError("script generation", str(error)) from error
        record = state.get("script_record")
        if not record:
            details = "; ".join(str(item) for item in state.get("errors", []))
            limitation = state.get("provider_limitation")
            reason = details or str(limitation) or "Draft flow did not save a script record."
            raise DailyPipelineError("script generation", reason)
        score100 = float(record.get("quality_score", -1)) * 10
        evaluation_passed = bool((record.get("evaluation") or {}).get("passed"))
        if score100 > float(settings["min_script_quality_score"]) and evaluation_passed:
            break
        logger.warning("Daily pipeline script rejected id=%s score=%s evaluation_passed=%s attempt=%s", record["id"], score100, evaluation_passed, attempt + 1)
        _discard_failed_draft(record["id"], state.get("script_json_path"), db_path)
        record = None
    if record is None:
        raise DailyPipelineError("script quality", f"All {int(settings['max_script_retries']) + 1} script attempts failed strict quality threshold (> {settings['min_script_quality_score']}).")
    script_id = int(record["id"])
    if not settings["auto_approve_scripts"]:
        raise DailyPipelineError("script approval", f"Manual approval required for script {script_id}.", script_id=script_id)
    try:
        approved = review_script(script_id, "approved", "auto_pipeline", db_path)
    except Exception as error:
        raise DailyPipelineError("script approval", f"Fail-closed script approval: {error}", script_id=script_id) from error
    logger.info("Synthetic script approval timestamp=%s script_id=%s score=%s/100 reviewer=auto_pipeline", approved["reviewed_at"], script_id, score100)

    parsed_script = MotivationalScript.model_validate(approved["script"])
    script = parsed_script.model_dump(mode="json")
    library = get_licensed_asset_path()
    try:
        image_provider = build_image_provider()
        generate_scene_images(parsed_script.scenes, library_path=library, provider=image_provider)
    except Exception as first_error:
        try:
            # Re-run existing scene generation flow once; successful scenes are reused.
            generate_scene_images(parsed_script.scenes, library_path=library, provider=build_image_provider())
        except Exception as retry_error:
            raise DailyPipelineError("scene images", f"Image generation failed twice: {retry_error}", script_id=script_id) from first_error

    state = workflow.invoke({"production_script_id": script_id, "errors": []})
    production = state.get("production_result")
    if not production:
        errors = "; ".join(state.get("errors", [])) or "Production workflow returned no result."
        raise DailyPipelineError("video production", errors, script_id=script_id)
    production_id = str(production["id"])
    if production.get("status") not in {"completed", "ready_for_manual_upload"}:
        raise DailyPipelineError("video production / QA", production.get("error") or f"Production status is {production.get('status')}.", script_id=script_id, production_id=production_id)
    production = get_production_by_id(production_id, db_path)
    metadata = production.get("metadata", {})
    audio_qa, video_qa = metadata.get("audio_qa"), metadata.get("video_qa")
    if not isinstance(audio_qa, dict) or audio_qa.get("technical_status") != "passed":
        raise DailyPipelineError("audio QA", "Audio QA did not explicitly pass.", script_id=script_id, production_id=production_id)
    if not isinstance(video_qa, dict) or video_qa.get("technical_status") != "passed":
        raise DailyPipelineError("video QA", "Video QA did not explicitly pass.", script_id=script_id, production_id=production_id)
    provenance = _validate_provenance(library, script)
    if provenance:
        raise DailyPipelineError("LICENSES.json provenance", provenance, script_id=script_id, production_id=production_id)
    video_path = Path(metadata.get("video", {}).get("path", ""))
    if not video_path.is_file():
        raise DailyPipelineError("production hash", "Final video is unavailable for hash verification.", script_id=script_id, production_id=production_id)
    digest = video_sha256(video_path)
    if not settings["auto_approve_productions"]:
        raise DailyPipelineError("production approval", f"Manual approval required for production {production_id}.", script_id=script_id, production_id=production_id)
    timestamp = datetime.now(timezone.utc).isoformat()
    decision = {"decision": "approve", "reviewer": "auto_pipeline", "confirmed": True,
                "reviewed_at": timestamp, "video_sha256": digest,
                "policy": "daily pipeline QA, provenance, and integrity gates",
                "underlying_scores": {"script_quality_score": score100,
                                      "audio_quality_score": audio_qa.get("quality_score"),
                                      "video_quality_score": video_qa.get("quality_score")}}
    metadata["review_decision"] = decision
    metadata["production_sha256"] = digest
    metadata["auto_approval"] = {"status": "approved", "reviewer": "auto_pipeline", "reviewed_at": timestamp}
    try:
        _write_metadata(Path(production["output_dir"]), production_id, metadata)
        production = update_production(production_id, "ready_for_manual_upload", metadata, database_path=db_path)
    except Exception as error:
        raise DailyPipelineError("production approval audit", f"Could not persist approval: {error}", script_id=script_id, production_id=production_id) from error
    logger.info("Synthetic production approval timestamp=%s production_id=%s script=%s audio=%s video=%s reviewer=auto_pipeline hash=%s", timestamp, production_id, score100, audio_qa.get("quality_score"), video_qa.get("quality_score"), digest)
    try:
        package = export_upload_package(production_id, db_path)
    except Exception as error:
        raise DailyPipelineError("upload package export", str(error), script_id=script_id, production_id=production_id) from error
    video_meta = metadata.get("video", {})
    title = (metadata.get("youtube_metadata") or {}).get("title") or script.get("title_candidates", [""])[0]
    return {"script_id": script_id, "title": title, "duration": script.get("target_duration_seconds"),
            "production_id": production_id, "package_path": str(package.resolve()),
            "video_path": str(video_path.resolve()), "status": production["status"]}


def _validate_provenance(library: Path, script: dict[str, Any]) -> str | None:
    manifest_path = library / "LICENSES.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return f"Missing or unreadable {manifest_path}."
    if not isinstance(manifest, dict):
        return "LICENSES.json must be an object."
    for scene in script.get("scenes", []):
        prefix = f"scene_{int(scene['scene_number']):02d}."
        assets = [path for path in library.glob(prefix + "*") if path.is_file()]
        if not assets:
            return f"No image asset for {prefix[:-1]} in licensed library."
        if not any(all(isinstance(manifest.get(path.name), dict) and
                       isinstance(manifest[path.name].get(key), str) and manifest[path.name][key].strip()
                       for key in ("source", "license", "rights_holder")) for path in assets):
            return f"Missing complete source/license/rights_holder provenance for {prefix[:-1]}."
    return None


def _discard_failed_draft(script_id: int, json_path: str | None, database_path: Path) -> None:
    """Keep exhausted low-quality attempts from leaving persisted partial drafts."""
    delete_unreviewed_script(int(script_id), database_path)
    if json_path:
        Path(json_path).unlink(missing_ok=True)


def _write_metadata(output_dir: Path, production_id: str, metadata: dict[str, Any]) -> None:
    artifact = output_dir / "metadata.json"
    data = json.loads(artifact.read_text(encoding="utf-8"))
    if data.get("production_id") != production_id:
        raise ValueError("Production metadata artifact does not match.")
    data["status"], data["metadata"] = "ready_for_manual_upload", metadata
    temporary = artifact.with_suffix(".pipeline.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(artifact)
