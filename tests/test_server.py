import json
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from app import server, video_generator


@contextmanager
def _running_server() -> Iterator[str]:
    http_server = ThreadingHTTPServer(("127.0.0.1", 0), server.VideoRequestHandler)
    thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{http_server.server_port}"
    finally:
        http_server.shutdown()
        thread.join(timeout=2)
        http_server.server_close()


def test_video_api_allows_preflight_only_for_configured_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed_origin = "https://utubeagent.vercel.app"
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", f"{allowed_origin}/")

    with _running_server() as base_url:
        preflight = Request(
            f"{base_url}/api/videos",
            headers={
                "Origin": allowed_origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
            method="OPTIONS",
        )
        with urlopen(preflight, timeout=3) as response:
            assert response.status == 204
            assert response.headers["Access-Control-Allow-Origin"] == allowed_origin
            assert response.headers["Access-Control-Allow-Methods"] == "GET, POST, OPTIONS"
            assert response.headers["Vary"] == "Origin"

        rejected_preflight = Request(
            f"{base_url}/api/videos",
            headers={"Origin": "https://untrusted.example"},
            method="OPTIONS",
        )
        with pytest.raises(HTTPError) as error:
            urlopen(rejected_preflight, timeout=3)
        assert error.value.code == 403


def test_health_endpoint_returns_ok() -> None:
    with _running_server() as base_url:
        with urlopen(f"{base_url}/health", timeout=3) as response:
            assert response.status == 200
            assert json.loads(response.read()) == {"status": "ok"}


def test_video_job_uses_hugging_face_when_configured_even_if_ollama_is_selected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = "hugging-face-provider-job"
    idea = "A fox finds a hidden door."
    dialogue = "A fox discovers a hidden door beneath an old tree."
    requests_seen: list[tuple[str, dict[str, str], dict[str, object], int]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {"message": {"role": "assistant", "content": dialogue}}
                ]
            }

    def fake_post(
        url: str,
        headers: dict[str, str],
        json: dict[str, object],
        timeout: int,
    ) -> FakeResponse:
        requests_seen.append((url, headers, json, timeout))
        return FakeResponse()

    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("HF_TOKEN", "test-token")
    monkeypatch.setenv("HF_MODEL", "Qwen/Qwen3-4B-Instruct-2507")
    monkeypatch.setenv("HF_BASE_URL", "https://router.huggingface.co/v1")
    monkeypatch.setattr("app.text_generator.requests.post", fake_post)
    monkeypatch.setattr(server, "OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(server, "ElevenLabs", lambda **_kwargs: object())
    monkeypatch.setattr(
        server.SupabaseStore,
        "from_env",
        classmethod(lambda _cls: None),
    )
    monkeypatch.setattr(video_generator.shutil, "which", lambda name: f"/usr/bin/{name}")

    class FakeMediaGenerator:
        def generate_image(
            self,
            _prompt: str,
            path: Path,
            _width: int,
            _height: int,
        ) -> Path:
            path.write_bytes(b"image")
            return path

        def generate_video(
            self,
            _image_path: Path,
            _prompt: str,
            path: Path,
        ) -> Path:
            path.write_bytes(b"animated video")
            return path

    monkeypatch.setattr(
        video_generator,
        "HuggingFaceMediaGenerator",
        FakeMediaGenerator,
    )
    monkeypatch.setattr(
        video_generator,
        "_generate_narration",
        lambda _text, _client, path, _cancel=None: path.write_bytes(b"audio"),
    )
    monkeypatch.setattr(video_generator, "_assemble_video", lambda *_args: None)

    job: server.VideoJob = {
        "jobId": job_id,
        "idea": idea,
        "status": "running",
        "stage": "dialogue",
        "message": "Writing the script...",
        "error": None,
        "result": None,
    }
    monkeypatch.setitem(server._JOBS, job_id, job)
    monkeypatch.setitem(
        server._JOB_CONTROLS,
        job_id,
        server.JobControl(threading.Event(), threading.Lock()),
    )

    server._run_video_job(job_id, idea)

    assert len(requests_seen) == 1
    url, headers, payload, _timeout = requests_seen[0]
    assert url == "https://router.huggingface.co/v1/chat/completions"
    assert "localhost:11434" not in url
    assert headers == {"Authorization": "Bearer test-token"}
    assert payload["model"] == "Qwen/Qwen3-4B-Instruct-2507"
    assert job["status"] == "complete"
    result = job["result"]
    assert result is not None
    dialogue_path = tmp_path / result["outputFolder"] / "dialogue.txt"
    assert dialogue_path.read_text(encoding="utf-8").strip() == dialogue


def test_main_prefers_railway_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_address: list[tuple[str, int]] = []

    class FakeHTTPServer:
        def __init__(self, address: tuple[str, int], _handler: type) -> None:
            server_address.append(address)

        def __enter__(self) -> "FakeHTTPServer":
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def serve_forever(self) -> None:
            pass

    monkeypatch.setattr(server, "ThreadingHTTPServer", FakeHTTPServer)
    monkeypatch.setattr(server, "load_dotenv", lambda _path: None)
    monkeypatch.setenv("APP_HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "12345")
    monkeypatch.setenv("APP_PORT", "8000")

    server.main()

    assert server_address == [("0.0.0.0", 12345)]


def test_video_api_validates_and_tracks_a_job(monkeypatch: pytest.MonkeyPatch) -> None:
    job_finished = threading.Event()
    created_ideas: list[str] = []

    def fake_run_job(job_id: str, idea: str) -> None:
        created_ideas.append(idea)
        result: server.VideoResult = {
            "videoUrl": "/output/video.mp4",
            "dialogueUrl": "/output/dialogue.txt",
            "audioUrl": "/output/narration.mp3",
            "imageUrls": ["/output/images/shot_001.png"],
            "outputFolder": "2026-01-01/video",
        }
        server._update_job(
            job_id,
            status="complete",
            stage="complete",
            message="Video ready.",
            result=result,
        )
        job_finished.set()

    monkeypatch.setattr(server, "_run_video_job", fake_run_job)

    with _running_server() as base_url:
        invalid_request = Request(
            f"{base_url}/api/videos",
            data=b'{"idea":"  "}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as error:
            urlopen(invalid_request, timeout=3)
        assert error.value.code == 400

        request = Request(
            f"{base_url}/api/videos",
            data=json.dumps({"idea": "A fox finds a hidden door."}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            assert response.status == 202
            created_job = json.loads(response.read())
            assert set(created_job) == {
                "jobId",
                "idea",
                "status",
                "stage",
                "message",
                "error",
                "result",
            }
            assert created_job["idea"] == "A fox finds a hidden door."
            assert created_job["status"] in {"queued", "complete"}
            job_id = created_job["jobId"]

        assert job_finished.wait(timeout=3)
        with urlopen(f"{base_url}/api/jobs/{job_id}", timeout=3) as response:
            job = json.loads(response.read())

    assert created_ideas == ["A fox finds a hidden door."]
    assert job["status"] == "complete"
    assert job["result"]["videoUrl"] == "/output/video.mp4"


def test_video_api_cancels_an_active_job(monkeypatch: pytest.MonkeyPatch) -> None:
    job_started = threading.Event()
    job_finished = threading.Event()
    for key in (
        "SUPABASE_URL",
        "supabase_url",
        "SUPABASE_SECRET_KEY",
        "supabase_sec",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_PUBLISHABLE_KEY",
        "supabase_pub",
    ):
        monkeypatch.delenv(key, raising=False)

    def fake_run_job(job_id: str, _idea: str) -> None:
        job_started.set()
        server._JOB_CONTROLS[job_id].cancel_event.wait(timeout=3)
        server._update_job(
            job_id,
            status="cancelled",
            stage="cancelled",
            message="Video generation stopped.",
        )
        job_finished.set()

    monkeypatch.setattr(server, "_run_video_job", fake_run_job)

    with _running_server() as base_url:
        request = Request(
            f"{base_url}/api/videos",
            data=json.dumps({"idea": "A story to cancel"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            job_id = json.loads(response.read())["jobId"]

        assert job_started.wait(timeout=3)
        cancel_request = Request(
            f"{base_url}/api/jobs/{job_id}/cancel",
            data=b"",
            method="POST",
        )
        with urlopen(cancel_request, timeout=3) as response:
            assert response.status == 202
            assert json.loads(response.read())["status"] == "cancelling"

        assert job_finished.wait(timeout=3)
        with urlopen(f"{base_url}/api/jobs/{job_id}", timeout=3) as response:
            job = json.loads(response.read())

    assert job["status"] == "cancelled"


def test_output_server_supports_video_byte_ranges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video_path = tmp_path / "sample.mp4"
    video_path.write_bytes(b"abcdef")
    monkeypatch.setattr(server, "OUTPUT_ROOT", tmp_path)

    with _running_server() as base_url:
        request = Request(
            f"{base_url}/output/sample.mp4",
            headers={"Range": "bytes=1-3"},
        )
        with urlopen(request, timeout=3) as response:
            assert response.status == 206
            assert response.headers["Content-Range"] == "bytes 1-3/6"
            assert response.read() == b"bcd"


def test_job_api_restores_persisted_supabase_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job: server.VideoJob = {
        "jobId": "restored-job",
        "idea": "A story from yesterday.",
        "status": "complete",
        "stage": "complete",
        "message": "Your video is ready.",
        "error": None,
        "result": {
            "videoUrl": "supabase://video-assets/restored-job/video.mp4",
            "dialogueUrl": "supabase://video-assets/restored-job/dialogue.txt",
            "audioUrl": "supabase://video-assets/restored-job/narration.mp3",
            "imageUrls": ["supabase://video-assets/restored-job/images/shot_001.png"],
            "outputFolder": "2026-01-01/video",
        },
    }

    class FakeStore:
        def get_job(self, job_id: str) -> server.VideoJob | None:
            return job if job_id == job["jobId"] else None

        def signed_url(self, storage_reference: str) -> str:
            return f"https://signed.example/{storage_reference.rsplit('/', 1)[-1]}"

    monkeypatch.setattr(
        server.SupabaseStore,
        "from_env",
        classmethod(lambda cls: FakeStore()),
    )

    with _running_server() as base_url:
        with urlopen(f"{base_url}/api/jobs/restored-job", timeout=3) as response:
            restored_job = json.loads(response.read())

    assert restored_job["idea"] == "A story from yesterday."
    assert restored_job["result"]["videoUrl"] == "https://signed.example/video.mp4"


def test_frontend_routes_fall_back_to_the_spa_shell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "index.html").write_text("<main>youtubeAgent</main>", encoding="utf-8")
    monkeypatch.setattr(server, "FRONTEND_ROOT", tmp_path)

    with _running_server() as base_url:
        with urlopen(f"{base_url}/features", timeout=3) as response:
            assert response.status == 200
            assert response.read() == b"<main>youtubeAgent</main>"

        with pytest.raises(HTTPError) as error:
            urlopen(f"{base_url}/assets/missing.js", timeout=3)
        assert error.value.code == 404
