"""Opt-in persistent daily analytics/report scheduler."""

import os
import logging
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import (
    get_auto_publish_settings,
    get_database_path,
    get_youtube_auto_match_enabled,
    get_report_schedule_cron,
    is_report_enabled,
)
from app.reporting.daily_report import DailyReportService
from app.youtube.repository import claim_job, finish_job

JOB_NAME = "daily-youtube-analytics-report"
DAILY_EMAIL_REPORT_JOB_NAME = "daily_email_report"
AUTO_PUBLISH_JOB_NAME = "youtube-auto-publish"
AUTO_MATCH_JOB_NAME = "youtube-upload-auto-match"
DAILY_PIPELINE_JOB_NAME = "motivationos-daily-pipeline"
logger = logging.getLogger(__name__)
_scheduler_instance = None


def run_scheduled_email_report(*, database_path: str | None = None, retry: bool = False) -> dict[str, Any]:
    """Run one local-date report, allowing one delayed retry after SMTP failure."""
    path = database_path or str(get_database_path())
    if not is_report_enabled():
        return {"status": "disabled"}
    service = DailyReportService(path)
    try:
        result = service.run()
    except Exception as error:
        result = {"email": {"sent": False, "status": "failed", "detail": f"{type(error).__name__}: report generation failed"}}
    email = result.get("email", {})
    if not email.get("sent") and not retry and _scheduler_instance is not None:
        from datetime import timedelta
        _scheduler_instance.add_job(
            run_scheduled_email_report,
            trigger="date", run_date=datetime.now(timezone.utc) + timedelta(minutes=5),
            id=f"{DAILY_EMAIL_REPORT_JOB_NAME}_retry", replace_existing=True,
            kwargs={"database_path": path, "retry": True},
        )
        logger.error("Daily email report failed; one retry scheduled in 5 minutes.")
    elif not email.get("sent"):
        logger.error("Daily email report retry failed; recipient=%s", __import__("app.config", fromlist=["get_report_email_to"]).get_report_email_to())
    return {"status": "completed" if email.get("sent") else "failed", "result": result}


def run_scheduled_report(
    *,
    database_path: str | None = None,
    report_service: DailyReportService | None = None,
) -> dict[str, Any]:
    path = database_path or str(get_database_path())
    report_date = datetime.now(timezone.utc).date().isoformat()
    service = report_service or DailyReportService(path)
    for attempt in range(3):
        if not claim_job(JOB_NAME, report_date, path, max_attempts=3):
            return {"status": "already_claimed_or_completed", "run_date": report_date}
        try:
            result = service.run(datetime.fromisoformat(report_date).date())
        except Exception as error:
            safe_error = f"{type(error).__name__}: daily report operation failed."
            finish_job(JOB_NAME, report_date, status="failed", error=safe_error, database_path=path)
            if attempt == 2:
                return {"status": "failed", "run_date": report_date, "error": safe_error}
            continue
        finish_job(JOB_NAME, report_date, status="completed", database_path=path)
        return {"status": "completed", "run_date": report_date, "result": result}
    return {"status": "failed", "run_date": report_date}


