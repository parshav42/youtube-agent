import json
import logging
import mimetypes
import os
import threading
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TypedDict
from urllib.parse import quote, unquote, urlsplit

from dotenv import load_dotenv
from elevenlabs import ElevenLabs

if __package__:
    from .image_prompt_generator import LocalImagePromptGenerator
    from .supabase_store import StoredVideoJob, StoredVideoResult, SupabaseStore
    from .text_generator import OllamaTextGenerator
    from .video_generator import VideoGenerationCancelled, create_video
else:
    from image_prompt_generator import LocalImagePromptGenerator
    from supabase_store import StoredVideoJob, StoredVideoResult, SupabaseStore
    from text_generator import OllamaTextGenerator
    from video_generator import VideoGenerationCancelled, create_video

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = PROJECT_ROOT / "output"
FRONTEND_ROOT = PROJECT_ROOT / "frontend" / "dist"
MAX_REQUEST_SIZE = 64 * 1024


def _allowed_cors_origins() -> set[str]:
    return {
        origin.strip().rstrip("/")
        for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    }


VideoResult = StoredVideoResult


class VideoJob(StoredVideoJob):
    result: VideoResult | None


_JOBS: dict[str, VideoJob] = {}
_JOBS_LOCK = threading.Lock()


@dataclass
class JobControl:
    cancel_event: threading.Event
    update_lock: threading.Lock


_JOB_CONTROLS: dict[str, JobControl] = {}


def _update_job(
    job_id: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    message: str | None = None,
    error: str | None = None,
    result: VideoResult | None = None,
) -> None:
    with _JOBS_LOCK:
        control = _JOB_CONTROLS.get(job_id)
    if control is None:
        control = JobControl(threading.Event(), threading.Lock())

    with control.update_lock:
        with _JOBS_LOCK:
            job = _JOBS[job_id]
            if status == "complete" and control.cancel_event.is_set():
                raise VideoGenerationCancelled("Video generation was stopped.")
            if status is not None:
                job["status"] = status
            if stage is not None:
                job["stage"] = stage
            if message is not None:
                job["message"] = message
            if error is not None:
                job["error"] = error
            if result is not None:
                job["result"] = result
            job_snapshot = dict(job)
        store = SupabaseStore.from_env()
        if store is not None:
            store.save_job(job_snapshot)


def _get_job(job_id: str) -> VideoJob | None:
    store = SupabaseStore.from_env()
    if store is not None:
        job = store.get_job(job_id)
        if job is not None:
            with _JOBS_LOCK:
                current = _JOBS.get(job_id)
                control = _JOB_CONTROLS.get(job_id)
                if (
                    current is not None
                    and control is not None
                    and control.cancel_event.is_set()
                    and current["status"] in {"cancelling", "cancelled"}
                ):
                    job = current
                else:
                    _JOBS[job_id] = job
            return _with_signed_asset_urls(job, store)
        return None

    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job is not None else None


def _with_signed_asset_urls(job: VideoJob, store: SupabaseStore) -> VideoJob:
    result = job["result"]
    if result is None:
        return dict(job)
    signed_result: VideoResult = {
        **result,
        "videoUrl": store.signed_url(result["videoUrl"]),
        "dialogueUrl": store.signed_url(result["dialogueUrl"]),
        "audioUrl": store.signed_url(result["audioUrl"]),
        "imageUrls": [store.signed_url(path) for path in result["imageUrls"]],
    }
    return {**job, "result": signed_result}


def _output_url(path: Path) -> str:
    relative_path = path.relative_to(OUTPUT_ROOT).as_posix()
    return f"/output/{quote(relative_path, safe='/')}"


def _cancel_job(job_id: str) -> VideoJob | None:
    with _JOBS_LOCK:
        control = _JOB_CONTROLS.get(job_id)
    if control is None:
        return None

    with control.update_lock:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is None:
                return None
            if job["status"] not in {"queued", "running", "cancelling"}:
                return dict(job)
            control.cancel_event.set()
            job["status"] = "cancelling"
            job["message"] = "Stopping video generation..."
            job_snapshot = dict(job)
        store = SupabaseStore.from_env()
        if store is not None:
            store.save_job(job_snapshot)
        return job_snapshot


