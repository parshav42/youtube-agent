import json

from app.database.db import _connection, record_report_sent
from app.reporting.daily_report import render_html_report


def test_html_report_has_required_sections_and_escapes_data():
    html = render_html_report({
        "report_date": "2026-10-02", "scripts_generated": [{"id": 2, "status": "needs_review", "rejection_reason": "<bad>"}],
        "videos_produced": [{"production_id": "p1", "topic": "<&", "production_status": "complete", "upload_status": "manual"}],
        "analytics": {"channel": {"rows": []}, "videos": []}, "items_waiting_for_review": {"total": 1},
        "uploads_succeeded": 0, "provider": {"provider": "groq", "status": "configured"},
        "quota_usage": {}, "failed_operations": [], "recommended_technical_next_steps": [],
    })
    for section in ("MotivationOS Daily Report", "Today's Pipeline Activity", "YouTube Analytics", "Queue Status", "System Health", "Action Items"):
        assert section in html
    assert "&lt;bad&gt;" in html and "<&" not in html


def test_report_history_records_sent_status(tmp_path):
    path = tmp_path / "reports.sqlite3"
    summary = json.dumps({"ok": True})
    record_report_sent("2026-10-02T10:00:00+00:00", "to@example.test", "sent", None, summary, path)
    with _connection(path) as connection:
        row = connection.execute("SELECT recipient, status, summary_json FROM report_history").fetchone()
    assert row["recipient"] == "to@example.test"
    assert row["status"] == "sent"
    assert json.loads(row["summary_json"]) == {"ok": True}
