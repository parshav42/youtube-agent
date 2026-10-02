from app.reporting.scheduler import DAILY_EMAIL_REPORT_JOB_NAME, build_scheduler


def test_daily_report_job_uses_stable_id_and_1530_utc(monkeypatch, tmp_path):
    monkeypatch.setattr("app.reporting.scheduler.is_report_enabled", lambda: True)
    monkeypatch.setattr("app.reporting.scheduler.get_report_schedule_cron", lambda: "30 15 * * *")
    monkeypatch.setattr("app.reporting.scheduler.get_auto_publish_settings", lambda: {"enabled": False})
    monkeypatch.setattr("app.reporting.scheduler.get_youtube_auto_match_enabled", lambda: False)
    monkeypatch.setattr("app.config.get_database_path", lambda: tmp_path / "db.sqlite3")
    scheduler = build_scheduler(str(tmp_path / "db.sqlite3"))
    job = scheduler.get_job(DAILY_EMAIL_REPORT_JOB_NAME)
    assert job is not None
    assert job.id == "daily_email_report"
    assert str(job.trigger) == "cron[month='*', day='*', day_of_week='*', hour='15', minute='30']"