def _run_video_job(job_id: str, idea: str) -> None:
    with _JOBS_LOCK:
        control = _JOB_CONTROLS[job_id]

    def check_cancelled() -> None:
        if control.cancel_event.is_set():
            raise VideoGenerationCancelled("Video generation was stopped.")

    try:
        check_cancelled()
        api_key = os.getenv("ELEVENLABS_API_KEY")
        if not api_key:
            raise RuntimeError("Missing ELEVENLABS_API_KEY in the project .env file.")

        client = ElevenLabs(api_key=api_key, timeout=60)

        def report_progress(stage: str, message: str) -> None:
            check_cancelled()
            _update_job(job_id, status="running", stage=stage, message=message)

        video_path = create_video(
            prompt=idea,
            client=client,
            output_dir=OUTPUT_ROOT,
            image_prompt_generator=LocalImagePromptGenerator(),
            text_generator=OllamaTextGenerator(),
            progress_callback=report_progress,
            cancel_requested=control.cancel_event.is_set,
        )
        run_dir = video_path.parent
        image_paths = sorted((run_dir / "images").glob("shot_*.png"))
        store = SupabaseStore.from_env()
        if store is None:
            video_url = _output_url(video_path)
            dialogue_url = _output_url(run_dir / "dialogue.txt")
            audio_url = _output_url(run_dir / "narration.mp3")
            image_urls = [_output_url(path) for path in image_paths]
        else:
            assets = store.upload_assets(job_id, run_dir, check_cancelled)
            video_url = assets[video_path.relative_to(run_dir).as_posix()]
            dialogue_url = assets["dialogue.txt"]
            audio_url = assets["narration.mp3"]
            image_urls = [
                assets[path.relative_to(run_dir).as_posix()] for path in image_paths
            ]
        result: VideoResult = {
            "videoUrl": video_url,
            "dialogueUrl": dialogue_url,
            "audioUrl": audio_url,
            "imageUrls": image_urls,
            "outputFolder": run_dir.relative_to(OUTPUT_ROOT).as_posix(),
        }
        _update_job(
            job_id,
            status="complete",
            stage="complete",
            message="Your video is ready.",
            result=result,
        )
    except VideoGenerationCancelled:
        try:
            _update_job(
                job_id,
                status="cancelled",
                stage="cancelled",
                message="Video generation stopped.",
            )
        except RuntimeError:
            logging.exception("Could not save cancelled video job %s", job_id)
    except Exception as error:
        logging.exception("Video generation job %s failed", job_id)
        if control.cancel_event.is_set():
            try:
                _update_job(
                    job_id,
                    status="cancelled",
                    stage="cancelled",
                    message="Video generation stopped.",
                )
            except RuntimeError:
                logging.exception("Could not save cancelled video job %s", job_id)
            return
        with _JOBS_LOCK:
            job = _JOBS[job_id]
            job.update(
                status="failed",
                stage="failed",
                message="Video generation failed.",
                error=f"{type(error).__name__}: {error}",
            )
            job_snapshot = dict(job)
        try:
            store = SupabaseStore.from_env()
            if store is not None:
                store.save_job(job_snapshot)
        except RuntimeError:
            logging.exception("Could not save failed video job %s to Supabase", job_id)


