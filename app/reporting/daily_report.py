"""Actual daily production, upload, analytics, and operational reporting."""

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import get_database_path, get_report_output_path
from app.database.db import _connection
from app.reporting.email_service import EmailDeliveryError, SMTPEmailService
from app.youtube.analytics import YouTubeAnalyticsService, YouTubeAnalyticsError
from app.youtube.repository import (
    get_quota_usage,
    get_productions_for_report,
    get_review_queue_counts,
    get_report_history,
    get_uploads_for_report,
    save_report,
)


class DailyReportService:
    """Persist a report first, then deliver it through configured SMTP."""

    def __init__(
        self,
        database_path: str | Path | None = None,
        output_directory: str | Path | None = None,
        *,
        analytics: YouTubeAnalyticsService | None = None,
        email: SMTPEmailService | None = None,
    ) -> None:
        self.database_path = database_path or get_database_path()
        self.output_directory = Path(output_directory) if output_directory else get_report_output_path()
        self.analytics = analytics or YouTubeAnalyticsService(self.database_path)
        self.email = email or SMTPEmailService()

    def run(self, report_date: date | None = None) -> dict[str, Any]:
        target_date = report_date or datetime.now(timezone.utc).date()
        day_start = datetime.combine(target_date, time.min, timezone.utc).isoformat()
        day_end = datetime.combine(target_date + timedelta(days=1), time.min, timezone.utc).isoformat()
        productions = get_productions_for_report(day_start, day_end, self.database_path)
        uploads = get_uploads_for_report(day_start, day_end, self.database_path)
        quota_usage = get_quota_usage(
            database_path=self.database_path,
            quota_date=(
                datetime.fromisoformat(day_end) - timedelta(microseconds=1)
            ).astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat(),
        )
        review_queue = get_review_queue_counts(self.database_path)
        analytics_start = target_date - timedelta(days=28)
        analytics_end = target_date - timedelta(days=1)
        analytics_result: dict[str, Any] = {
            "retrieved": False,
            "retrieval_date": datetime.now(timezone.utc).date().isoformat(),
            "start_date": analytics_start.isoformat(),
            "end_date": analytics_end.isoformat(),
            "channel": None,
            "videos": [],
            "errors": [],
        }
        try:
            analytics_result["channel"] = self.analytics.channel_report(
                analytics_start.isoformat(), analytics_end.isoformat()
            )
            analytics_result["retrieved"] = True
        except Exception as error:
            analytics_result["errors"].append(_safe_error(error))
        try:
            analytics_result["videos"] = self.analytics.collect_uploaded_videos(
                analytics_start.isoformat(), analytics_end.isoformat()
            )
        except Exception as error:
            analytics_result["errors"].append(_safe_error(error))

        failed_operations = []
        for production in productions:
            if production["production_status"] == "failed":
                failed_operations.append(
                    {"type": "production", "production_id": production["id"], "error": production["production_error"]}
                )
            if production["upload_status"] == "failed":
                failed_operations.append(
                    {"type": "youtube_upload", "production_id": production["id"], "error": production["upload_error"]}
                )
            automatic_review = production["metadata"].get("auto_approval")
            if isinstance(automatic_review, dict) and automatic_review.get("status") == "needs_review":
                failed_operations.append({
                    "type": "auto_approval",
                    "production_id": production["id"],
                    "error": "; ".join(automatic_review.get("reasons", [])),
                })
        for upload in uploads:
            if upload["status"] == "failed" and not any(
                item.get("production_id") == upload["production_id"]
                and item["type"] == "youtube_upload"
                for item in failed_operations
            ):
                failed_operations.append({
                    "type": "youtube_upload",
                    "production_id": upload["production_id"],
                    "error": upload["error"],
                })
        for video in analytics_result["videos"]:
            if not video.get("retrieved", True):
                failed_operations.append(
                    {"type": "youtube_analytics", "video_id": video["video_id"], "error": video.get("error")}
                )
        failed_operations.extend(
            {"type": "youtube_analytics", "error": error}
            for error in analytics_result["errors"]
        )
        report = {
            "report_date": target_date.isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "production_window_utc": {"start": day_start, "end_exclusive": day_end},
            "videos_produced": [
                {
                    "production_id": row["id"],
                    "topic": row["topic"],
                    "production_status": row["production_status"],
                    "upload_status": row["upload_status"],
                    "visibility": row["visibility"],
                    "video_id": row["video_id"],
                    "video_url": f"https://www.youtube.com/watch?v={row['video_id']}" if row["video_id"] else None,
                    "production_error": row["production_error"],
                    "upload_error": row["upload_error"],
                }
                for row in productions
            ],
            "uploads_attempted": quota_usage["uploads_attempted"],
            "uploads_succeeded": sum(
                1 for upload in uploads if upload["status"] in {"uploaded", "uploaded_private", "manual"}
            ),
            "public_video_urls": [
                upload["video_url"]
                for upload in uploads
                if upload["status"] in {"uploaded", "manual"}
                and upload["visibility"] == "public"
                and upload["video_url"]
            ],
            "quota_usage": quota_usage,
            "items_waiting_for_review": review_queue,
            "analytics": analytics_result,
            "failed_operations": failed_operations,
            "recommended_technical_next_steps": _recommendations(failed_operations, analytics_result),
        }
        markdown = _render_markdown(report)
        self.output_directory.mkdir(parents=True, exist_ok=True)
        report_path = self.output_directory / f"daily-report-{target_date.isoformat()}.md"
        temporary_path = report_path.with_suffix(".md.tmp")
        temporary_path.write_text(markdown, encoding="utf-8")
        temporary_path.replace(report_path)

        existing = get_report_history(target_date.isoformat(), self.database_path)
        saved = save_report(
            target_date.isoformat(),
            str(report_path.resolve()),
            report,
            "sent" if existing and existing["email_status"] == "sent" else "pending",
            database_path=self.database_path,
        )
        if saved["email_status"] == "sent":
            return {"report": report, "report_path": str(report_path), "email": {"sent": True, "status": "already_sent"}}
        if existing and existing["email_status"] in {"sending", "delivery_unknown"}:
            result = {"sent": False, "status": "delivery_unknown", "detail": "Previous send outcome is ambiguous; automatic resend blocked."}
            save_report(target_date.isoformat(), str(report_path.resolve()), report, "delivery_unknown", result["detail"], self.database_path)
            return {"report": report, "report_path": str(report_path), "email": result}
        email_status = "sending"
        save_report(target_date.isoformat(), str(report_path.resolve()), report, email_status, database_path=self.database_path)
        try:
            email_result = self.email.send(
                subject=f"MotivationOS daily report: {target_date.isoformat()}", body=markdown
            )
            email_status = str(email_result["status"])
            email_error = None
        except EmailDeliveryError as error:
            email_result = {"sent": False, "status": "delivery_unknown", "detail": str(error)}
            email_status = "delivery_unknown"
            email_error = str(error)
        save_report(
            target_date.isoformat(),
            str(report_path.resolve()),
            report,
            email_status,
            email_error,
            self.database_path,
        )
        return {"report": report, "report_path": str(report_path), "email": email_result}


