"""Acquire approved visuals only from a local, provenance-documented asset library."""

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from app.config import get_licensed_asset_path
from app.schemas.script_schema import ScriptScene

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


class VisualAssetError(RuntimeError):
    """Raised when no usable, documented licensed scene asset is available."""


class LicensedLocalAssetManager:
    """Copy scene_NN images from a user-managed library with a LICENSES.json manifest."""

    def __init__(self, library_path: str | Path | None = None) -> None:
        self.library_path = Path(library_path) if library_path else get_licensed_asset_path()

    def acquire(self, scenes: list[ScriptScene], destination: str | Path) -> list[dict[str, Any]]:
        library = self.library_path.expanduser().resolve()
        manifest_path = library / "LICENSES.json"
        if not library.is_dir() or not manifest_path.is_file():
            raise VisualAssetError(
                f"Licensed visual library unavailable at {library}. Add scene_01.jpg etc. and "
                "a LICENSES.json manifest with source, license, and rights_holder for each file. "
                "No web scraping or unlicensed fallback is used."
            )
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise VisualAssetError(f"Cannot read visual asset manifest {manifest_path}: {error}") from error
        if not isinstance(manifest, dict):
            raise VisualAssetError("LICENSES.json must be an object keyed by asset filename.")

        output = Path(destination)
        output.mkdir(parents=True, exist_ok=True)
        acquired = []
        for scene in scenes:
            candidates = sorted(
                path
                for path in library.glob(f"scene_{scene.scene_number:02d}.*")
                if path.suffix.lower() in SUPPORTED_EXTENSIONS
            )
            if not candidates:
                raise VisualAssetError(
                    f"No supported licensed image found for scene {scene.scene_number}; "
                    f"expected scene_{scene.scene_number:02d}.jpg/.png/.webp."
                )
            source = candidates[0].resolve()
            if source.parent != library:
                raise VisualAssetError("Scene asset path escapes the configured licensed library.")
            provenance = manifest.get(source.name)
            has_source_and_license = isinstance(provenance, dict) and all(
                isinstance(provenance.get(field), str) and provenance[field].strip()
                for field in ("source", "license")
            )
            is_pollinations = (
                has_source_and_license
                and provenance.get("source") == "Pollinations AI (generated)"
                and all(
                    isinstance(provenance.get(field), str) and provenance[field].strip()
                    for field in ("author", "url", "model", "prompt", "generated_at")
                )
            )
            has_manual_rights_holder = (
                has_source_and_license
                and isinstance(provenance.get("rights_holder"), str)
                and bool(provenance["rights_holder"].strip())
            )
            if not (is_pollinations or has_manual_rights_holder):
                raise VisualAssetError(
                    f"Asset {source.name} needs source, license, and rights_holder metadata, "
                    "or complete Pollinations provenance, in LICENSES.json."
                )
            try:
                with Image.open(source) as image:
                    image.verify()
                with Image.open(source) as image:
                    width, height = image.size
                    image_format = image.format
            except (OSError, UnidentifiedImageError) as error:
                raise VisualAssetError(f"Invalid visual asset {source.name}: {error}") from error
            if width < 320 or height < 320 or image_format not in {"JPEG", "PNG", "WEBP"}:
                raise VisualAssetError(
                    f"Visual asset {source.name} has unsupported format or dimensions {width}x{height}."
                )
            target = output / f"scene_{scene.scene_number:02d}{source.suffix.lower()}"
            shutil.copy2(source, target)
            acquired.append(
                {
                    "scene_number": scene.scene_number,
                    "path": str(target.resolve()),
                    "visual_description": scene.visual_description,
                    "source": provenance["source"],
                    "license": provenance["license"],
                    "rights_holder": provenance.get("rights_holder", provenance.get("author", "")),
                    "attribution": provenance.get("attribution", ""),
                    "width": width,
                    "height": height,
                    "format": image_format,
                    "sha256": _sha256(target),
                }
            )
        return acquired


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as asset:
        for chunk in iter(lambda: asset.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()