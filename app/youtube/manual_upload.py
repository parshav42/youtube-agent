"""Manual YouTube upload helpers for completed productions."""

import json
import re
import shutil
from pathlib import Path
from typing import Any

from app.database.db import get_production_by_id


def export_upload_package(
    production_id: str,
    database_path: str | Path | None = None,
) -> Path:
    """Copy a completed production and generated metadata into upload_package."""
    production = get_production_by_id(production_id, database_path)
    if production is None:
        raise ValueError(f"Production {production_id} was not found.")
    output_dir = Path(production["output_dir"]).resolve()
    final_video = output_dir / "final.mp4"
    if production["status"] not in {"ready_for_manual_upload", "completed", "published"}:
        raise ValueError("Only a finished production can be exported.")
    if not final_video.is_file():
        raise ValueError("The final.mp4 artifact is missing.")
    metadata = production.get("metadata", {}).get("youtube_metadata")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("title"), str):
        raise ValueError("Generated YouTube metadata is missing or invalid.")
    description = metadata.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("Generated YouTube description is missing or invalid.")
    tags = metadata.get("tags") or [
        str(tag).lstrip("#") for tag in metadata.get("hashtags", [])
    ]
    if not isinstance(tags, list):
        raise ValueError("Generated YouTube tags are invalid.")

    package_dir = output_dir / "upload_package"
    package_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(final_video, package_dir / "final.mp4")
    (package_dir / "title.txt").write_text(metadata["title"].strip() + "\n", encoding="utf-8")
    (package_dir / "description.txt").write_text(description.strip() + "\n", encoding="utf-8")
    (package_dir / "tags.txt").write_text(", ".join(str(tag).strip() for tag in tags) + "\n", encoding="utf-8")
    (package_dir / "README.txt").write_text(
        "Manual YouTube upload\n"
        "1. Upload final.mp4 to the intended YouTube channel.\n"
        "2. Copy title.txt, description.txt, and tags.txt into the upload details.\n"
        "3. Set the video visibility to Public, then publish it.\n"
        "4. Open the production review page and save the resulting YouTube URL.\n",
        encoding="utf-8",
    )
    thumbnail = _thumbnail_path(output_dir, metadata)
    if thumbnail is not None:
        shutil.copy2(thumbnail, package_dir / "thumbnail.jpg")
    return package_dir


def extract_youtube_video_id(url: str) -> str:
    """Extract a YouTube video ID from a supported canonical URL."""
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url.strip())
    host = parsed.hostname.casefold() if parsed.hostname else ""
    if host in {"youtu.be", "www.youtu.be"}:
        candidate = parsed.path.strip("/").split("/")[0]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            candidate = parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith(("/shorts/", "/embed/", "/live/")):
            candidate = parsed.path.split("/", 2)[2]
        else:
            candidate = ""
    else:
        candidate = ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
        raise ValueError("Enter a valid YouTube video URL.")
    return candidate


def _thumbnail_path(output_dir: Path, metadata: dict[str, Any]) -> Path | None:
    candidates = [
        metadata.get("thumbnail_path"),
        metadata.get("thumbnail"),
        output_dir / "thumbnail.jpg",
        output_dir / "assets" / "thumbnail.jpg",
    ]
    for candidate in candidates:
        if isinstance(candidate, str):
            path = Path(candidate)
        elif isinstance(candidate, Path):
            path = candidate
        else:
            continue
        if path.is_file():
            return path
    return None