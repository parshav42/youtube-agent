"""Fail-closed orchestration and approval gates for scheduled YouTube Shorts."""

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import (
    get_auto_publish_settings,
    get_database_path,
    get_licensed_asset_path,
    get_niche,
)
from app.database.db import (
    get_production_by_id,
    get_script_by_id,
    get_scripts_needing_review,
    review_script,
    update_production,
)
from app.graph.workflow import build_workflow
from app.video.production_service import ProductionService
from app.video.review_gates import video_sha256
from app.youtube.publisher import YouTubePublisher
from app.youtube.repository import (
    QuotaBudgetExceeded,
    finish_quota_reservation,
    get_approval,
    get_auto_approval_candidates,
    get_approved_scripts_without_productions,
    get_quota_usage,
    get_upload_candidates,
    reserve_upload_quota,
    set_upload_retry_after,
)

logger = logging.getLogger(__name__)


_POLICY_CHECKS = (
    "language_consistency",
    "story_coherence",
    "originality_safeguards",
    "duration_feasibility",
    "repetition",
    "motivational_message",
    "scene_completeness",
    "caption_scene_alignment",
    "structural_labels_absent",
    "unsupported_factual_assertions",
    "unsupported_guarantees",
)


def evaluate_auto_approval_gates(
    script_record: dict[str, Any],
    production: dict[str, Any],
    *,
    licensed_asset_directory: str | Path,
    min_script_quality_score: float,
    min_audio_quality_score: float,
    min_video_quality_score: float,
) -> dict[str, Any]:
    """Return all failed script, QA, provenance, and integrity gates without mutation."""
    reasons: list[str] = []
    evaluation = script_record.get("evaluation")
    script_score = _score(script_record.get("quality_score"))
    if script_score is None and isinstance(evaluation, dict):
        script_score = _score(evaluation.get("quality_score"))
    if script_score is None or script_score < min_script_quality_score:
        reasons.append("script_quality_below_threshold")

    if not isinstance(evaluation, dict) or evaluation.get("passed") is not True:
        reasons.append("script_evaluation_failed_or_missing")
    elif any(evaluation.get(check) is not True for check in _POLICY_CHECKS):
        reasons.append("script_policy_or_quality_flags")

    script_payload = script_record.get("script")
    if not isinstance(script_payload, dict):
        reasons.append("script_content_missing")
    if any(
        _has_flags(source)
        for source in (
            script_record.get("policy_flags"),
            script_record.get("safety_flags"),
            evaluation.get("policy_flags") if isinstance(evaluation, dict) else None,
            script_payload.get("policy_flags") if isinstance(script_payload, dict) else None,
            script_payload.get("safety_flags") if isinstance(script_payload, dict) else None,
        )
    ):
        reasons.append("script_policy_or_safety_flags")

    metadata = production.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    if not _qa_passes(metadata.get("audio_qa"), min_audio_quality_score):
        reasons.append("audio_qa_failed_or_below_threshold")
    if not _qa_passes(metadata.get("video_qa"), min_video_quality_score):
        reasons.append("video_qa_failed_or_below_threshold")
    if not _assets_have_provenance(
        metadata.get("visual_assets"), licensed_asset_directory
    ):
        reasons.append("scene_asset_provenance_incomplete")

    digest = _production_digest(production)
    if digest is None:
        reasons.append("production_hash_unavailable")
    else:
        recorded_digest = metadata.get("production_sha256")
        decision = metadata.get("review_decision")
        if not recorded_digest and isinstance(decision, dict):
            recorded_digest = decision.get("video_sha256")
        if recorded_digest != digest:
            reasons.append("production_hash_not_recorded_or_mismatched")
    return {"eligible": not reasons, "reasons": reasons, "production_sha256": digest}


def auto_approval_failures(
    script: dict[str, Any],
    production: dict[str, Any],
    licensed_asset_directory: str | Path,
    settings: dict[str, Any],
) -> list[str]:
    """Return readable reasons for failed automatic approval gates."""
    result = evaluate_auto_approval_gates(
        script,
        production,
        licensed_asset_directory=licensed_asset_directory,
        min_script_quality_score=settings["min_script_quality_score"],
        min_audio_quality_score=settings["min_audio_quality_score"],
        min_video_quality_score=settings["min_video_quality_score"],
    )
    labels = {
        "script_quality_below_threshold": "script quality score is below MIN_SCRIPT_QUALITY_SCORE",
        "script_evaluation_failed_or_missing": "script quality evaluation did not pass",
        "script_policy_or_quality_flags": "script policy or safety checks did not all pass",
        "script_content_missing": "script content is missing",
        "script_policy_or_safety_flags": "script has policy or safety flags",
        "audio_qa_failed_or_below_threshold": "audio QA did not pass or is below MIN_AUDIO_QUALITY_SCORE",
        "video_qa_failed_or_below_threshold": "video QA did not pass or is below MIN_VIDEO_QUALITY_SCORE",
        "scene_asset_provenance_incomplete": "scene assets lack complete LICENSES.json provenance",
        "production_hash_unavailable": "production video hash cannot be computed",
        "production_hash_not_recorded_or_mismatched": "recorded production hash is missing or mismatched",
    }
    return [labels[reason] for reason in result["reasons"]]


