"""Isolated route and asset checks for the local review dashboards."""

from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api import create_app


def test_review_dashboard_serves_static_assets_without_weakening_api_auth(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("MOTIVATIONOS_ADMIN_TOKEN", "dashboard-test-token")
    monkeypatch.setenv("MOTIVATIONOS_SCHEDULER_ENABLED", "false")
    application = create_app(tmp_path / "dashboard.sqlite3")
    page_route = next(route for route in application.routes if getattr(route, "path", None) == "/review")
    page = page_route.endpoint()
    assert page.status_code == 200
    page_html = Path(page.path).read_text(encoding="utf-8")
    assert "Script Review | MotivationOS" in page_html
    assert "/review-assets/review.css" in page_html
    assert "/review-assets/review.js" in page_html
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]

    static = Path(__file__).resolve().parents[1] / "app" / "static"
    stylesheet = (static / "review.css").read_text(encoding="utf-8")
    script = (static / "review.js").read_text(encoding="utf-8")
    assert "@media (max-width: 760px)" in stylesheet
    assert "confirm_review: true" in script
    assert ".textContent" in script

    review_route = next(route for route in application.routes if getattr(route, "path", None) == "/api/scripts/review")
    require_admin = review_route.dependant.dependencies[0].call
    with pytest.raises(HTTPException) as unauthorized:
        require_admin(credentials=None)
    assert unauthorized.value.status_code == 401
    assert require_admin(credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials="dashboard-test-token")) is None


def test_production_review_page_is_local_and_cannot_upload(tmp_path) -> None:
    application = create_app(tmp_path / "production-dashboard.sqlite3")
    page_route = next(route for route in application.routes if getattr(route, "path", None) == "/production-review")
    response = page_route.endpoint()
    html = Path(response.path).read_text(encoding="utf-8")
    script = (Path(__file__).resolve().parents[1] / "app" / "static" / "production-review.js").read_text(encoding="utf-8")
    assert response.status_code == 200
    assert "Final video review" in html
    assert "Audio QA" in html and "Video QA" in html
    assert "Approve for publishing workflow" in html
    assert "/upload" not in html + script
    assert ".textContent" in script


def test_audio_review_dashboard_renders_findings_safely_and_separates_decisions() -> None:
    static = Path(__file__).resolve().parents[1] / "app" / "static"
    html = (static / "production-review.html").read_text(encoding="utf-8")
    script = (static / "production-review.js").read_text(encoding="utf-8")

    assert 'id="audio-review"' in html
    assert 'id="audio-status"' in html
    assert 'id="audio-findings"' in html
    assert 'id="audio-review-required"' in html
    assert 'id="audio-decision-metadata"' in html
    assert 'id="audio-approve"' in html and 'id="audio-reject"' in html
    assert 'audioStatus === "needs_review" && !decision' in script
    assert 'data.status === "needs_review"' in script
    assert "item.textContent = String(finding)" in script
    assert "innerHTML" not in script and "localStorage" not in script
    assert "confirm_review: true, reviewer" in script
    assert "This does not approve production or publish." in script
    assert "response.status === 401" in script
    assert "response.status === 404" in script
    assert "response.status === 409" in script
    assert "response.status >= 500" in script
    assert 'id="signout"' in html and 'id="refresh-review"' in html
    assert 'rel="stylesheet" href="/review-assets/review.css"' in html
