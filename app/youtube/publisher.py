"""Human-approved, resumable YouTube Data API uploads."""

import json
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import get_database_path, get_youtube_visibility
from app.database.db import get_production_by_id
from app.schemas.script_schema import ProductionMetadata
from app.video.video_assembler import validate_video
from app.youtube.auth import GoogleOAuthService
from app.youtube.repository import (
    approve_production,
    claim_upload,
    get_approval,
    get_upload,
    request_review,
    update_upload_progress,
)


class YouTubePublishError(RuntimeError):
    """A safe-to-display upload error that never includes credentials."""


class PublishingMetadata(BaseModel):
    """Metadata snapshotted for review and then sent to YouTube."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    language: str = Field(min_length=2, max_length=35)
    category_id: str = Field(pattern=r"^\d{1,3}$")
    publishing_notes: str = Field(default="", max_length=1000)

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

    def approve(
        self,
        production_id: str,
        *,
        visibility: str | None = None,
        confirm_public: bool = False,
    ) -> dict[str, Any]:
        """Record explicit human approval and the exact visibility choice."""
        approval = get_approval(production_id, self.database_path)
        if approval is None or approval["state"] != "awaiting_review":
            raise YouTubePublishError("Publishing approval is only accepted from awaiting_review.")
        selected_visibility = visibility or approval["visibility"]
        if selected_visibility == "public" and not confirm_public:
            raise YouTubePublishError(
                "Public visibility requires confirm_public=true in the explicit approval request."
            )
        metadata = PublishingMetadata.model_validate(approval["metadata"])
        return approve_production(
            production_id,
            selected_visibility,
            metadata.model_dump(mode="json"),
            self.database_path,
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
            raise YouTubePublishError(str(error)) from error
        try:
            upload = claim_upload(production_id, self.database_path)
        except ValueError as error:
            raise YouTubePublishError(str(error)) from error

        request: Any | None = None
        try:
            service = self.api_builder(credentials)
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
                },
            }
            media_body = self.media_upload_factory(video_path)
            request = service.videos().insert(
                part="snippet,status", body=body, media_body=media_body
            )
            if upload.get("resumable_uri"):
                request.resumable_uri = self.oauth.decrypt_persistent_secret(
                    upload["resumable_uri"]
                )
            response = None
            while response is None:
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
            video_id = response.get("id") if isinstance(response, dict) else None
            if not video_id:
                raise YouTubePublishError(
                    "YouTube returned no video ID; upload remains failed and will not be restarted automatically."
                )
            final_status = "uploaded_private" if approval["visibility"] == "private" else "uploaded"
            return update_upload_progress(
                production_id,
                status=final_status,
                video_id=video_id,
                api_response=response,
                visibility=approval["visibility"],
                database_path=self.database_path,
            )
        except Exception as error:
            resumable_uri = getattr(request, "resumable_uri", None) if request is not None else None
            http_status = getattr(getattr(error, "resp", None), "status", None)
            if http_status == 401:
                self.oauth.mark_reauthorization_required()
                detail = "Google authorization expired or was revoked; reconnect the YouTube account."
            elif http_status == 403:
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
        if upload["status"] != "failed" or upload.get("resumable_uri"):
            raise YouTubePublishError(
                "Reconciliation is only available after a failed upload without a resumable session."
            )
        try:
            service = self.api_builder(self.oauth.authorized_credentials())
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
            visibility = video.get("status", {}).get("privacyStatus", upload["visibility"])
            final_status = "uploaded_private" if visibility == "private" else "uploaded"
            saved = update_upload_progress(
                production_id,
                status=final_status,
                video_id=video["id"],
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
        return production

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
                    "category_id": normalized.category_id,
                    "publishing_notes": normalized.publishing_notes,
                }
            )
        except Exception as error:
            raise YouTubePublishError("Publishing metadata exceeds YouTube limits or is incomplete.") from error

    @staticmethod
    def _video_path(production: dict[str, Any]) -> Path:
        video = production.get("metadata", {}).get("video", {})
        raw_path = video.get("path") if isinstance(video, dict) else None
        if not raw_path:
            raise YouTubePublishError("Production has no validated MP4 path in its record.")
        root = Path(production["output_dir"]).resolve()
        path = Path(raw_path).resolve()
        if root not in path.parents or path.suffix.lower() != ".mp4" or not path.is_file():
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
    ) -> None:
        current = get_upload(production_id, self.database_path)
        if current is None:
            return
        saved_uri = (
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