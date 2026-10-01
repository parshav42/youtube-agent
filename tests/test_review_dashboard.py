"""Offline integration checks for the local script review dashboard."""

from fastapi.testclient import TestClient

from app.api import create_app


def test_review_dashboard_serves_static_assets_without_weakening_api_auth(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("MOTIVATIONOS_ADMIN_TOKEN", "dashboard-test-token")
    monkeypatch.setenv("MOTIVATIONOS_SCHEDULER_ENABLED", "false")
    client = TestClient(create_app(tmp_path / "dashboard.sqlite3"))

    with client:
        page = client.get("/review")
        stylesheet = client.get("/review-assets/review.css")
        script = client.get("/review-assets/review.js")

    assert page.status_code == 200
    assert "Script Review | MotivationOS" in page.text
    assert "/review-assets/review.css" in page.text
    assert "/review-assets/review.js" in page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert stylesheet.status_code == 200
    assert "@media (max-width: 760px)" in stylesheet.text
    assert script.status_code == 200
    assert "confirm_review: true" in script.text

    unauthorized = client.get("/api/scripts/review")
    assert unauthorized.status_code == 401