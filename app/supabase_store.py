import mimetypes
import os
from pathlib import Path
from typing import BinaryIO, Callable, TypedDict
from urllib.parse import quote, urlsplit

import requests


class StoredVideoResult(TypedDict):
    videoUrl: str
    dialogueUrl: str
    audioUrl: str
    imageUrls: list[str]
    outputFolder: str


class StoredVideoJob(TypedDict):
    jobId: str
    idea: str
    status: str
    stage: str
    message: str
    error: str | None
    result: StoredVideoResult | None


class SupabaseStore:
    def __init__(self, url: str, secret_key: str, timeout: float = 15) -> None:
        self.url = url.rstrip("/")
        self.secret_key = secret_key
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "SupabaseStore | None":
        url = os.getenv("SUPABASE_URL") or os.getenv("supabase_url")
        key = (
            os.getenv("SUPABASE_SECRET_KEY")
            or os.getenv("supabase_sec")
            or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        )
        publishable_key = (
            os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("supabase_pub")
        )
        if not url and not key and not publishable_key:
            return None
        if not url or not key:
            raise RuntimeError(
                "Set SUPABASE_URL and SUPABASE_SECRET_KEY (or supabase_sec) "
                "to enable Supabase storage."
            )
        return cls(url, key)

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self.secret_key,
            "Authorization": f"Bearer {self.secret_key}",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json: object | None = None,
        data: BinaryIO | None = None,
        timeout: float | None = None,
    ) -> requests.Response:
        try:
            response = requests.request(
                method,
                f"{self.url}{path}",
                headers={**self._headers(), **(headers or {})},
                timeout=self.timeout if timeout is None else timeout,
                json=json,
                data=data,
            )
            response.raise_for_status()
            return response
        except requests.HTTPError as error:
            response = error.response
            detail = ""
            if response is not None:
                try:
                    payload = response.json()
                except ValueError:
                    detail = response.text.strip()
                else:
                    if isinstance(payload, dict):
                        detail = str(
                            payload.get("message")
                            or payload.get("details")
                            or payload.get("hint")
                            or ""
                        )
            status = response.status_code if response is not None else "unknown status"
            message = f"Supabase request failed ({status})"
            if detail:
                message = f"{message}: {detail}"
            raise RuntimeError(message) from error
        except requests.RequestException as error:
            raise RuntimeError(f"Supabase request failed: {error}") from error

    def save_job(self, job: StoredVideoJob) -> None:
        row = {
            "id": job["jobId"],
            "idea": job["idea"],
            "status": job["status"],
            "stage": job["stage"],
            "message": job["message"],
            "error": job["error"],
            "result": job["result"],
        }
        self._request(
            "POST",
            "/rest/v1/video_jobs?on_conflict=id",
            json=row,
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )

    def get_job(self, job_id: str) -> StoredVideoJob | None:
        response = self._request(
            "GET",
            f"/rest/v1/video_jobs?id=eq.{quote(job_id, safe='')}&select=*",
        )
        rows = response.json()
        if not rows:
            return None
        row = rows[0]
        return {
            "jobId": row["id"],
            "idea": row["idea"],
            "status": row["status"],
            "stage": row["stage"],
            "message": row["message"],
            "error": row["error"],
            "result": row["result"],
        }

    def upload_assets(
        self,
        job_id: str,
        run_dir: Path,
        check_cancelled: Callable[[], None] | None = None,
    ) -> dict[str, str]:
        assets: dict[str, str] = {}
        for path in sorted(file for file in run_dir.rglob("*") if file.is_file()):
            if check_cancelled is not None:
                check_cancelled()
            relative_path = path.relative_to(run_dir).as_posix()
            object_path = f"{job_id}/{relative_path}"
            encoded_path = quote(object_path, safe="/")
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            with path.open("rb") as file:
                self._request(
                    "POST",
                    f"/storage/v1/object/video-assets/{encoded_path}",
                    data=file,
                    headers={"Content-Type": content_type, "x-upsert": "true"},
                    timeout=120,
                )
            if check_cancelled is not None:
                check_cancelled()
            assets[relative_path] = f"supabase://video-assets/{object_path}"
        return assets

    def signed_url(self, storage_reference: str) -> str:
        parsed = urlsplit(storage_reference)
        if parsed.scheme != "supabase" or not parsed.netloc or not parsed.path.strip("/"):
            raise ValueError(f"Invalid Supabase storage reference: {storage_reference}")
        bucket = quote(parsed.netloc, safe="")
        object_path = quote(parsed.path.lstrip("/"), safe="/")
        response = self._request(
            "POST",
            f"/storage/v1/object/sign/{bucket}/{object_path}",
            json={"expiresIn": 3600},
        )
        signed_url = response.json()["signedURL"]
        if signed_url.startswith("http://") or signed_url.startswith("https://"):
            return signed_url
        return f"{self.url}/storage/v1{signed_url}"