class VideoRequestHandler(BaseHTTPRequestHandler):
    def end_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin and origin in _allowed_cors_origins():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Vary", "Origin")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        origin = self.headers.get("Origin")
        if not origin or origin not in _allowed_cors_origins():
            self.send_error(403, "Origin is not allowed.")
            return
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_json(self, status_code: int, payload: object) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path: Path) -> None:
        file_size = path.stat().st_size
        start = 0
        end = file_size - 1
        status_code = 200
        range_header = self.headers.get("Range")

        if range_header:
            try:
                unit, byte_range = range_header.split("=", 1)
                if unit != "bytes" or "," in byte_range or file_size == 0:
                    raise ValueError
                first, last = byte_range.split("-", 1)
                if not first:
                    suffix_length = int(last)
                    if suffix_length <= 0:
                        raise ValueError
                    start = max(0, file_size - suffix_length)
                else:
                    start = int(first)
                    end = min(int(last), file_size - 1) if last else file_size - 1
                    if start < 0 or start >= file_size or end < start:
                        raise ValueError
                status_code = 206
            except ValueError:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

        content_length = max(0, end - start + 1)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        if status_code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.end_headers()

        with path.open("rb") as file:
            file.seek(start)
            remaining = content_length
            while remaining:
                chunk = file.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_GET(self) -> None:
        request_path = urlsplit(self.path).path
        if request_path == "/health":
            self._send_json(200, {"status": "ok"})
            return

        if request_path.startswith("/api/jobs/"):
            job_id = request_path.removeprefix("/api/jobs/")
            try:
                job = _get_job(job_id) if job_id and "/" not in job_id else None
            except RuntimeError as error:
                self._send_json(503, {"error": str(error)})
                return
            if job is None:
                self._send_json(404, {"error": "Video job not found."})
                return
            self._send_json(200, job)
            return

        if request_path.startswith("/output/"):
            relative_path = unquote(request_path.removeprefix("/output/"))
            output_root = OUTPUT_ROOT.resolve()
            file_path = (output_root / relative_path).resolve()
            if not file_path.is_relative_to(output_root) or not file_path.is_file():
                self._send_json(404, {"error": "Output file not found."})
                return
            self._serve_file(file_path)
            return

        frontend_root = FRONTEND_ROOT.resolve()
        relative_path = "index.html" if request_path == "/" else unquote(request_path.lstrip("/"))
        file_path = (frontend_root / relative_path).resolve()
        if not file_path.is_relative_to(frontend_root):
            self._send_json(404, {"error": "Page not found."})
            return
        if not file_path.is_file() and request_path != "/" and not Path(relative_path).suffix:
            file_path = frontend_root / "index.html"
        if not file_path.is_file():
            if not (frontend_root / "index.html").is_file():
                self._send_json(
                    503,
                    {"error": "Build the frontend first with `npm --prefix frontend run build`."},
                )
                return
            self._send_json(404, {"error": "Page not found."})
            return
        self._serve_file(file_path)

    def do_POST(self) -> None:
        request_path = urlsplit(self.path).path
        if request_path.startswith("/api/jobs/") and request_path.endswith("/cancel"):
            job_id = request_path.removeprefix("/api/jobs/").removesuffix("/cancel").strip("/")
            if not job_id or "/" in job_id:
                self._send_json(404, {"error": "Video job not found."})
                return
            try:
                job = _cancel_job(job_id)
            except RuntimeError as error:
                self._send_json(503, {"error": str(error)})
                return
            if job is None:
                self._send_json(
                    404,
                    {"error": "This video job is not running on this server."},
                )
                return
            status_code = 202 if job["status"] == "cancelling" else 200
            self._send_json(status_code, job)
            return

        if request_path != "/api/videos":
            self._send_json(404, {"error": "Endpoint not found."})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "Invalid request size."})
            return
        if content_length <= 0 or content_length > MAX_REQUEST_SIZE:
            self._send_json(413, {"error": "Request must contain a story under 64 KB."})
            return

        try:
            payload = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "Request body must be valid JSON."})
            return
        idea = payload.get("idea") if isinstance(payload, dict) else None
        if not isinstance(idea, str) or not idea.strip():
            self._send_json(400, {"error": "Enter a video idea before generating."})
            return

        job_id = uuid.uuid4().hex
        job: VideoJob = {
            "jobId": job_id,
            "idea": idea.strip(),
            "status": "queued",
            "stage": "dialogue",
            "message": "Waiting to start...",
            "error": None,
            "result": None,
        }
        control = JobControl(threading.Event(), threading.Lock())
        try:
            store = SupabaseStore.from_env()
            if store is not None:
                store.save_job(job)
        except RuntimeError as error:
            self._send_json(503, {"error": str(error)})
            return
        with _JOBS_LOCK:
            _JOBS[job_id] = job
            _JOB_CONTROLS[job_id] = control
        threading.Thread(
            target=_run_video_job,
            args=(job_id, idea.strip()),
            daemon=True,
        ).start()
        self._send_json(202, job)


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("PORT", os.getenv("APP_PORT", "8000")))
    with ThreadingHTTPServer((host, port), VideoRequestHandler) as server:
        print(f"Video generator ready at http://{host}:{port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nVideo generator stopped.")
