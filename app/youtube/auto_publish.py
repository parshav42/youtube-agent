"""Fail-closed orchestration for scheduled Shorts production and publication."""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import get_auto_publish_settings, get_database_path, get_niche
from app.database.db import get_production_by_id, get_script_by_id, review_script, update_production
from app.graph.workflow import build_workflow
from app.video.production_service import ProductionService
from app.video.review_gates import video_sha256
from app.youtube.publisher import YouTubePublisher
from app.youtube.repository import (
    QuotaBudgetExceeded,
    finish_quota_reservation,
    get_approval,
    get_approved_scripts_without_productions,
    get_quota_usage,
    get_upload,
    get_upload_candidates,
    reserve_upload_quota,
    set_upload_retry_after,
)


def auto_approval_failures(
    script: dict[str, Any],
    production: dict[str, Any],
    licensed_asset_directory: str | Path,
    settings: dict[str, Any],
) -> list[str]:
    """Return every failed policy, quality, provenance, and integrity gate."""
    failures: list[str] = []
    evaluation = script.get("evaluation")
    if not isinstance(evaluation, dict):
        failures.append("script quality evaluation is missing")
    else:
        if not evaluation.get("passed"):
            failures.append("script quality evaluation did not pass")
        if float(evaluation.get("quality_score", -1)) < settings["min_script_quality_score"]:
            failures.append("script quality score is below MIN_SCRIPT_QUALITY_SCORE")
        checks = {
            key: value
            for key, value in evaluation.items()
            if isinstance(value, bool) and key not in {"passed"}
        }
        if not checks or not all(checks.values()):
            failures.append("script policy or safety checks did not all pass")
    script_payload = script.get("script")
    if isinstance(script_payload, dict):
        flags = script_payload.get("policy_flags", []) + script_payload.get("safety_flags", [])
        if flags:
            failures.append("script has policy or safety flags")
    else:
        failures.append("script content is missing")

    metadata = production.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    audio_qa = metadata.get("audio_qa")
    video_qa = metadata.get("video_qa")
    if not isinstance(audio_qa, dict) or audio_qa.get("technical_status") != "passed":
        failures.append("audio QA did not pass")
    elif _qa_score(audio_qa) < settings["min_audio_quality_score"]:
        failures.append("audio quality score is below MIN_AUDIO_QUALITY_SCORE")
    if not isinstance(video_qa, dict) or video_qa.get("technical_status") != "passed":
        failures.append("video QA did not pass")
    elif _qa_score(video_qa) < settings["min_video_quality_score"]:
        failures.append("video quality score is below MIN_VIDEO_QUALITY_SCORE")

    if not _licenses_are_complete(metadata.get("visual_assets"), licensed_asset_directory):
        failures.append("one or more scene assets lack complete LICENSES.json provenance")
    video = metadata.get("video")
    video_path = video.get("path") if isinstance(video, dict) else None
    try:
        current_hash = video_sha256(video_path) if video_path else None
    except OSError:
        current_hash = None
    recorded_hash = (metadata.get("review_decision") or {}).get("video_sha256")
    if not current_hash:
        failures.append("production video hash cannot be computed")
    elif not recorded_hash:
        failures.append("production hash has not been recorded")
    elif recorded_hash != current_hash:
        failures.append("recorded production hash does not match the final video")
    return failures


def _qa_score(report: dict[str, Any]) -> float:
    supplied = report.get("quality_score")
    if isinstance(supplied, (int, float)) and not isinstance(supplied, bool):
        return float(supplied)
    return 10.0 if report.get("technical_status") == "passed" else 0.0


def _licenses_are_complete(assets: Any, library_path: str | Path) -> bool:
    if not isinstance(assets, list) or not assets:
        return False
    manifest_path = Path(library_path) / "LICENSES.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(manifest, dict):
        return False
    for asset in assets:
        if not isinstance(asset, dict):
            return False
        name = Path(str(asset.get("path", ""))).name
        provenance = manifest.get(name)
        if not isinstance(provenance, dict):
            return False
        for field in ("source", "license", "rights_holder"):
            if not isinstance(provenance.get(field), str) or not provenance[field].strip():
                return False
            if asset.get(field) != provenance[field]:
                return False
    return True