def build_scheduler(database_path: str | None = None) -> BackgroundScheduler:
    global _scheduler_instance
    scheduler = BackgroundScheduler(timezone="UTC")
    if is_report_enabled():
        try:
            report_trigger = CronTrigger.from_crontab(get_report_schedule_cron(), timezone="UTC")
        except ValueError as error:
            raise ValueError("REPORT_SCHEDULE_CRON must be a valid five-field UTC cron expression.") from error
        scheduler.add_job(
            run_scheduled_email_report, trigger=report_trigger,
            id=DAILY_EMAIL_REPORT_JOB_NAME,
            kwargs={"database_path": database_path or str(get_database_path())},
            replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=3600,
        )
    _scheduler_instance = scheduler
    auto_settings = get_auto_publish_settings()
    from app.config import get_daily_pipeline_settings
    pipeline_cron = str(get_daily_pipeline_settings()["cron"])
    if pipeline_cron:
        try:
            pipeline_trigger = CronTrigger.from_crontab(pipeline_cron, timezone="UTC")
        except ValueError as error:
            raise ValueError("DAILY_PIPELINE_CRON must be a valid five-field cron expression.") from error
        scheduler.add_job(
            run_scheduled_daily_pipeline, trigger=pipeline_trigger,
            id=DAILY_PIPELINE_JOB_NAME,
            kwargs={"database_path": database_path or str(get_database_path())},
            replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=3600,
        )
    if auto_settings["enabled"]:
        try:
            auto_trigger = CronTrigger.from_crontab(
                str(auto_settings["cron"]), timezone="UTC"
            )
        except ValueError as error:
            raise ValueError("AUTO_PUBLISH_CRON must be a valid five-field cron expression.") from error
        scheduler.add_job(
            run_scheduled_auto_publish,
            trigger=auto_trigger,
            id=AUTO_PUBLISH_JOB_NAME,
            kwargs={"database_path": database_path or str(get_database_path())},
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
    if get_youtube_auto_match_enabled():
        match_trigger = CronTrigger.from_crontab(
            os.getenv("YOUTUBE_AUTO_MATCH_CRON", "*/15 * * * *"), timezone="UTC"
        )
        scheduler.add_job(
            run_scheduled_auto_match,
            trigger=match_trigger,
            id=AUTO_MATCH_JOB_NAME,
            kwargs={"database_path": database_path or str(get_database_path())},
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
    return scheduler


def run_scheduled_daily_pipeline(*, database_path: str | None = None) -> dict[str, Any]:
    """Run and log one opt-in full pipeline attempt; upload remains manual."""
    path = database_path or str(get_database_path())
    run_key = datetime.now(timezone.utc).isoformat()
    try:
        from app.daily_pipeline import run_daily_pipeline
        result = run_daily_pipeline(path)
    except Exception as error:
        logger.exception("Scheduled daily pipeline failed timestamp=%s", run_key)
        return {"status": "failed", "timestamp": run_key, "error": str(error)}
    logger.info("Scheduled daily pipeline completed timestamp=%s production_id=%s", run_key, result["production_id"])
    return {"status": "completed", "timestamp": run_key, "result": result}


def run_scheduled_auto_publish(
    *,
    database_path: str | None = None,
    auto_publish_service: Any | None = None,
) -> dict[str, Any]:
    """Run one durable, idempotent auto-publish cycle for the current UTC minute."""
    settings = get_auto_publish_settings()
    if not settings["enabled"]:
        return {"status": "disabled", "uploads_attempted": 0, "uploads_succeeded": 0}
    path = database_path or str(get_database_path())
    run_key = datetime.now(timezone.utc).replace(second=0, microsecond=0).isoformat()
    if not claim_job(AUTO_PUBLISH_JOB_NAME, run_key, path, max_attempts=3):
        return {"status": "already_claimed_or_completed", "run_date": run_key}
    try:
        if auto_publish_service is None:
            from app.youtube.auto_publish import AutoPublishService

            auto_publish_service = AutoPublishService(path)
        result = auto_publish_service.run()
    except Exception as error:
        safe_error = f"{type(error).__name__}: automatic publish operation did not complete."
        finish_job(AUTO_PUBLISH_JOB_NAME, run_key, status="failed", error=safe_error, database_path=path)
        return {"status": "failed", "run_date": run_key, "error": safe_error}
    finish_job(AUTO_PUBLISH_JOB_NAME, run_key, status="completed", database_path=path)
    return {"status": "completed", "run_date": run_key, "result": result}


def run_scheduled_auto_match(
    *,
    database_path: str | None = None,
    auto_matcher: Any | None = None,
) -> dict[str, Any]:
    """Run one durable optional poll of the YouTube uploads playlist."""
    if not get_youtube_auto_match_enabled():
        return {"status": "disabled", "matched": []}
    path = database_path or str(get_database_path())
    run_key = datetime.now(timezone.utc).replace(second=0, microsecond=0).isoformat()
    if not claim_job(AUTO_MATCH_JOB_NAME, run_key, path, max_attempts=3):
        return {"status": "already_claimed_or_completed", "run_date": run_key}
    try:
        if auto_matcher is None:
            from app.youtube.auto_match import YouTubeAutoMatcher

            auto_matcher = YouTubeAutoMatcher(path)
        result = auto_matcher.run()
    except Exception as error:
        safe_error = f"{type(error).__name__}: YouTube auto-match operation did not complete."
        finish_job(AUTO_MATCH_JOB_NAME, run_key, status="failed", error=safe_error, database_path=path)
        return {"status": "failed", "run_date": run_key, "error": safe_error}
    finish_job(AUTO_MATCH_JOB_NAME, run_key, status="completed", database_path=path)
    return {"status": "completed", "run_date": run_key, "result": result}


def _read_integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}.") from error
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}.")
    return value
