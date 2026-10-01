"""Opt-in persistent daily analytics/report scheduler."""

import os
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_database_path
from app.reporting.daily_report import DailyReportService
from app.youtube.repository import claim_job, finish_job

JOB_NAME = "daily-youtube-analytics-report"
AUTO_PUBLISH_JOB_NAME = "youtube-auto-publish"


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
    hour = _read_integer("MOTIVATIONOS_REPORT_HOUR_UTC", 8, 0, 23)
    minute = _read_integer("MOTIVATIONOS_REPORT_MINUTE_UTC", 0, 0, 59)
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_scheduled_report,
        trigger=CronTrigger(hour=hour, minute=minute, timezone="UTC"),
        id=JOB_NAME,
        kwargs={"database_path": database_path or str(get_database_path())},
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    from app.config import get_auto_publish_settings

    settings = get_auto_publish_settings()
    if settings["enabled"]:
        try:
            trigger = CronTrigger.from_crontab(str(settings["cron"]), timezone="UTC")
        except ValueError as error:
            raise ValueError("AUTO_PUBLISH_CRON must be a valid five-field cron expression.") from error
        scheduler.add_job(
            run_scheduled_auto_publish,
            trigger=trigger,
            id=AUTO_PUBLISH_JOB_NAME,
            kwargs={"database_path": database_path or str(get_database_path())},
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
    return scheduler


def run_scheduled_auto_publish(
    *,
    database_path: str | None = None,
    auto_publish_service: Any | None = None,
) -> dict[str, Any]:
    """Run an auto-publish cycle under a restart-safe per-minute DB claim."""
    from app.youtube.auto_publish import AutoPublishService

    path = database_path or str(get_database_path())
    slot = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    if not claim_job(AUTO_PUBLISH_JOB_NAME, slot, path, max_attempts=2):
        return {"status": "already_claimed_or_completed", "run_slot": slot}
    service = auto_publish_service or AutoPublishService(path)
    try:
        result = service.run()
    except Exception as error:
        safe_error = f"{type(error).__name__}: automatic publishing cycle failed."
        finish_job(AUTO_PUBLISH_JOB_NAME, slot, status="failed", error=safe_error, database_path=path)
        return {"status": "failed", "run_slot": slot, "error": safe_error}
    finish_job(AUTO_PUBLISH_JOB_NAME, slot, status="completed", database_path=path)
    return {"status": "completed", "run_slot": slot, "result": result}


def _read_integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}.") from error
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}.")
    return value