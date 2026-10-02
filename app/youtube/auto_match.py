"""Read-only matching of recent channel uploads to ready productions."""

from pathlib import Path
from typing import Any

from app.config import get_database_path
from app.youtube.auth import GoogleOAuthService
from app.youtube.repository import get_ready_manual_uploads, link_manual_upload


class YouTubeAutoMatcher:
    """Poll the channel uploads playlist and link unique exact-title matches."""

    def __init__(
        self,
        database_path: str | Path | None = None,
        *,
        oauth: GoogleOAuthService | None = None,
        api_builder: Any | None = None,
    ) -> None:
        self.database_path = database_path or get_database_path()
        self.oauth = oauth or GoogleOAuthService(str(self.database_path))
        self.api_builder = api_builder or self._build_api

    def run(self) -> dict[str, Any]:
        pending = _unique_title_map(get_ready_manual_uploads(self.database_path))
        if not pending:
            return {"status": "completed", "matched": []}
        service = self.api_builder(self.oauth.authorized_credentials())
        channels = service.channels().list(
            part="contentDetails", mine=True, maxResults=1
        ).execute(num_retries=2)
        channel_items = channels.get("items", []) if isinstance(channels, dict) else []
        try:
            playlist_id = channel_items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
        except (IndexError, KeyError, TypeError) as error:
            raise RuntimeError("The authenticated channel uploads playlist was unavailable.") from error

        matched = []
        page_token = None
        while pending:
            request = service.playlistItems().list(
                part="snippet,contentDetails",
                playlistId=playlist_id,
                maxResults=50,
                **({"pageToken": page_token} if page_token else {}),
            )
            page = request.execute(num_retries=2)
            for item in page.get("items", []):
                snippet = item.get("snippet", {})
                resource = snippet.get("resourceId", {})
                video_id = resource.get("videoId") or item.get("contentDetails", {}).get("videoId")
                candidates = pending.get(_normalize_title(snippet.get("title", "")), [])
                if not video_id or len(candidates) != 1:
                    continue
                production_id = candidates[0]
                upload = link_manual_upload(production_id, video_id, self.database_path)
                matched.append(
                    {"production_id": production_id, "video_id": video_id, "video_url": upload["video_url"]}
                )
                del pending[_normalize_title(snippet["title"])]
            page_token = page.get("nextPageToken")
            if not page_token:
                break
        return {"status": "completed", "matched": matched}

    @staticmethod
    def _build_api(credentials: Any) -> Any:
        from googleapiclient.discovery import build

        return build("youtube", "v3", credentials=credentials, cache_discovery=False)


def _unique_title_map(productions: list[dict[str, Any]]) -> dict[str, list[str]]:
    title_map: dict[str, list[str]] = {}
    for production in productions:
        generated = production["metadata"].get("youtube_metadata", {})
        title = generated.get("title") if isinstance(generated, dict) else None
        if isinstance(title, str) and title.strip():
            title_map.setdefault(_normalize_title(title), []).append(production["production_id"])
    return title_map


def _normalize_title(title: str) -> str:
    return " ".join(title.split()).casefold()