def _score(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _qa_passes(report: Any, threshold: float) -> bool:
    if not isinstance(report, dict) or report.get("technical_status") != "passed":
        return False
    score = _score(report.get("quality_score"))
    return (score if score is not None else 10.0) >= threshold


def _has_flags(flags: Any) -> bool:
    if isinstance(flags, str):
        return bool(flags.strip())
    if isinstance(flags, dict):
        return any(bool(value) for value in flags.values())
    return isinstance(flags, (list, tuple, set)) and bool(flags)


def _assets_have_provenance(assets: Any, library_path: str | Path) -> bool:
    if not isinstance(assets, list) or not assets:
        return False
    try:
        manifest = json.loads(
            (Path(library_path).expanduser() / "LICENSES.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(manifest, dict):
        return False
    for asset in assets:
        if not isinstance(asset, dict):
            return False
        path = Path(str(asset.get("path", "")))
        provenance = manifest.get(path.name)
        if not path.is_file() or not isinstance(provenance, dict):
            return False
        for field in ("source", "license", "rights_holder"):
            value = asset.get(field)
            declared = provenance.get(field)
            if (
                not isinstance(value, str)
                or not value.strip()
                or not isinstance(declared, str)
                or not declared.strip()
                or value.strip() != declared.strip()
            ):
                return False
        expected_hash = asset.get("sha256")
        if expected_hash:
            try:
                if _file_sha256(path) != expected_hash:
                    return False
            except OSError:
                return False
    return True


def _production_digest(production: dict[str, Any]) -> str | None:
    metadata = production.get("metadata")
    video = metadata.get("video") if isinstance(metadata, dict) else None
    path_value = video.get("path") if isinstance(video, dict) else None
    if not isinstance(path_value, str) or not path_value:
        return None
    path = Path(path_value)
    try:
        return _file_sha256(path) if path.is_file() else None
    except OSError:
        return None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as content:
        for chunk in iter(lambda: content.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class AutoPublishService:
    """Run one opt-in research-to-public-upload cycle using durable state."""

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
            "public_video_urls": [],
            "failures": [],
            "waiting_for_review": 0,
        }
        processed_productions: set[str] = set()
        try:
            self._create_researched_script(result, settings)
        except Exception as error:
            result["failures"].append(_safe_failure(error))

        if settings["auto_approve_productions"]:
            for script in get_approved_scripts_without_productions(self.database_path, limit=5):
                try:
                    production = self.production_service.produce_script_record(script["id"])
                    production_id = production["id"]
                    processed_productions.add(production_id)
                    result["productions"].append(production_id)
                    reasons = self._auto_review_production(production_id, script, settings)
                    if reasons:
                        result["failures"].append(
                            {"production_id": production_id, "reason": "; ".join(reasons)}
                        )
                except Exception as error:
                    result["failures"].append(_safe_failure(error))
            for candidate in get_auto_approval_candidates(self.database_path, limit=20):
                production_id = candidate["production_id"]
                if production_id in processed_productions:
                    continue
                try:
                    self.publisher.approve_automatically(production_id)
                except Exception as error:
                    result["failures"].append(
                        {"production_id": production_id, "reason": _safe_failure(error)}
                    )

        for candidate in get_upload_candidates(self.database_path, limit=100):
            production_id = candidate["production_id"]
            approval = get_approval(production_id, self.database_path)
            if approval is None or approval["visibility"] != "public":
                continue
            retry_after = candidate.get("retry_after")
            if retry_after:
                try:
                    if datetime.fromisoformat(retry_after) > datetime.now(timezone.utc):
                        continue
                except ValueError:
                    result["failures"].append(
                        {"production_id": production_id, "reason": "saved retry timestamp is invalid"}
                    )
                    continue
            if candidate["status"] in {"failed", "uploading"} and not candidate.get("resumable_uri"):
                self._reconcile_without_session(production_id, result)
                continue
            channel_id = candidate.get("channel_id") or approval.get("approved_channel_id")
            if not channel_id:
                result["failures"].append(
                    {"production_id": production_id, "reason": "approved YouTube channel ID is missing"}
                )
                continue
            try:
                reserve_upload_quota(
                    production_id,
                    channel_id,
                    max_uploads=int(settings["max_uploads_per_day"]),
                    quota_units=int(settings["insert_quota_units"]),
                    daily_quota_units=int(settings["daily_quota_units"]),
                    database_path=self.database_path,
                )
            except QuotaBudgetExceeded as error:
                result["failures"].append({"production_id": production_id, "reason": str(error)})
                break
            result["uploads_attempted"] += 1
            try:
                uploaded = self.publisher.upload(production_id)
                finish_quota_reservation(
                    production_id, channel_id, "succeeded", self.database_path
                )
                result["uploads_succeeded"] += 1
                video_id = uploaded.get("video_id")
                if video_id:
                    result["public_video_urls"].append(
                        uploaded.get("video_url") or f"https://www.youtube.com/watch?v={video_id}"
                    )
            except Exception as error:
                finish_quota_reservation(
                    production_id, channel_id, "failed", self.database_path
                )
                retry_after = _retry_time(candidate.get("attempt_count", 0), error)
                set_upload_retry_after(production_id, retry_after, self.database_path)
                result["failures"].append(
                    {
                        "production_id": production_id,
                        "reason": _safe_failure(error),
                        "retry_after": retry_after,
                    }
                )
        result["waiting_for_review"] = self._waiting_review_count()
        result["quota_usage"] = get_quota_usage(database_path=self.database_path)
        return result

    def _create_researched_script(
        self, result: dict[str, Any], settings: dict[str, Any]
    ) -> None:
        from app.agents.research_agent import HuggingFaceResearchProvider
        from app.providers.llm_factory import build_llm_provider

        llm = build_llm_provider()
        research = HuggingFaceResearchProvider(llm)
        research_workflow = build_workflow(
            research_provider=research,
            database_path=str(self.database_path),
        )
        researched = research_workflow.invoke({"niche": get_niche(), "errors": []})
        topics = researched.get("selected_topics", [])
        if not topics:
            raise RuntimeError("Live research returned no eligible topic.")
        generated = build_workflow(database_path=str(self.database_path)).invoke(
            {
                "script_request": {"topic": topics[0].topic},
                "errors": [],
                "revision_attempts": 0,
            }
        )
        record = generated.get("script_record")
        if not record:
            raise RuntimeError("Script generation did not produce a persisted record.")
        result["draft"] = record["id"]
        if not settings["auto_approve_scripts"]:
            return
        latest = get_script_by_id(record["id"], self.database_path)
        if latest is None:
            raise RuntimeError("Generated script record could not be reloaded.")
        reasons = _script_gate_failures(latest, settings)
        if reasons:
            logger.warning(
                "Automatic script approval blocked for %s: %s",
                record["id"],
                "; ".join(reasons),
            )
            result["failures"].append(
                {"script_id": record["id"], "reason": "; ".join(reasons)}
            )
            return
        review_script(
            record["id"],
            "approved",
            reviewer="auto-publish-policy",
            database_path=self.database_path,
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
        raw_metadata = production.get("metadata")
        metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
        existing_decision = metadata.get("review_decision")
        if isinstance(existing_decision, dict) and existing_decision.get("reviewer") != "auto-publish-policy":
            raise RuntimeError("Production already has a human review decision.")
        if production["status"] not in {"completed", "ready_for_manual_upload"}:
            reasons = [f"production status is {production['status']!r}, not ready for upload"]
            return self._mark_needs_review(production, metadata, reasons)

        video_metadata = metadata.get("video")
        video_path = video_metadata.get("path") if isinstance(video_metadata, dict) else None
        try:
            current_hash = video_sha256(video_path) if isinstance(video_path, str) else ""
        except (OSError, TypeError, ValueError):
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
        reasons = auto_approval_failures(
            script, gate_production, get_licensed_asset_path(), settings
        )
        if reasons:
            metadata.pop("review_decision", None)
            return self._mark_needs_review(production, metadata, reasons)

        if not existing_decision:
            metadata["review_decision"] = {
                "decision": "approve",
                "reviewer": "auto-publish-policy",
                "confirmed": True,
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
                "video_sha256": current_hash,
                "policy": "configured quality, safety, provenance, and integrity gates",
            }
        metadata["production_sha256"] = current_hash
        metadata["auto_approval"] = {"status": "approved", "reasons": []}
        artifact_path = Path(production["output_dir"]) / "metadata.json"
        try:
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            if not isinstance(artifact, dict) or artifact.get("production_id") != production_id:
                raise ValueError("Production metadata artifact does not match this production.")
            artifact["status"] = "completed"
            artifact["metadata"] = metadata
            temporary_path = artifact_path.with_suffix(".auto.tmp")
            temporary_path.write_text(
                json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            temporary_path.replace(artifact_path)
        except (OSError, json.JSONDecodeError, ValueError):
            return self._mark_needs_review(
                production,
                metadata,
                ["completed production metadata artifact is missing or invalid"],
            )
        update_production(
            production_id,
            "ready_for_manual_upload",
            metadata,
            database_path=self.database_path,
        )
        approval = self.publisher.approve_automatically(production_id)
        if approval["visibility"] != "public":
            raise RuntimeError("Automatic publishing requires public visibility.")
        return []

    def _mark_needs_review(
        self,
        production: dict[str, Any],
        metadata: dict[str, Any],
        reasons: list[str],
    ) -> list[str]:
        production_id = production["id"]
        metadata.pop("review_decision", None)
        metadata["auto_approval"] = {"status": "needs_review", "reasons": reasons}
        logger.warning(
            "Automatic approval blocked for %s: %s", production_id, "; ".join(reasons)
        )
        update_production(
            production_id,
            "needs_review",
            metadata,
            error=production.get("error"),
            database_path=self.database_path,
        )
        return reasons

    def _reconcile_without_session(
        self, production_id: str, result: dict[str, Any]
    ) -> None:
        try:
            reconciled = self.publisher.reconcile(production_id)
            if reconciled.get("reconciled"):
                result["uploads_succeeded"] += 1
                video_id = reconciled.get("video_id")
                if video_id:
                    result["public_video_urls"].append(
                        f"https://www.youtube.com/watch?v={video_id}"
                    )
            else:
                result["failures"].append(
                    {
                        "production_id": production_id,
                        "reason": reconciled.get(
                            "detail", "Upload outcome remains ambiguous; retry blocked."
                        ),
                    }
                )
        except Exception as error:
            result["failures"].append(
                {"production_id": production_id, "reason": _safe_failure(error)}
            )

    def _waiting_review_count(self) -> int:
        return len(get_scripts_needing_review(self.database_path, limit=1000))


def _script_gate_failures(
    script: dict[str, Any], settings: dict[str, Any]
) -> list[str]:
    failures: list[str] = []
    evaluation = script.get("evaluation")
    if not isinstance(evaluation, dict) or evaluation.get("passed") is not True:
        failures.append("script quality evaluation did not pass")
        return failures
    score = _score(evaluation.get("quality_score"))
    if score is None or score < settings["min_script_quality_score"]:
        failures.append("script quality score is below MIN_SCRIPT_QUALITY_SCORE")
    checks = [evaluation.get(key) for key in _POLICY_CHECKS]
    if any(check is not True for check in checks):
        failures.append("script policy or safety checks did not all pass")
    payload = script.get("script")
    if not isinstance(payload, dict):
        failures.append("script content is missing")
    elif _has_flags(payload.get("policy_flags")) or _has_flags(payload.get("safety_flags")):
        failures.append("script has policy or safety flags")
    return failures


def _retry_time(attempt_count: int, error: Exception) -> str:
    now = datetime.now(timezone.utc)
    message = str(error).casefold()
    if "quota" in message or "rate limit" in message:
        pacific_now = now.astimezone(ZoneInfo("America/Los_Angeles"))
        reset = datetime.combine(
            pacific_now.date() + timedelta(days=1),
            datetime.min.time(),
            ZoneInfo("America/Los_Angeles"),
        )
        return reset.astimezone(timezone.utc).isoformat()
    delay = min(3600, 60 * (2 ** min(max(attempt_count, 0), 6)))
    return (now + timedelta(seconds=delay)).isoformat()


def _safe_failure(error: Exception) -> str:
    message = f"{type(error).__name__}: {error}"
    for key in (
        "HF_TOKEN",
        "ELEVENLABS_API_KEY",
        "ADMIN_PASSWORD",
        "MOTIVATIONOS_ADMIN_PASSWORD",
        "ADMIN_TOKEN",
        "MOTIVATIONOS_ADMIN_TOKEN",
        "GOOGLE_CLIENT_SECRET",
    ):
        secret = os.getenv(key, "")
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return message[:500]
