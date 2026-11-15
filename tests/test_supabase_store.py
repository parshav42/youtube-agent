from pathlib import Path
from typing import Any

import pytest
import requests

from app.supabase_store import SupabaseStore


class FakeResponse:
    def __init__(self, payload: Any = None) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return

    def json(self) -> Any:
        return self.payload


def test_from_env_requires_both_supabase_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("supabase_url", "https://example.supabase.co")
    monkeypatch.setenv("supabase_pub", "publishable-key")
    monkeypatch.setenv("supabase_sec", "")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    with pytest.raises(RuntimeError, match="SUPABASE_SECRET_KEY"):
        SupabaseStore.from_env()


def test_from_env_uses_supabase_secret_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("supabase_url", "https://example.supabase.co")
    monkeypatch.setenv("supabase_pub", "publishable-key")
    monkeypatch.setenv("supabase_sec", "secret-key")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    store = SupabaseStore.from_env()

    assert store is not None
    assert store.url == "https://example.supabase.co"
    assert store.secret_key == "secret-key"


def test_save_and_read_video_job(monkeypatch: pytest.MonkeyPatch) -> None:
    requests_made: list[dict[str, Any]] = []
    row = {
        "id": "job-1",
        "idea": "A story",
        "status": "running",
        "stage": "images",
        "message": "Creating images",
        "error": None,
        "result": None,
    }

    def fake_request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        requests_made.append({"method": method, "url": url, **kwargs})
        return FakeResponse([row])

    monkeypatch.setattr(requests, "request", fake_request)
    store = SupabaseStore("https://example.supabase.co/", "service-key")
    job = {
        "jobId": "job-1",
        "idea": "A story",
        "status": "running",
        "stage": "images",
        "message": "Creating images",
        "error": None,
        "result": None,
    }

    store.save_job(job)
    loaded = store.get_job("job-1")

    assert loaded == job
    assert requests_made[0]["method"] == "POST"
    assert requests_made[0]["json"]["id"] == "job-1"
    assert requests_made[0]["headers"]["Authorization"] == "Bearer service-key"
    assert requests_made[1]["url"].endswith("/rest/v1/video_jobs?id=eq.job-1&select=*")


def test_supabase_http_error_includes_api_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ErrorResponse(FakeResponse):
        status_code = 404
        text = ""

        def raise_for_status(self) -> None:
            raise requests.HTTPError("404 Client Error", response=self)

    monkeypatch.setattr(
        requests,
        "request",
        lambda *_args, **_kwargs: ErrorResponse(
            {"message": "Could not find the table 'public.video_jobs' in the schema cache"}
        ),
    )
    store = SupabaseStore("https://example.supabase.co", "service-key")

    with pytest.raises(RuntimeError, match="Could not find the table 'public.video_jobs'"):
        store.save_job(
            {
                "jobId": "job-1",
                "idea": "A story",
                "status": "queued",
                "stage": "dialogue",
                "message": "Waiting",
                "error": None,
                "result": None,
            }
        )


def test_upload_assets_and_create_signed_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run"
    image_dir = run_dir / "images"
    image_dir.mkdir(parents=True)
    video_path = run_dir / "clip.mp4"
    image_path = image_dir / "shot 1.png"
    video_path.write_bytes(b"video")
    image_path.write_bytes(b"image")
    requests_made: list[dict[str, Any]] = []

    def fake_request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        requests_made.append({"method": method, "url": url, **kwargs})
        if "/object/sign/" in url:
            return FakeResponse({"signedURL": "/object/sign/video-assets/job-1/clip.mp4?token=abc"})
        return FakeResponse()

    monkeypatch.setattr(requests, "request", fake_request)
    store = SupabaseStore("https://example.supabase.co", "service-key")

    assets = store.upload_assets("job-1", run_dir)
    signed_url = store.signed_url(assets["clip.mp4"])

    assert assets == {
        "clip.mp4": "supabase://video-assets/job-1/clip.mp4",
        "images/shot 1.png": "supabase://video-assets/job-1/images/shot 1.png",
    }
    assert requests_made[0]["url"].endswith("/storage/v1/object/video-assets/job-1/clip.mp4")
    assert requests_made[0]["timeout"] == 120
    assert requests_made[1]["url"].endswith(
        "/storage/v1/object/video-assets/job-1/images/shot%201.png"
    )
    assert signed_url == (
        "https://example.supabase.co/storage/v1/object/sign/"
        "video-assets/job-1/clip.mp4?token=abc"
    )
