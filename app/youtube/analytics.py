"""Authorized YouTube Analytics API queries and metric persistence."""

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.config import get_database_path
from app.youtube.auth import GoogleOAuthService
from app.youtube.repository import (
    get_analytics,
    initialize_youtube_database,
    save_analytics,
)

ANALYTICS_METRICS = (
    "views,likes,comments,estimatedMinutesWatched,averageViewDuration,"
    "averageViewPercentage,subscribersGained"
)
CHANNEL_KEY = "__channel__"


class YouTubeAnalyticsError(RuntimeError):
    """Raised when authorized Analytics API data cannot be retrieved."""


class YouTubeAnalyticsService:
    """Query reports for the authenticated channel and uploaded videos."""

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

    def channel_report(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        start, end = _validate_dates(start_date, end_date)
        service = self._authorized_api()
        try:
            result = service.reports().query(
                ids="channel==MINE",
                startDate=start.isoformat(),
                endDate=end.isoformat(),
                metrics=ANALYTICS_METRICS,
                dimensions="day",
                sort="day",
            ).execute(num_retries=2)
        except Exception as error:
            self._record_auth_failure(error)
            raise _analytics_error(error) from error
        records = _parse_rows(result, has_video_dimension=False)
        persisted = _storage_rows(records, CHANNEL_KEY)
        save_analytics(persisted, self.database_path)
        return {
            "scope": "authenticated_channel",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "retrieved": True,
            "metrics_available": bool(records),
            "rows": records,
        }

    def video_report(
        self,
        video_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        if not video_id.strip():
            raise ValueError("video_id must not be blank.")
        start, end = _validate_dates(start_date, end_date)
        service = self._authorized_api()
        try:
            result = service.reports().query(
                ids="channel==MINE",
                startDate=start.isoformat(),
                endDate=end.isoformat(),
                metrics=ANALYTICS_METRICS,
                dimensions="day",
                filters=f"video=={video_id}",
                sort="day",
            ).execute(num_retries=2)
        except Exception as error:
            self._record_auth_failure(error)
            raise _analytics_error(error) from error
        records = _parse_rows(result, has_video_dimension=False)
        persisted = _storage_rows(records, video_id)
        save_analytics(persisted, self.database_path)
        return {
            "video_id": video_id,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "retrieved": True,
            "metrics_available": bool(records),
            "rows": records,
        }

    def stored_video_report(
        self,
        video_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        start, end = _validate_dates(start_date, end_date)
        rows = get_analytics(
            video_id=video_id,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            database_path=self.database_path,
        )
        return {
            "video_id": video_id,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "source": "stored_youtube_analytics_api_values",
            "metrics_available": bool(rows),
            "rows": rows,
        }

    def collect_uploaded_videos(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch data for persisted uploads only; errors remain per-video and visible."""
        from app.database.db import _connection

        initialize_youtube_database(self.database_path)
        start, end = _validate_dates(start_date, end_date)
        with _connection(self.database_path) as connection:
            rows = connection.execute(
                "SELECT video_id FROM youtube_uploads WHERE video_id IS NOT NULL ORDER BY uploaded_at"
            ).fetchall()
        reports = []
        for row in rows:
            video_id = row["video_id"]
            try:
                reports.append(self.video_report(video_id, start.isoformat(), end.isoformat()))
            except YouTubeAnalyticsError as error:
                reports.append(
                    {
                        "video_id": video_id,
                        "start_date": start.isoformat(),
                        "end_date": end.isoformat(),
                        "retrieved": False,
                        "metrics_available": False,
                        "error": str(error),
                        "rows": [],
                    }
                )
        return reports

    def _authorized_api(self) -> Any:
        credentials = self.oauth.authorized_credentials()
        return self.api_builder(credentials)

    def _record_auth_failure(self, error: Exception) -> None:
        if getattr(getattr(error, "resp", None), "status", None) == 401:
            self.oauth.mark_reauthorization_required()

    @staticmethod
    def _build_api(credentials: Any) -> Any:
        from googleapiclient.discovery import build

        return build("youtubeAnalytics", "v2", credentials=credentials, cache_discovery=False)


def _validate_dates(start_date: str | None, end_date: str | None) -> tuple[date, date]:
    end = date.fromisoformat(end_date) if end_date else date.today() - timedelta(days=1)
    start = date.fromisoformat(start_date) if start_date else end - timedelta(days=27)
    if start > end:
        raise ValueError("start_date must be on or before end_date.")
    if end >= date.today():
        raise ValueError("end_date must be before today because YouTube Analytics can lag by a day.")
    return start, end


def _parse_rows(result: dict[str, Any], *, has_video_dimension: bool) -> list[dict[str, Any]]:
    headers = [column["name"] for column in result.get("columnHeaders", [])]
    if not headers:
        return []
    rows = []
    for values in result.get("rows", []):
        mapped = dict(zip(headers, values, strict=True))
        video_id = mapped.pop("video", None) if has_video_dimension else None
        metric_date = mapped.pop("day", None)
        if not metric_date:
            continue
        records = []
        for name, value in mapped.items():
            if value is None:
                continue
            try:
                numeric_value = float(value)
            except (TypeError, ValueError):
                continue
            records.append(
                {
                    "video_id": video_id,
                    "metric_date": metric_date,
                    "metric_name": name,
                    "metric_value": numeric_value,
                }
            )
        by_date: dict[str, dict[str, Any]] = {}
        for record in records:
            data = by_date.setdefault(
                record["metric_date"], {"date": record["metric_date"], "metrics": {}}
            )
            data["metrics"][record["metric_name"]] = record["metric_value"]
        rows.extend(by_date.values())
    return rows


def _storage_rows(rows: list[dict[str, Any]], video_id: str) -> list[dict[str, Any]]:
    return [
        {
            "video_id": video_id,
            "metric_date": row["date"],
            "metric_name": metric_name,
            "metric_value": metric_value,
            "source": "youtube_analytics_api",
        }
        for row in rows
        for metric_name, metric_value in row["metrics"].items()
    ]


def _analytics_error(error: Exception) -> YouTubeAnalyticsError:
    status = getattr(getattr(error, "resp", None), "status", None)
    if status == 401:
        return YouTubeAnalyticsError(
            "YouTube Analytics authorization was rejected; reconnect the account."
        )
    if status == 429:
        return YouTubeAnalyticsError("YouTube Analytics API quota was exceeded; retry after the quota resets.")
    if status == 403:
        return YouTubeAnalyticsError(
            "YouTube Analytics denied access; check channel permission, API enablement, project quota, and restrictions."
        )
    return YouTubeAnalyticsError("YouTube Analytics data could not be retrieved from Google.")