class AutoPublishService:
    """Run one idempotent automatic research-to-upload cycle."""

    def __init__(
        self,
        database_path: str | Path | None = None,
        *,
        publisher: YouTubePublisher | None = None,
        production_service: ProductionService | None = None,
    ) -> None:
        self.database_path = Path(database_path) if database_path else get_database_path()
        self.publisher = publisher or YouTubePublisher(self.database_path)
        self.production_service = production_service or ProductionService(self.database_path)

    def run(self) -> dict[str, Any]:
        settings = get_auto_publish_settings()
        if not settings["enabled"]:
            return {"status": "disabled", "uploads_attempted": 0, "uploads_succeeded": 0}
        result: dict[str, Any] = {
            "status": "completed",
            "draft": None,
            "productions": [],
            "uploads_attempted": 0,
            "uploads_succeeded": 0,
            "failures": [],
            "waiting_for_review": 0,
        }
        try:
            self._create_researched_script(result, settings)
        except Exception as error:
            result["failures"].append(_safe_failure(error))

        if settings["auto_approve_productions"]:
            for script in get_approved_scripts_without_productions(self.database_path, limit=5):
                try:
                    production = self.production_service.produce_script_record(script["id"])
                    result["productions"].append(production["id"])
                    failures = self._auto_review_production(production["id"], script, settings)
                    if failures:
                        result["failures"].append({"production_id": production["id"], "reason": "; ".join(failures)})
                except Exception as error:
                    result["failures"].append(_safe_failure(error))

        for candidate in get_upload_candidates(self.database_path, limit=100):
            approval = get_approval(candidate["production_id"], self.database_path)
            if approval is None or approval["visibility"] != "public":
                continue
            retry_after = candidate.get("retry_after")
            if retry_after and datetime.fromisoformat(retry_after) > datetime.now(timezone.utc):
                continue
            if candidate["status"] == "failed" and not candidate.get("resumable_uri"):
                try:
                    reconciled = self.publisher.reconcile(candidate["production_id"])
                    if reconciled.get("reconciled"):
                        result["uploads_succeeded"] += 1
                    else:
                        result["failures"].append({
                            "production_id": candidate["production_id"],
                            "reason": reconciled.get("detail", "Upload outcome remains ambiguous; retry blocked."),
                        })
                except Exception as error:
                    result["failures"].append({"production_id": candidate["production_id"], "reason": _safe_failure(error)})
                continue
            channel_id = candidate.get("channel_id") or approval.get("approved_channel_id")
            if not channel_id:
                result["failures"].append({
                    "production_id": candidate["production_id"],
                    "reason": "approved YouTube channel ID is missing",
                })
                continue
            try:
                reserve_upload_quota(
                    candidate["production_id"], channel_id,
                    max_uploads=settings["max_uploads_per_day"],
                    quota_units=settings["insert_quota_units"],
                    daily_quota_units=settings["daily_quota_units"],
                    database_path=self.database_path,
                )
            except QuotaBudgetExceeded as error:
                result["failures"].append({"production_id": candidate["production_id"], "reason": str(error)})
                break
            result["uploads_attempted"] += 1
            try:
                uploaded = self.publisher.upload(candidate["production_id"])
                finish_quota_reservation(candidate["production_id"], channel_id, "succeeded", self.database_path)
                result["uploads_succeeded"] += 1
                result.setdefault("public_video_urls", []).append(uploaded.get("video_url") or f"https://www.youtube.com/watch?v={uploaded['video_id']}")
            except Exception as error:
                finish_quota_reservation(candidate["production_id"], channel_id, "failed", self.database_path)
                retry_after = _retry_time(candidate.get("attempt_count", 0), error)
                set_upload_retry_after(candidate["production_id"], retry_after, self.database_path)
                result["failures"].append({
                    "production_id": candidate["production_id"],
                    "reason": _safe_failure(error),
                    "retry_after": retry_after,
                })
        result["waiting_for_review"] = self._waiting_review_count()
        result["quota_usage"] = get_quota_usage(database_path=self.database_path)
        return result

    def _create_researched_script(self, result: dict[str, Any], settings: dict[str, Any]) -> None:
        from app.agents.research_agent import HuggingFaceResearchProvider
        from app.providers.huggingface_llm import HuggingFaceLLMProvider

        llm = HuggingFaceLLMProvider.from_environment()
        research = HuggingFaceResearchProvider(llm)
        research_workflow = build_workflow(
            research_provider=research,
            database_path=str(self.database_path),
        )
        researched = research_workflow.invoke({"niche": get_niche(), "errors": []})
        topics = researched.get("selected_topics", [])
        if not topics:
            raise RuntimeError("Live research returned no eligible topic.")
        topic = topics[0].topic
        script_workflow = build_workflow(database_path=str(self.database_path))
        generated = script_workflow.invoke({
            "script_request": {"topic": topic},
            "errors": [],
            "revision_attempts": 0,
        })
        record = generated.get("script_record")
        if not record:
            raise RuntimeError("Script generation did not produce a persisted record.")
        result["draft"] = record["id"]
        if not settings["auto_approve_scripts"]:
            return
        latest = get_script_by_id(record["id"], self.database_path)
        if not latest:
            raise RuntimeError("Generated script record could not be reloaded.")
        failures = _script_gate_failures(latest, settings)
        if failures:
            result["failures"].append({"script_id": record["id"], "reason": "; ".join(failures)})
            return
        review_script(
            record["id"], "approved", reviewer="auto-publish-policy", database_path=self.database_path
        )

    def _auto_review_production(
        self,
        production_id: str,
        script: dict[str, Any],
        settings: dict[str, Any],
    ) -> list[str]:
        production = get_production_by_id(production_id, self.database_path)
        if production is None:
            raise RuntimeError("Production record is missing.")
        metadata = dict(production["metadata"])
        existing_decision = metadata.get("review_decision")
        if existing_decision and existing_decision.get("reviewer") != "auto-publish-policy":
            raise RuntimeError("Production already has a human review decision.")
        if production["status"] != "completed":
            reasons = [f"production status is {production['status']!r}, not completed"]
            metadata["auto_approval"] = {"status": "needs_review", "reasons": reasons}
            update_production(
                production_id,
                production["status"],
                metadata,
                error=production.get("error"),
                database_path=self.database_path,
            )
            return reasons
        video_path = Path(metadata.get("video", {}).get("path", ""))
        try:
            current_hash = video_sha256(video_path)
        except OSError:
            current_hash = ""
        if current_hash and not existing_decision:
            metadata["review_decision"] = {
                "decision": "approve",
                "reviewer": "auto-publish-policy",
                "confirmed": True,
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
                "video_sha256": current_hash,
                "policy": "configured quality, safety, provenance, and integrity gates",
            }
        gate_production = dict(production)
        gate_production["metadata"] = metadata
        failures = auto_approval_failures(
            script, gate_production, os.getenv("MOTIVATIONOS_LICENSED_ASSET_DIR", "data/licensed-assets"), settings
        )
        if failures:
            metadata.pop("review_decision", None)
            metadata["auto_approval"] = {"status": "needs_review", "reasons": failures}
            update_production(production_id, "needs_review", metadata, database_path=self.database_path)
            return failures
        if not existing_decision:
            metadata["review_decision"] = {
                "decision": "approve",
                "reviewer": "auto-publish-policy",
                "confirmed": True,
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
                "video_sha256": video_sha256(video_path),
                "policy": "configured quality, safety, provenance, and integrity gates",
            }
        metadata["auto_approval"] = {"status": "approved", "reasons": []}
        update_production(production_id, "completed", metadata, database_path=self.database_path)
        artifact_path = Path(production["output_dir"]) / "metadata.json"
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact["metadata"] = metadata
        temporary_path = artifact_path.with_suffix(".auto.tmp")
        temporary_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary_path.replace(artifact_path)
        approval = self.publisher.approve_automatically(production_id)
        if approval["visibility"] != "public":
            raise RuntimeError("Automatic publishing requires public visibility.")
        return []

    def _waiting_review_count(self) -> int:
        from app.database.db import get_scripts_needing_review

        return len(get_scripts_needing_review(self.database_path, limit=1000))


