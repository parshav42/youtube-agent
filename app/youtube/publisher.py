"""Human-approved, resumable YouTube Data API uploads."""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import (
    get_auto_publish_settings,
    get_database_path,
    get_youtube_notify_subscribers,
    get_youtube_visibility,
)
from app.database.db import get_production_by_id
from app.schemas.script_schema import ProductionMetadata
from app.video.video_assembler import validate_video
from app.video.review_gates import has_recorded_decision, production_qa_allows_approval, video_sha256
from app.youtube.auth import GoogleOAuthService
from app.youtube.repository import (
    approve_production,
    claim_upload,
    get_approval,
    get_upload,
    record_channel_identity_audit,
    request_review,
    update_upload_progress,
)


class YouTubePublishError(RuntimeError):
    """A safe-to-display upload error that never includes credentials."""


class _UnverifiedUploadResult(YouTubePublishError):
    """A terminal response that cannot safely authorize a completed state."""

    def __init__(self, response_summary: dict[str, Any], message: str = "YouTube returned an unverified upload result.") -> None:
        super().__init__(message)
        self.response_summary = response_summary


class PublishingMetadata(BaseModel):
    """Metadata snapshotted for review and then sent to YouTube."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    language: str = Field(min_length=2, max_length=35)
    category_id: str = Field(pattern=r"^\d{1,3}$")
    publishing_notes: str = Field(default="", max_length=1000)
    self_declared_made_for_kids: bool | None = None

    @field_validator("title", "description", "language", "category_id", mode="before")
    @classmethod
    def trim_required(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Publishing metadata fields must not be blank.")
        return value.strip()

    @field_validator("tags", mode="before")
    @classmethod
    def clean_tags(cls, values: list[str]) -> list[str]:
        tags = [str(value).strip().lstrip("#") for value in values if str(value).strip()]
        if sum(len(tag) for tag in tags) > 500:
            raise ValueError("YouTube tags must not exceed 500 characters in total.")
        return tags


class YouTubePublisher:
    """Publish an approved completed production through YouTube's official API."""

    def __init__(
        self,
        database_path: str | Path | None = None,
        *,
        oauth: GoogleOAuthService | None = None,
        api_builder: Callable[..., Any] | None = None,
        media_upload_factory: Callable[..., Any] | None = None,
        video_validator: Callable[[str | Path], dict[str, Any]] = validate_video,
    ) -> None:
        self.database_path = database_path or get_database_path()
        self.oauth = oauth or GoogleOAuthService(str(self.database_path))
        self.api_builder = api_builder or self._build_api
        self.media_upload_factory = media_upload_factory or self._media_upload
        self.video_validator = video_validator

    def request_review(
        self,
        production_id: str,
        visibility: str | None = None,
    ) -> dict[str, Any]:
        """Validate a completed production and create its awaiting-review record."""
        production = self._load_completed_production(production_id)
        if get_approval(production_id, self.database_path) is not None or get_upload(
            production_id, self.database_path
        ) is not None:
            raise YouTubePublishError(
                "A publishing review or upload record already exists for this production."
            )
        metadata = self._load_metadata(production)
        target_visibility = visibility or get_youtube_visibility()
        if target_visibility not in {"private", "unlisted", "public"}:
            raise YouTubePublishError("Publishing visibility must be private, unlisted, or public.")
        approval = request_review(
            production_id,
            target_visibility,
            metadata.model_dump(mode="json"),
            self.database_path,
        )
        upload = get_upload(production_id, self.database_path)
        return {"approval": approval, "upload": upload, "video": self._video_summary(production, metadata)}

    def approve_automatically(self, production_id: str) -> dict[str, Any]:
        """Record policy approval only when automatic publishing is explicitly enabled."""
        settings = get_auto_publish_settings()
        if not settings["enabled"] or not settings["auto_approve_productions"]:
            raise YouTubePublishError("Automatic production approval is disabled by configuration.")
        production = self._load_completed_production(production_id)
        approval = get_approval(production_id, self.database_path)
        if approval and approval["state"] not in {"awaiting_review", "approved"}:
            raise YouTubePublishError("Production publishing is not eligible for automatic approval.")
        if approval and approval["state"] == "approved":
            if approval["visibility"] != "public":
                raise YouTubePublishError("Automatic publishing requires a public approval record.")
            return approval
        if approval is None:
            metadata = self._load_metadata(production)
            metadata.category_id = settings["category_id"]
            metadata.self_declared_made_for_kids = bool(settings["made_for_kids"])
            request_review(
                production_id,
                "public",
                metadata.model_dump(mode="json"),
                self.database_path,
            )
        approval = get_approval(production_id, self.database_path)
        if approval is None or approval["visibility"] != "public":
            raise YouTubePublishError("Automatic publishing requires a public review record.")
        try:
            service = self.api_builder(self.oauth.authorized_credentials())
            channel_id = self._authenticated_channel_id(service)
        except Exception as error:
            raise YouTubePublishError("The authenticated YouTube channel could not be verified.") from error
        if not channel_id:
            raise YouTubePublishError("The authenticated YouTube channel could not be verified.")
        record_channel_identity_audit(
            production_id,
            phase="approval",
            outcome="verified",
            reason="automatic_policy_channel_verified",
            approved_channel_id=channel_id,
            authenticated_channel_id=channel_id,
            database_path=self.database_path,
        )
        approved_metadata = dict(approval["metadata"])
        approved_metadata["self_declared_made_for_kids"] = bool(settings["made_for_kids"])
        return approve_production(
            production_id,
            "public",
            approved_metadata,
            self.database_path,
            approved_channel_id=channel_id,
            authenticated_channel_id_at_approval=channel_id,
        )

    def approve(
        self,
        production_id: str,
        *,
        channel_id: str | None = None,
        visibility: str,
        self_declared_made_for_kids: bool,
        confirm_public: bool = False,
    ) -> dict[str, Any]:
        """Record explicit human approval and the exact visibility choice."""
        approval = get_approval(production_id, self.database_path)
        if approval is None or approval["state"] != "awaiting_review":
            raise YouTubePublishError("Publishing approval is only accepted from awaiting_review.")
        upload = get_upload(production_id, self.database_path)
        if upload is None or upload["status"] != "awaiting_review":
            raise YouTubePublishError("A conflicting or active upload record already exists.")
        self._load_completed_production(production_id)
        selected_visibility = visibility
        if selected_visibility not in {"private", "unlisted", "public"}:
            raise YouTubePublishError("An explicit valid publishing visibility is required.")
        if selected_visibility == "public" and not confirm_public:
            raise YouTubePublishError(
                "Public visibility requires confirm_public=true in the explicit approval request."
            )
        metadata = PublishingMetadata.model_validate(approval["metadata"])
        if type(self_declared_made_for_kids) is not bool:
            raise YouTubePublishError("An explicit audience setting is required for publishing approval.")
        approval_metadata = metadata.model_dump(mode="json")
        approval_metadata["self_declared_made_for_kids"] = self_declared_made_for_kids
        approved_channel_id = channel_id.strip() if isinstance(channel_id, str) else ""
        if not approved_channel_id:
            record_channel_identity_audit(
                production_id,
                phase="approval",
                outcome="rejected",
                reason="approved_channel_missing",
                database_path=self.database_path,
            )
            raise YouTubePublishError("An explicitly approved YouTube channel ID is required.")
        try:
            credentials = self.oauth.authorized_credentials()
            service = self.api_builder(credentials)
            authenticated_channel_id = self._authenticated_channel_id(service)
        except Exception as error:
            record_channel_identity_audit(
                production_id,
                phase="approval",
                outcome="rejected",
                reason="authenticated_channel_unavailable",
                approved_channel_id=approved_channel_id,
                database_path=self.database_path,
            )
            raise YouTubePublishError("The authenticated YouTube channel could not be verified.") from error
        if not authenticated_channel_id:
            record_channel_identity_audit(
                production_id,
                phase="approval",
                outcome="rejected",
                reason="authenticated_channel_missing",
                approved_channel_id=approved_channel_id,
                database_path=self.database_path,
            )
            raise YouTubePublishError("The authenticated YouTube channel could not be verified.")
        return approve_production(
            production_id,
            selected_visibility,
            approval_metadata,
            self.database_path,
            approved_channel_id=approved_channel_id,
            authenticated_channel_id_at_approval=authenticated_channel_id,
        )

    def upload(self, production_id: str) -> dict[str, Any]:
        """Start or resume the one authorized resumable upload for a production."""
        approval = get_approval(production_id, self.database_path)
        upload = get_upload(production_id, self.database_path)
        resumable_retry = (
            approval is not None
            and approval["state"] in {"failed", "uploading"}
            and upload is not None
            and upload["status"] == approval["state"]
            and bool(upload.get("resumable_uri"))
        )
        if approval is None or (approval["state"] != "approved" and not resumable_retry):
            raise YouTubePublishError("Upload rejected: explicit publishing approval is required.")
        production = self._load_completed_production(production_id)
        metadata = PublishingMetadata.model_validate(approval["metadata"])
        video_path = self._video_path(production)
        try:
            self.video_validator(video_path)
        except Exception as error:
            raise YouTubePublishError("Production MP4 failed media validation.") from error
        try:
            credentials = self.oauth.authorized_credentials()
        except Exception as error:
            record_channel_identity_audit(
                production_id,
                phase="upload",
                outcome="rejected",
                reason="authenticated_channel_unavailable",
                approved_channel_id=approval.get("approved_channel_id"),
                database_path=self.database_path,
            )
            raise YouTubePublishError("The authenticated YouTube channel could not be verified.") from error
        try:
            service = self.api_builder(credentials)
            self._verify_channel_identity(production_id, approval, service)
            production = self._load_completed_production(production_id)
            video_path = self._video_path(production)
            upload = claim_upload(production_id, self.database_path)
        except ValueError as error:
            raise YouTubePublishError(str(error)) from error
        except YouTubePublishError:
            raise

        request: Any | None = None
        upload_started = False
        try:
            description = self._description_with_reference(metadata.description, production_id)
            body = {
                "snippet": {
                    "title": metadata.title,
                    "description": description,
                    "tags": metadata.tags,
                    "categoryId": metadata.category_id,
                    "defaultLanguage": metadata.language,
                },
                "status": {
                    "privacyStatus": approval["visibility"],
                    "selfDeclaredMadeForKids": metadata.self_declared_made_for_kids,
                },
            }
            media_body = self.media_upload_factory(video_path)
            request = service.videos().insert(
                part="snippet,status", body=body, media_body=media_body,
                notifySubscribers=get_youtube_notify_subscribers(),
            )
            if upload.get("resumable_uri"):
                request.resumable_uri = self.oauth.decrypt_persistent_secret(
                    upload["resumable_uri"]
                )
            response = None
            while response is None:
                if not upload_started:
                    self._load_completed_production(production_id)
                    upload_started = True
                status, response = request.next_chunk(num_retries=0)
                resumable_uri = getattr(request, "resumable_uri", None)
                if resumable_uri:
                    update_upload_progress(
                        production_id,
                        status="uploading",
                        resumable_uri=self.oauth.encrypt_persistent_secret(resumable_uri),
                        visibility=approval["visibility"],
                        database_path=self.database_path,
                    )
            video_id = self._validate_upload_result(response, approval)
            self._verify_remote_upload(service, video_id, approval)
            final_status = "uploaded_private" if approval["visibility"] == "private" else "uploaded"
            saved = update_upload_progress(
                production_id,
                status=final_status,
                video_id=video_id,
                api_response=response,
                visibility=approval["visibility"],
                channel_id=approval.get("approved_channel_id"),
                database_path=self.database_path,
            )
            extras = self._post_upload_extras(service, video_id, production)
            if extras:
                response["motivationos_post_upload"] = extras
                saved = update_upload_progress(
                    production_id,
                    status=final_status,
                    api_response=response,
                    visibility=approval["visibility"],
                    channel_id=approval.get("approved_channel_id"),
                    database_path=self.database_path,
                )
            return saved
        except Exception as error:
            resumable_uri = getattr(request, "resumable_uri", None) if request is not None else None
            if isinstance(error, _UnverifiedUploadResult):
                self._mark_failed(
                    production_id,
                    approval["visibility"],
                    None,
                    str(error),
                    clear_resumable_uri=True,
                    api_response=error.response_summary,
                )
                raise
            if isinstance(error, YouTubePublishError) and not upload_started:
                self._mark_failed(
                    production_id,
                    approval["visibility"],
                    resumable_uri,
                    str(error),
                )
                raise
            http_status = getattr(getattr(error, "resp", None), "status", None)
            api_reason = self._api_error_reason(error)
            if http_status == 401:
                self.oauth.mark_reauthorization_required()
                detail = "Google authorization expired or was revoked; reconnect the YouTube account."
            elif http_status == 403:
                if api_reason in {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}:
                    detail = "YouTube API quota is exhausted; automatic uploads are paused until quota resets."
                else:
                    detail = "YouTube denied the upload; check channel permission, API project restrictions, and quota."
            elif http_status == 429:
                detail = "YouTube upload quota or rate limit was reached; retry after its reset."
            else:
                detail = "YouTube upload failed due to a network or API error."
            self._mark_failed(production_id, approval["visibility"], resumable_uri, detail)
            if isinstance(error, YouTubePublishError):
                raise
            raise YouTubePublishError(detail) from error

    def status(self, production_id: str) -> dict[str, Any]:
        approval = get_approval(production_id, self.database_path)
        upload = get_upload(production_id, self.database_path)
        return {"production_id": production_id, "approval": approval, "upload": upload}

    def list_channel_videos(self, max_results: int = 50) -> dict[str, Any]:
        if not 1 <= max_results <= 100:
            raise ValueError("max_results must be between 1 and 100.")
        try:
            service = self.api_builder(self.oauth.authorized_credentials())
            channel_response = service.channels().list(
                part="snippet,contentDetails", mine=True, maxResults=1
            ).execute(num_retries=2)
            channels = channel_response.get("items", [])
            if not channels:
                return {"channel": None, "videos": []}
            channel = channels[0]
            uploads_id = channel["contentDetails"]["relatedPlaylists"]["uploads"]
            playlist_items = []
            page_token = None
            while len(playlist_items) < max_results:
                page = service.playlistItems().list(
                    part="contentDetails", playlistId=uploads_id,
                    maxResults=min(50, max_results - len(playlist_items)),
                    pageToken=page_token,
                ).execute(num_retries=2)
                playlist_items.extend(page.get("items", []))
                page_token = page.get("nextPageToken")
                if not page_token:
                    break
            ids = [item.get("contentDetails", {}).get("videoId") for item in playlist_items]
            ids = [video_id for video_id in ids if video_id]
            videos = []
            for offset in range(0, len(ids), 50):
                result = service.videos().list(
                    part="snippet,status", id=",".join(ids[offset : offset + 50]), maxResults=50
                ).execute(num_retries=2)
                videos.extend(
                    {
                        "video_id": item.get("id"),
                        "title": item.get("snippet", {}).get("title"),
                        "published_at": item.get("snippet", {}).get("publishedAt"),
                        "visibility": item.get("status", {}).get("privacyStatus"),
                        "url": f"https://www.youtube.com/watch?v={item.get('id')}",
                    }
                    for item in result.get("items", [])
                )
            return {
                "channel": {
                    "channel_id": channel.get("id"),
                    "title": channel.get("snippet", {}).get("title"),
                },
                "videos": videos,
            }
        except Exception as error:
            if isinstance(error, YouTubePublishError):
                raise
            raise YouTubePublishError(
                "Authorized YouTube channel videos could not be retrieved."
            ) from error

    def cancel(self, production_id: str) -> dict[str, Any]:
        """Cancel a review/approved/failed record without deleting its audit trail."""
        from app.youtube.repository import cancel_publishing

        try:
            return cancel_publishing(production_id, self.database_path)
        except ValueError as error:
            raise YouTubePublishError(str(error)) from error

    def reconcile(self, production_id: str) -> dict[str, Any]:
        """Find an upload whose API response may have been lost, without re-uploading."""
        upload = get_upload(production_id, self.database_path)
        if upload is None:
            raise YouTubePublishError("Publishing record was not found.")
        if upload.get("video_id"):
            return {"production_id": production_id, "status": upload["status"], "video_id": upload["video_id"], "reconciled": True}
        expired_without_session = (
            upload["status"] == "uploading"
            and not upload.get("resumable_uri")
            and self._upload_lease_expired(upload.get("lease_until"))
        )
        if expired_without_session:
            approval = get_approval(production_id, self.database_path)
            if approval is None:
                raise YouTubePublishError("Publishing approval was not found.")
            upload = update_upload_progress(
                production_id,
                status="failed",
                error="Upload was interrupted before a resumable session was saved; reconciliation is required.",
                visibility=approval["visibility"],
                clear_resumable_uri=True,
                database_path=self.database_path,
            )
        if upload["status"] != "failed" or upload.get("resumable_uri"):
            raise YouTubePublishError(
                "Reconciliation requires a failed upload or an expired upload without a resumable session."
            )
        try:
            approval = get_approval(production_id, self.database_path)
            if approval is None:
                raise YouTubePublishError("Publishing approval was not found.")
            service = self.api_builder(self.oauth.authorized_credentials())
            self._verify_channel_identity(production_id, approval, service)
            marker = f"MotivationOS production reference: {production_id}"
            search = service.search().list(
                part="id,snippet", q=production_id, type="video", forMine=True, maxResults=25
            ).execute(num_retries=2)
            matches = []
            for item in search.get("items", []):
                candidate_id = item.get("id", {}).get("videoId")
                if not candidate_id:
                    continue
                details = service.videos().list(
                    part="snippet,status", id=candidate_id, maxResults=1
                ).execute(num_retries=2)
                for video in details.get("items", []):
                    description = video.get("snippet", {}).get("description", "")
                    if marker in description:
                        if video.get("id") != candidate_id:
                            raise _UnverifiedUploadResult(
                                self._safe_upload_result_summary(video),
                                "YouTube reconciliation returned an unverified upload result.",
                            )
                        matches.append(video)
            if not matches:
                return {
                    "production_id": production_id,
                    "status": "failed",
                    "reconciled": False,
                    "detail": "No matching uploaded video was returned; automatic re-upload remains blocked.",
                }
            if len(matches) != 1:
                raise YouTubePublishError(
                    "More than one YouTube video contains this production reference; manual resolution is required."
                )
            video = matches[0]
            video_id = self._validate_upload_result(video, approval)
            visibility = approval["visibility"]
            final_status = "uploaded_private" if visibility == "private" else "uploaded"
            saved = update_upload_progress(
                production_id,
                status=final_status,
                video_id=video_id,
                api_response=video,
                visibility=visibility,
                database_path=self.database_path,
            )
            return {
                "production_id": production_id,
                "status": saved["status"],
                "video_id": saved["video_id"],
                "reconciled": True,
            }
        except YouTubePublishError:
            raise
        except Exception as error:
            raise YouTubePublishError(
                "YouTube upload reconciliation could not complete; automatic re-upload remains blocked."
            ) from error

    def _load_completed_production(self, production_id: str) -> dict[str, Any]:
        production = get_production_by_id(production_id, self.database_path)
        if production is None:
            raise YouTubePublishError("Production was not found.")
        if production["status"] != "completed":
            raise YouTubePublishError("Only completed productions can be published.")
        metadata = production.get("metadata")
        if not isinstance(metadata, dict) or not has_recorded_decision(
            metadata.get("review_decision"), "approve"
        ):
            raise YouTubePublishError(
                "A confirmed production-review approval with reviewer and timestamp is required."
            )
        if not production_qa_allows_approval(metadata):
            raise YouTubePublishError("Production QA requirements have not been satisfied.")
        decision = metadata["review_decision"]
        try:
            approved_video = self._video_path(production)
            current_digest = video_sha256(approved_video)
        except (OSError, YouTubePublishError) as error:
            raise YouTubePublishError("The final reviewed video is unavailable.") from error
        if decision.get("video_sha256") != current_digest:
            raise YouTubePublishError(
                "The final video differs from the artifact recorded at production review."
            )
        return production

    @staticmethod
    def _authenticated_channel_id(service: Any) -> str | None:
        response = service.channels().list(part="id", mine=True, maxResults=1).execute(
            num_retries=0
        )
        channels = response.get("items", []) if isinstance(response, dict) else []
        if not channels or not isinstance(channels[0], dict):
            return None
        channel_id = channels[0].get("id")
        return channel_id.strip() if isinstance(channel_id, str) and channel_id.strip() else None

    @staticmethod
    def _upload_lease_expired(lease_until: str | None) -> bool:
        if lease_until is None:
            return True
        try:
            lease_expiry = datetime.fromisoformat(lease_until)
        except ValueError:
            return False
        if lease_expiry.tzinfo is None:
            return False
        return lease_expiry <= datetime.now(timezone.utc)

    def _verify_channel_identity(
        self,
        production_id: str,
        approval: dict[str, Any],
        service: Any,
    ) -> None:
        approved_channel_id = approval.get("approved_channel_id")
        approval_account_id = approval.get("authenticated_channel_id_at_approval")

        def reject(reason: str, authenticated_channel_id: str | None = None) -> None:
            record_channel_identity_audit(
                production_id,
                phase="upload",
                outcome="rejected",
                reason=reason,
                approved_channel_id=approved_channel_id,
                authenticated_channel_id=authenticated_channel_id,
                database_path=self.database_path,
            )
            raise YouTubePublishError("Upload rejected: YouTube channel identity verification failed.")

        if not isinstance(approved_channel_id, str) or not approved_channel_id.strip():
            reject("approved_channel_missing")
        if not isinstance(approval_account_id, str) or not approval_account_id.strip():
            reject("approval_account_missing")
        try:
            authenticated_channel_id = self._authenticated_channel_id(service)
        except Exception as error:
            reject("authenticated_channel_unavailable")
            raise AssertionError("unreachable") from error
        if not authenticated_channel_id:
            reject("authenticated_channel_missing")
        if authenticated_channel_id != approval_account_id:
            reject("authenticated_account_changed", authenticated_channel_id)
        if authenticated_channel_id != approved_channel_id:
            reject("approved_channel_mismatch", authenticated_channel_id)
        record_channel_identity_audit(
            production_id,
            phase="upload",
            outcome="verified",
            reason="channel_identity_match",
            approved_channel_id=approved_channel_id,
            authenticated_channel_id=authenticated_channel_id,
            database_path=self.database_path,
        )

    @staticmethod
    def _validate_upload_result(response: Any, approval: dict[str, Any]) -> str:
        if not isinstance(response, dict):
            raise _UnverifiedUploadResult(YouTubePublisher._safe_upload_result_summary(response))
        video_id = response.get("id")
        if not isinstance(video_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) is None:
            raise _UnverifiedUploadResult(YouTubePublisher._safe_upload_result_summary(response))
        snippet = response.get("snippet")
        channel_id = snippet.get("channelId") if isinstance(snippet, dict) else None
        approved_channel_id = approval.get("approved_channel_id")
        if not isinstance(channel_id, str) or channel_id != approved_channel_id:
            raise _UnverifiedUploadResult(YouTubePublisher._safe_upload_result_summary(response))
        status = response.get("status")
        privacy_status = status.get("privacyStatus") if isinstance(status, dict) else None
        if approval.get("visibility") == "public" and privacy_status == "private":
            raise _UnverifiedUploadResult(
                YouTubePublisher._safe_upload_result_summary(response),
                "Google restricted this upload to private. Public uploads require the OAuth API project to complete YouTube's required audit; see the Google Cloud setup runbook.",
            )
        if (
            privacy_status not in {"private", "unlisted", "public"}
            or privacy_status != approval.get("visibility")
        ):
            raise _UnverifiedUploadResult(YouTubePublisher._safe_upload_result_summary(response))
        audience_status = status.get("selfDeclaredMadeForKids")
        expected_audience = approval.get("metadata", {}).get("self_declared_made_for_kids")
        if type(audience_status) is not bool or audience_status != expected_audience:
            raise _UnverifiedUploadResult(YouTubePublisher._safe_upload_result_summary(response))
        return video_id

    @staticmethod
    def _verify_remote_upload(
        service: Any,
        video_id: str,
        approval: dict[str, Any],
    ) -> None:
        try:
            result = service.videos().list(
                part="snippet,status", id=video_id, maxResults=1
            ).execute(num_retries=0)
        except Exception as error:
            raise _UnverifiedUploadResult(
                {"response_type": "remote_verification_unavailable"}
            ) from error
        items = result.get("items") if isinstance(result, dict) else None
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise _UnverifiedUploadResult({"response_type": "remote_video_not_retrievable"})
        remote_video_id = YouTubePublisher._validate_upload_result(items[0], approval)
        if remote_video_id != video_id:
            raise _UnverifiedUploadResult(
                YouTubePublisher._safe_upload_result_summary(items[0])
            )

    @staticmethod
    def _safe_upload_result_summary(response: Any) -> dict[str, Any]:
        if not isinstance(response, dict):
            return {"response_type": type(response).__name__[:32]}
        summary: dict[str, Any] = {}
        video_id = response.get("id")
        if isinstance(video_id, str):
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                summary["id"] = video_id
            else:
                summary["id_format"] = "invalid"
        snippet = response.get("snippet")
        channel_id = snippet.get("channelId") if isinstance(snippet, dict) else None
        if isinstance(channel_id, str):
            summary["channel_id"] = channel_id[:64]
        status = response.get("status")
        privacy_status = status.get("privacyStatus") if isinstance(status, dict) else None
        if isinstance(privacy_status, str):
            summary["privacy_status"] = (
                privacy_status
                if privacy_status in {"private", "unlisted", "public"}
                else "invalid"
            )
        audience_status = status.get("selfDeclaredMadeForKids") if isinstance(status, dict) else None
        if type(audience_status) is bool:
            summary["audience_status"] = audience_status
        return summary

    def _load_metadata(self, production: dict[str, Any]) -> PublishingMetadata:
        artifact_path = Path(production["output_dir"]) / "metadata.json"
        try:
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise YouTubePublishError("Completed production metadata is missing or invalid.") from error
        if artifact.get("production_id") != production["id"] or artifact.get("status") != "completed":
            raise YouTubePublishError("Production metadata does not match a completed production record.")
        artifact_metadata = artifact.get("metadata")
        if not isinstance(artifact_metadata, dict):
            raise YouTubePublishError("Completed production has no metadata object.")
        generated = artifact_metadata.get("youtube_metadata")
        if not isinstance(generated, dict):
            raise YouTubePublishError("HF-generated YouTube metadata is unavailable for this production.")
        try:
            normalized = ProductionMetadata.model_validate(generated)
        except Exception as error:
            raise YouTubePublishError("HF-generated YouTube metadata failed validation.") from error
        tags = normalized.tags or [tag.lstrip("#") for tag in normalized.hashtags]
        language = generated.get("language") or self._language_code(
            production.get("metadata", {}).get("language", "")
        ) or normalized.language
        try:
            return PublishingMetadata.model_validate(
                {
                    "title": normalized.title,
                    "description": normalized.description,
                    "tags": tags,
                    "language": language,
                    "category_id": os.getenv("YOUTUBE_CATEGORY_ID", normalized.category_id),
                    "publishing_notes": normalized.publishing_notes,
                    "self_declared_made_for_kids": False,
                }
            )
        except Exception as error:
            raise YouTubePublishError("Publishing metadata exceeds YouTube limits or is incomplete.") from error

    def _post_upload_extras(
        self,
        service: Any,
        video_id: str,
        production: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply optional thumbnail and playlist operations without undoing upload success."""
        import os

        outcomes: dict[str, Any] = {}
        thumbnail = production.get("metadata", {}).get("thumbnail")
        thumbnail_path = thumbnail.get("path") if isinstance(thumbnail, dict) else None
        if thumbnail_path and Path(thumbnail_path).is_file():
            try:
                service.thumbnails().set(
                    videoId=video_id,
                    media_body=self.media_upload_factory(Path(thumbnail_path)),
                ).execute(num_retries=2)
                outcomes["thumbnail"] = "uploaded"
            except Exception:
                outcomes["thumbnail"] = "failed"
        playlist_id = os.getenv("YOUTUBE_PLAYLIST_ID", "").strip()
        if playlist_id:
            try:
                service.playlistItems().insert(
                    part="snippet",
                    body={"snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id},
                    }},
                ).execute(num_retries=2)
                outcomes["playlist"] = "added"
            except Exception:
                outcomes["playlist"] = "failed"
        return outcomes

    @staticmethod
    def _api_error_reason(error: Exception) -> str | None:
        content = getattr(error, "content", None)
        if not isinstance(content, bytes):
            return None
        try:
            details = json.loads(content).get("error", {}).get("errors", [])
        except (json.JSONDecodeError, AttributeError):
            return None
        return details[0].get("reason") if details and isinstance(details[0], dict) else None

    @staticmethod
    def _video_path(production: dict[str, Any]) -> Path:
        video = production.get("metadata", {}).get("video", {})
        raw_path = video.get("path") if isinstance(video, dict) else None
        if not raw_path:
            raise YouTubePublishError("Production has no validated MP4 path in its record.")
        root = Path(production["output_dir"]).resolve()
        path = Path(raw_path).resolve()
        expected_path = (root / "final.mp4").resolve()
        if (
            root not in path.parents
            or path != expected_path
            or path.suffix.lower() != ".mp4"
            or not path.is_file()
        ):
            raise YouTubePublishError("Production MP4 is missing or outside its production directory.")
        return path

    @staticmethod
    def _video_summary(
        production: dict[str, Any], metadata: PublishingMetadata
    ) -> dict[str, Any]:
        path = YouTubePublisher._video_path(production)
        return {
            "production_id": production["id"],
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "metadata": metadata.model_dump(mode="json"),
        }

    @staticmethod
    def _description_with_reference(description: str, production_id: str) -> str:
        reference = f"\n\nMotivationOS production reference: {production_id}"
        if len(description) + len(reference) > 5000:
            raise YouTubePublishError("Description plus reconciliation reference exceeds YouTube's limit.")
        return description + reference

    @staticmethod
    def _language_code(language: str) -> str:
        value = language.strip().lower()
        if value.startswith("hindi") or value.startswith("hinglish"):
            return "hi"
        if value.startswith("english"):
            return "en"
        return value or "hi"

    def _mark_failed(
        self,
        production_id: str,
        visibility: str,
        resumable_uri: str | None,
        error: str,
        *,
        clear_resumable_uri: bool = False,
        api_response: dict[str, Any] | None = None,
    ) -> None:
        current = get_upload(production_id, self.database_path)
        if current is None:
            return
        saved_uri = None if clear_resumable_uri else (
            self.oauth.encrypt_persistent_secret(resumable_uri)
            if resumable_uri
            else current.get("resumable_uri")
        )
        update_upload_progress(
            production_id,
            status="failed",
            resumable_uri=saved_uri,
            visibility=visibility,
            error=error,
            api_response=api_response,
            clear_resumable_uri=clear_resumable_uri,
            database_path=self.database_path,
        )

    @staticmethod
    def _build_api(credentials: Any) -> Any:
        from googleapiclient.discovery import build

        return build("youtube", "v3", credentials=credentials, cache_discovery=False)

    @staticmethod
    def _media_upload(path: Path) -> Any:
        from googleapiclient.http import MediaFileUpload

        return MediaFileUpload(str(path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)