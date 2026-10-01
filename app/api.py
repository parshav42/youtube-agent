"""Local, access-controlled HTTP API for YouTube operations."""

import hmac
import json
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import get_database_path
from app.database.db import (
    ScriptNotFoundError,
    ScriptReviewError,
    get_script_by_id,
    get_production_by_id,
    get_scripts_needing_review,
    review_script,
    update_production,
)
from app.reporting.daily_report import DailyReportService
from app.reporting.scheduler import build_scheduler, run_scheduled_report
from app.youtube.analytics import YouTubeAnalyticsError, YouTubeAnalyticsService
from app.youtube.auth import GoogleOAuthService, YouTubeAuthError
from app.youtube.publisher import YouTubePublishError, YouTubePublisher
from app.youtube.repository import initialize_youtube_database
from app.video.review_gates import production_qa_allows_approval, video_sha256

_bearer = HTTPBearer(auto_error=False)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visibility: str | None = Field(default=None, pattern="^(private|unlisted|public)$")


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_approval: bool
    channel_id: str | None = Field(default=None, max_length=64)
    visibility: str | None = Field(default=None, pattern="^(private|unlisted|public)$")
    confirm_public: bool = False


class ScriptReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_review: bool
    reviewer: str = Field(min_length=1, max_length=100)

    @field_validator("reviewer", mode="before")
    @classmethod
    def require_reviewer_name(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Reviewer must not be empty.")
        return value.strip()


class ProductionReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_review: bool
    reviewer: str = Field(min_length=1, max_length=100)

    @field_validator("reviewer", mode="before")
    @classmethod
    def require_reviewer_name(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Reviewer must not be empty.")
        return value.strip()


def create_app(
    database_path: str | Path | None = None,
    *,
    publisher: YouTubePublisher | None = None,
    analytics: YouTubeAnalyticsService | None = None,
    report_service: DailyReportService | None = None,
    oauth: GoogleOAuthService | None = None,
) -> FastAPI:
    resolved_path = Path(database_path) if database_path is not None else get_database_path()
    resolved_oauth = oauth or GoogleOAuthService(str(resolved_path))
    resolved_publisher = publisher or YouTubePublisher(resolved_path, oauth=resolved_oauth)
    resolved_analytics = analytics or YouTubeAnalyticsService(resolved_path, oauth=resolved_oauth)
    resolved_reports = report_service or DailyReportService(
        resolved_path, analytics=resolved_analytics
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        initialize_youtube_database(resolved_path)
        scheduler = None
        if os.getenv("MOTIVATIONOS_SCHEDULER_ENABLED", "false").strip().lower() in {
            "1", "true", "yes"
        }:
            scheduler = build_scheduler(str(resolved_path))
            scheduler.start()
        application.state.scheduler = scheduler
        try:
            yield
        finally:
            if scheduler and scheduler.running:
                scheduler.shutdown(wait=False)

    application = FastAPI(title="MotivationOS YouTube Operations", lifespan=lifespan)
    application.state.database_path = str(resolved_path)
    application.state.oauth = resolved_oauth
    application.state.publisher = resolved_publisher
    application.state.analytics = resolved_analytics
    application.state.reports = resolved_reports
    static_directory = Path(__file__).with_name("static")
    application.mount(
        "/review-assets", StaticFiles(directory=static_directory), name="review-assets"
    )

    @application.get("/review", include_in_schema=False)
    def review_dashboard() -> FileResponse:
        return FileResponse(
            static_directory / "review.html",
            headers={
                "Content-Security-Policy": (
                    "default-src 'none'; script-src 'self'; style-src 'self'; "
                    "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
                    "form-action 'none'; frame-ancestors 'none'"
                ),
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )

    @application.get("/production-review", include_in_schema=False)
    def production_review_page() -> FileResponse:
        return FileResponse(static_directory / "production-review.html", headers={
            "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; media-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
        })

    def require_admin(
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> None:
        expected = os.getenv("MOTIVATIONOS_ADMIN_TOKEN", "")
        if not expected:
            raise HTTPException(status_code=503, detail="Administrative API is not configured.")
        supplied = credentials.credentials if credentials else ""
        if not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="Administrative authorization required.")

    @application.get("/api/productions/{production_id}/review", dependencies=[Depends(require_admin)])
    def production_review(production_id: str) -> dict[str, Any]:
        production = get_production_by_id(production_id, resolved_path)
        if production is None:
            raise HTTPException(status_code=404, detail="Production was not found.")
        script = get_script_by_id(production["script_id"], resolved_path)
        if script is None:
            raise HTTPException(status_code=404, detail="Production script was not found.")
        meta = production["metadata"]
        return {
            "id": production["id"], "status": production["status"],
            "error": production["error"], "script": script["script"],
            "title": (meta.get("youtube_metadata") or {}).get("title"),
            "description": (meta.get("youtube_metadata") or {}).get("description"),
            "audio_qa": meta.get("audio_qa"), "video_qa": meta.get("video_qa"),
            "audio_review_decision": meta.get("audio_review_decision"),
            "findings": [*(meta.get("audio_qa") or {}).get("findings", []), *(meta.get("video_qa") or {}).get("findings", [])],
            "video_url": f"/api/productions/{production_id}/video",
        }

    @application.get("/api/productions/{production_id}/video", dependencies=[Depends(require_admin)])
    def production_review_video(production_id: str) -> FileResponse:
        production = get_production_by_id(production_id, resolved_path)
        if production is None:
            raise HTTPException(status_code=404, detail="Production was not found.")
        output_root = Path(production["output_dir"]).resolve()
        video = (output_root / "final.mp4").resolve()
        if video.parent != output_root or not video.is_file():
            raise HTTPException(status_code=404, detail="Final video is unavailable.")
        return FileResponse(video, media_type="video/mp4", headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    @application.post("/api/productions/{production_id}/review/{decision}", dependencies=[Depends(require_admin)])
    def record_production_review(production_id: str, decision: str, body: ProductionReviewDecision) -> dict[str, Any]:
        if decision not in {"approve", "reject"}:
            raise HTTPException(status_code=404, detail="Review decision was not found.")
        if not body.confirm_review:
            raise HTTPException(status_code=400, detail="Explicit confirm_review=true is required.")
        record = get_production_by_id(production_id, resolved_path)
        if record is None:
            raise HTTPException(status_code=404, detail="Production was not found.")
        if record["status"] not in {"needs_review", "completed"}:
            raise HTTPException(status_code=409, detail="Production is not available for review.")
        if record["metadata"].get("review_decision"):
            raise HTTPException(status_code=409, detail="A review decision has already been recorded.")
        meta = dict(record["metadata"])
        if decision == "approve" and not production_qa_allows_approval(meta):
            raise HTTPException(status_code=409, detail="Production QA blocks approval.")
        meta["review_decision"] = {
            "decision": decision,
            "reviewer": body.reviewer,
            "confirmed": True,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
        if decision == "approve":
            output_root = Path(record["output_dir"]).resolve()
            video_path = (output_root / "final.mp4").resolve()
            if output_root not in video_path.parents or not video_path.is_file():
                raise HTTPException(status_code=409, detail="Final video is unavailable for approval.")
            meta["review_decision"]["video_sha256"] = video_sha256(video_path)
        status = "completed" if decision == "approve" else "rejected"
        if decision == "approve":
            artifact_path = Path(record["output_dir"]).resolve() / "metadata.json"
            artifact = {"production_id": production_id, "status": status, "metadata": meta, "error": None}
            temporary_path = artifact_path.with_name("metadata.review.tmp")
            try:
                temporary_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                temporary_path.replace(artifact_path)
            except OSError as error:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise HTTPException(status_code=500, detail="Production review could not be persisted.") from error
        updated = update_production(production_id, status, meta, database_path=resolved_path)
        return {"production_id": updated["id"], "status": updated["status"], "review_decision": meta["review_decision"]}

    @application.post(
        "/api/productions/{production_id}/audio-review/{decision}",
        dependencies=[Depends(require_admin)],
    )
    def record_audio_review(
        production_id: str, decision: str, body: ProductionReviewDecision
    ) -> dict[str, Any]:
        if decision not in {"approve", "reject"}:
            raise HTTPException(status_code=404, detail="Audio review decision was not found.")
        if not body.confirm_review:
            raise HTTPException(status_code=400, detail="Explicit confirm_review=true is required.")
        record = get_production_by_id(production_id, resolved_path)
        if record is None:
            raise HTTPException(status_code=404, detail="Production was not found.")
        if not isinstance(record["metadata"], dict):
            raise HTTPException(status_code=409, detail="Production metadata is invalid.")
        metadata = dict(record["metadata"])
        audio_qa = metadata.get("audio_qa")
        if record["status"] != "needs_review" or not isinstance(audio_qa, dict) or audio_qa.get("technical_status") != "needs_review":
            raise HTTPException(status_code=409, detail="Audio is not awaiting human review.")
        if metadata.get("audio_review_decision"):
            raise HTTPException(status_code=409, detail="An audio review decision has already been recorded.")
        audio_review = {
            "decision": decision,
            "reviewer": body.reviewer,
            "confirmed": True,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
        metadata["audio_review_decision"] = audio_review
        status = "needs_review" if decision == "approve" else "rejected"
        updated = update_production(production_id, status, metadata, database_path=resolved_path)
        return {
            "production_id": updated["id"],
            "status": updated["status"],
            "audio_review_decision": audio_review,
        }

    @application.get("/api/scripts/review", dependencies=[Depends(require_admin)])
    def scripts_awaiting_review(
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> list[dict[str, Any]]:
        return get_scripts_needing_review(resolved_path, limit)

    @application.get(
        "/api/scripts/{script_id}/review", dependencies=[Depends(require_admin)]
    )
    def script_review_status(script_id: int) -> dict[str, Any]:
        record = get_script_by_id(script_id, resolved_path)
        if record is None:
            raise HTTPException(status_code=404, detail="Script was not found.")
        return {
            "script_id": record["id"],
            "topic": record["topic"],
            "language": record["language"],
            "script": record["script"],
            "created_at": record["created_at"],
            "status": record["status"],
            "quality_score": record["quality_score"],
            "evaluation": record["evaluation"],
            "reviewed_at": record["reviewed_at"],
            "reviewed_by": record["reviewed_by"],
        }

    def record_script_review(
        script_id: int, decision: str, body: ScriptReviewRequest
    ) -> dict[str, Any]:
        if not body.confirm_review:
            raise HTTPException(status_code=400, detail="Explicit confirm_review=true is required.")
        try:
            record = review_script(
                script_id, decision, body.reviewer, database_path=resolved_path
            )
        except ScriptNotFoundError as error:
            raise HTTPException(status_code=404, detail="Script was not found.") from error
        except ScriptReviewError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {
            "script_id": record["id"],
            "status": record["status"],
            "reviewed_at": record["reviewed_at"],
            "reviewed_by": record["reviewed_by"],
        }

    @application.post(
        "/api/scripts/{script_id}/approve", dependencies=[Depends(require_admin)]
    )
    def approve_script(script_id: int, body: ScriptReviewRequest) -> dict[str, Any]:
        return record_script_review(script_id, "approved", body)

    @application.post(
        "/api/scripts/{script_id}/reject", dependencies=[Depends(require_admin)]
    )
    def reject_script(script_id: int, body: ScriptReviewRequest) -> dict[str, Any]:
        return record_script_review(script_id, "rejected", body)

    @application.get("/api/youtube/status", dependencies=[Depends(require_admin)])
    def youtube_status() -> dict[str, Any]:
        return resolved_oauth.status()

    @application.get("/api/youtube/auth/start", dependencies=[Depends(require_admin)])
    def youtube_auth_start() -> dict[str, str]:
        try:
            return resolved_oauth.start_authorization()
        except YouTubeAuthError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @application.get("/api/youtube/auth/callback")
    def youtube_auth_callback(
        state: str | None = Query(default=None),
        code: str | None = Query(default=None),
        error: str | None = Query(default=None),
    ) -> dict[str, str]:
        try:
            resolved_oauth.complete_authorization(state=state, code=code, provider_error=error)
        except YouTubeAuthError as auth_error:
            raise HTTPException(status_code=400, detail=str(auth_error)) from auth_error
        return {"status": "connected", "message": "Google OAuth completed. Tokens are stored encrypted."}

    @application.get("/api/youtube/videos", dependencies=[Depends(require_admin)])
    def youtube_videos(max_results: int = Query(default=50, ge=1, le=100)) -> dict[str, Any]:
        try:
            return resolved_publisher.list_channel_videos(max_results)
        except (YouTubePublishError, YouTubeAuthError) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @application.post(
        "/api/publishing/{production_id}/review", dependencies=[Depends(require_admin)]
    )
    def publishing_review(production_id: str, body: ReviewRequest) -> dict[str, Any]:
        try:
            return resolved_publisher.request_review(production_id, body.visibility)
        except YouTubePublishError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @application.post(
        "/api/publishing/{production_id}/approve", dependencies=[Depends(require_admin)]
    )
    def publishing_approve(production_id: str, body: ApprovalRequest) -> dict[str, Any]:
        if not body.confirm_approval:
            raise HTTPException(status_code=400, detail="Explicit confirm_approval=true is required.")
        try:
            approval = resolved_publisher.approve(
                production_id,
                channel_id=body.channel_id,
                visibility=body.visibility,
                confirm_public=body.confirm_public,
            )
            return _approval_response(approval)
        except YouTubePublishError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @application.post(
        "/api/publishing/{production_id}/upload", dependencies=[Depends(require_admin)]
    )
    def publishing_upload(production_id: str) -> dict[str, Any]:
        try:
            return _upload_response(resolved_publisher.upload(production_id))
        except YouTubePublishError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @application.post(
        "/api/publishing/{production_id}/cancel", dependencies=[Depends(require_admin)]
    )
    def publishing_cancel(production_id: str) -> dict[str, Any]:
        try:
            return _approval_response(resolved_publisher.cancel(production_id))
        except YouTubePublishError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @application.post(
        "/api/publishing/{production_id}/reconcile", dependencies=[Depends(require_admin)]
    )
    def publishing_reconcile(production_id: str) -> dict[str, Any]:
        try:
            return resolved_publisher.reconcile(production_id)
        except YouTubePublishError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @application.get(
        "/api/publishing/{production_id}/status", dependencies=[Depends(require_admin)]
    )
    def publishing_status(production_id: str) -> dict[str, Any]:
        return _upload_status_response(resolved_publisher.status(production_id))

    @application.get("/api/analytics/channel", dependencies=[Depends(require_admin)])
    def channel_analytics(
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, Any]:
        try:
            return resolved_analytics.channel_report(
                start_date.isoformat() if start_date else None,
                end_date.isoformat() if end_date else None,
            )
        except YouTubeAuthError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        except YouTubeAnalyticsError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @application.get(
        "/api/analytics/videos/{video_id}", dependencies=[Depends(require_admin)]
    )
    def video_analytics(
        video_id: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, Any]:
        try:
            return resolved_analytics.video_report(
                video_id,
                start_date.isoformat() if start_date else None,
                end_date.isoformat() if end_date else None,
            )
        except YouTubeAuthError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        except YouTubeAnalyticsError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @application.post("/api/reports/daily", dependencies=[Depends(require_admin)])
    def daily_report(report_date: date | None = None) -> dict[str, Any]:
        return resolved_reports.run(report_date)

    @application.post("/api/scheduler/run-daily", dependencies=[Depends(require_admin)])
    def run_daily_scheduler_job() -> dict[str, Any]:
        return run_scheduled_report(
            database_path=str(resolved_path), report_service=resolved_reports
        )

    return application


def _approval_response(approval: dict[str, Any]) -> dict[str, Any]:
    return {
        "production_id": approval["production_id"],
        "state": approval["state"],
        "visibility": approval["visibility"],
        "approved_at": approval["approved_at"],
        "approved_channel_id": approval.get("approved_channel_id"),
        "metadata": approval["metadata"],
        "error": approval["error"],
    }


def _upload_response(upload: dict[str, Any]) -> dict[str, Any]:
    return {
        "production_id": upload["production_id"],
        "video_id": upload["video_id"],
        "status": upload["status"],
        "visibility": upload["visibility"],
        "uploaded_at": upload["uploaded_at"],
        "error": upload["error"],
    }


def _upload_status_response(status: dict[str, Any]) -> dict[str, Any]:
    upload = status.get("upload")
    return {
        "production_id": status["production_id"],
        "approval": _approval_response(status["approval"]) if status.get("approval") else None,
        "upload": _upload_response(upload) if upload else None,
    }


app = create_app()