def _script_gate_failures(script: dict[str, Any], settings: dict[str, Any]) -> list[str]:
    evaluation = script.get("evaluation")
    reasons = []
    if not isinstance(evaluation, dict) or not evaluation.get("passed"):
        reasons.append("script quality evaluation did not pass")
    if not isinstance(evaluation, dict) or evaluation.get("quality_score", -1) < settings["min_script_quality_score"]:
        reasons.append("script quality score is below MIN_SCRIPT_QUALITY_SCORE")
    if isinstance(evaluation, dict) and not all(
        value for key, value in evaluation.items()
        if isinstance(value, bool) and key != "passed"
    ):
        reasons.append("script policy or safety checks did not all pass")
    return reasons


def _retry_time(attempt_count: int, error: Exception) -> str:
    now = datetime.now(timezone.utc)
    if "quota" in str(error).lower() or "rate limit" in str(error).lower():
        tomorrow = now.date() + timedelta(days=1)
        return datetime.combine(tomorrow, datetime.min.time(), timezone.utc).isoformat()
    delay = min(3600, 60 * (2 ** min(max(attempt_count, 0), 6)))
    return (now + timedelta(seconds=delay)).isoformat()


def _safe_failure(error: Exception) -> str:
    return f"{type(error).__name__}: {str(error)[:500]}"