def _safe_error(error: Exception) -> str:
    if isinstance(error, YouTubeAnalyticsError):
        return str(error)
    return f"{type(error).__name__}: authorized analytics operation did not complete."


def _recommendations(failures: list[dict[str, Any]], analytics: dict[str, Any]) -> list[str]:
    recommendations = []
    if any(row["type"] == "youtube_upload" for row in failures):
        recommendations.append("Review the saved YouTube resumable session before retrying an upload.")
    if any(row["type"] == "youtube_analytics" for row in failures) or not analytics["retrieved"]:
        recommendations.append("Check Google OAuth status, enabled APIs, channel permissions, date range, and quota.")
    if not analytics["retrieved"]:
        recommendations.append("Analytics values are unavailable; this report does not substitute estimated values.")
    if not recommendations:
        recommendations.append("No technical follow-up is indicated by the recorded operations.")
    return recommendations


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# MotivationOS daily report: {report['report_date']}",
        "",
        f"Generated at: {report['generated_at']}",
        f"Analytics retrieved at: {report['analytics']['retrieval_date']}",
        f"Analytics data window: {report['analytics']['start_date']} through {report['analytics']['end_date']}",
        "",
        "## Productions and uploads",
        f"Uploads attempted: {report['uploads_attempted']}",
        f"Uploads succeeded: {report['uploads_succeeded']}",
        f"Quota units used: {report['quota_usage']['quota_units_used']}",
        f"Items waiting for review: {report['items_waiting_for_review']['total']}",
    ]
    if report["videos_produced"]:
        for video in report["videos_produced"]:
            link = video["video_url"] or "No YouTube video ID"
            lines.append(
                f"- {video['production_id']} | {video['topic']} | production={video['production_status']} "
                f"| upload={video['upload_status'] or 'not submitted'} | visibility={video['visibility'] or 'n/a'} | {link}"
            )
    else:
        lines.append("- No production records were created in this UTC reporting window.")
    lines.extend(["", "## Public video URLs"])
    lines.extend(f"- {url}" for url in report["public_video_urls"])
    if not report["public_video_urls"]:
        lines.append("- No public uploads were completed in this reporting window.")
    lines.extend(["", "## Available analytics"])
    channel = report["analytics"]["channel"]
    if channel and channel["metrics_available"]:
        for row in channel["rows"]:
            lines.append(f"- Channel {row['date']}: {json.dumps(row['metrics'], sort_keys=True)}")
    else:
        lines.append("- No channel metrics were returned for the selected date range.")
    for video in report["analytics"]["videos"]:
        if video.get("metrics_available"):
            for row in video["rows"]:
                lines.append(f"- Video {video['video_id']} on {row['date']}: {json.dumps(row['metrics'], sort_keys=True)}")
        else:
            lines.append(f"- Video {video['video_id']}: metrics not available in the returned report.")
    lines.extend(["", "## Failed operations"])
    if report["failed_operations"]:
        for failure in report["failed_operations"]:
            lines.append(f"- {failure['type']}: {failure.get('production_id', failure.get('video_id', 'channel'))}: {failure.get('error')}")
    else:
        lines.append("- None recorded.")
    lines.extend(["", "## Recommended technical next steps"])
    lines.extend(f"- {item}" for item in report["recommended_technical_next_steps"])
    lines.append("")
    return "\n".join(lines)