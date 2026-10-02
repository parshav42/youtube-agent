"""Manual and Pollinations image providers for vertical scene artwork."""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Protocol
from urllib.parse import quote
import uuid

from PIL import Image, UnidentifiedImageError

from app.config import (
    get_image_provider_name,
    get_licensed_asset_path,
    get_pollinations_api_key,
    get_pollinations_height,
    get_pollinations_model,
    get_pollinations_timeout_seconds,
    get_pollinations_width,
)
from app.schemas.script_schema import ScriptScene

SUPPORTED_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MIN_IMAGE_BYTES = 20 * 1024
POLLINATIONS_ENDPOINT = "https://image.pollinations.ai/prompt/"


class ImageGenerationError(RuntimeError):
    """An image provider failed to produce a validated image."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ImageProvider(Protocol):
    """Interface implemented by image-generation and manual providers."""

    provider_name: str

    def generate(self, prompt: str, output_path: Path) -> None:
        """Write one image for a prompt to the requested output path."""


class PollinationsImageProvider:
    """Generate images through the Pollinations prompt endpoint."""

    provider_name = "Pollinations AI"

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        width: int | None = None,
        height: int | None = None,
        timeout_seconds: int | None = None,
        max_attempts: int = 4,
        session: Any | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        try:
            import requests
        except ImportError as error:
            raise ImageGenerationError(
                "IMAGE_PROVIDER=pollinations requested but the requests library is missing from requirements.txt."
            ) from error
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")
        self.model = model if model is not None else get_pollinations_model()
        self.api_key = api_key if api_key is not None else get_pollinations_api_key()
        self.width = width if width is not None else get_pollinations_width()
        self.height = height if height is not None else get_pollinations_height()
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else get_pollinations_timeout_seconds()
        )
        self.max_attempts = max_attempts
        self._requests = requests
        self._session = session or requests
        self._sleep = sleep

    def generate(self, prompt: str, output_path: Path) -> None:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ImageGenerationError("Scene visual_description must not be empty.")
        encoded_prompt = quote(prompt, safe="")
        url = f"{POLLINATIONS_ENDPOINT}{encoded_prompt}"
        params: dict[str, str | int] = {
            "width": self.width,
            "height": self.height,
            "nologo": "true",
            "model": self.model,
        }
        if self.api_key:
            params["token"] = self.api_key

        for attempt in range(self.max_attempts):
            try:
                response = self._session.get(
                    url, params=params, timeout=self.timeout_seconds
                )
            except self._requests.exceptions.RequestException as error:
                raise ImageGenerationError(
                    "Pollinations request failed due to a network error."
                ) from error

            status = int(response.status_code)
            if status == 429 or status >= 500:
                if attempt + 1 < self.max_attempts:
                    close = getattr(response, "close", None)
                    if callable(close):
                        close()
                    self._sleep(15 * (2**attempt))
                    continue
                close = getattr(response, "close", None)
                if callable(close):
                    close()
                raise self._http_error(status)
            if status >= 400:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
                raise self._http_error(status)

            target: Path | None = None
            try:
                content = response.content
                with Image.open(BytesIO(content)) as image:
                    image_format = image.format
                    image.verify()
                extension = SUPPORTED_FORMATS.get(image_format or "")
                if extension is None:
                    raise ImageGenerationError(
                        "Pollinations returned an unsupported image format."
                    )
                target = Path(output_path).with_suffix(extension)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                if target.stat().st_size < MIN_IMAGE_BYTES:
                    raise ImageGenerationError(
                        f"Generated image is smaller than the {MIN_IMAGE_BYTES}-byte minimum."
                    )
                with Image.open(target) as image:
                    image.verify()
            except ImageGenerationError:
                _remove_if_present(target)
                raise
            except (OSError, UnidentifiedImageError, ValueError) as error:
                _remove_if_present(target)
                raise ImageGenerationError(
                    "Pollinations response was not a valid supported image."
                ) from error
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
            return

        raise self._http_error(503)

    @staticmethod
    def _http_error(status: int) -> ImageGenerationError:
        if status in {401, 403}:
            message = "Pollinations rejected the request. Check POLLINATIONS_API_KEY."
        elif status == 402:
            message = (
                "Pollinations returned HTTP 402 (access/payment required). Check POLLINATIONS_API_KEY "
                "and current free-tier availability."
            )
        elif status == 429:
            message = (
                "Pollinations rate limit reached. Free tier allows roughly one request every 15 seconds; "
                "retry later or set POLLINATIONS_API_KEY."
            )
        else:
            message = f"Pollinations image request failed with HTTP {status}."
        return ImageGenerationError(message, status_code=status)


class ManualImageProvider:
    """Require a user-provided image at the expected licensed-asset path."""

    provider_name = "manual licensed assets"

    def generate(self, prompt: str, output_path: Path) -> None:
        del prompt
        if not Path(output_path).is_file():
            raise ImageGenerationError(
                f"Manual image provider expects an existing licensed image at {output_path}. "
                "Set IMAGE_PROVIDER=pollinations to generate one automatically."
            )


def build_image_provider() -> ImageProvider:
    """Construct the explicitly configured image provider."""
    provider = get_image_provider_name()
    if provider == "pollinations":
        return PollinationsImageProvider()
    return ManualImageProvider()


def generate_scene_images(
    scenes: list[ScriptScene],
    *,
    library_path: str | Path | None = None,
    provider: ImageProvider | None = None,
    force: bool = False,
    scene_number: int | None = None,
) -> list[Path]:
    """Ensure scene images exist, recording provenance for Pollinations output."""
    library = Path(library_path) if library_path else get_licensed_asset_path()
    library.mkdir(parents=True, exist_ok=True)
    selected_provider = provider or build_image_provider()
    if force and isinstance(selected_provider, ManualImageProvider):
        raise ImageGenerationError(
            "Cannot regenerate an image with IMAGE_PROVIDER=manual; select pollinations."
        )

    selected_scenes = [
        scene for scene in scenes
        if scene_number is None or scene.scene_number == scene_number
    ]
    if scene_number is not None and not selected_scenes:
        raise ImageGenerationError(f"Script has no scene {scene_number}.")

    written: list[Path] = []
    for scene in selected_scenes:
        prompt = getattr(scene, "visual_description", None)
        if not isinstance(prompt, str) or not prompt.strip():
            raise ImageGenerationError(
                f"Scene {scene.scene_number} image generation failed using "
                f"{selected_provider.provider_name}: missing visual_description."
            )
        existing = _existing_scene_images(library, scene.scene_number)
        if existing and not force:
            written.append(existing[0])
            continue
        if isinstance(selected_provider, ManualImageProvider):
            expected_path = library / f"scene_{scene.scene_number:02d}.jpg"
            selected_provider.generate(prompt, expected_path)
            written.append(expected_path)
            continue

        temporary_base = library / f".scene_{scene.scene_number:02d}-{uuid.uuid4().hex}.jpg"
        try:
            selected_provider.generate(prompt, temporary_base)
            temporary_images = list(library.glob(f"{temporary_base.stem}.*"))
            if len(temporary_images) != 1:
                raise ImageGenerationError(
                    f"Provider did not write exactly one image for scene {scene.scene_number}."
                )
            generated = temporary_images[0]
            destination = library / f"scene_{scene.scene_number:02d}{generated.suffix.lower()}"
            os.replace(generated, destination)
            for old_image in existing:
                if old_image != destination:
                    old_image.unlink(missing_ok=True)
            _update_manifest(
                library / "LICENSES.json",
                destination.name,
                selected_provider,
                prompt,
            )
            written.append(destination)
        except Exception as error:
            for leftover in library.glob(f"{temporary_base.stem}.*"):
                leftover.unlink(missing_ok=True)
            cause = str(error) if isinstance(error, ImageGenerationError) else error.__class__.__name__
            raise ImageGenerationError(
                f"Scene {scene.scene_number} image generation failed using "
                f"{selected_provider.provider_name}: {cause}",
                status_code=getattr(error, "status_code", None),
            ) from error
    return written


def _existing_scene_images(library: Path, scene_number: int) -> list[Path]:
    return sorted(
        path for path in library.glob(f"scene_{scene_number:02d}.*")
        if path.suffix.lower() in SUPPORTED_EXTENSIONS and path.is_file()
    )


def _update_manifest(
    manifest_path: Path,
    filename: str,
    provider: ImageProvider,
    prompt: str,
) -> None:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    except (OSError, json.JSONDecodeError) as error:
        raise ImageGenerationError(f"Cannot read LICENSES.json: {error.__class__.__name__}") from error
    if not isinstance(manifest, dict):
        raise ImageGenerationError("LICENSES.json must be an object keyed by asset filename.")
    model = getattr(provider, "model", get_pollinations_model())
    manifest[filename] = {
        "source": "Pollinations AI (generated)",
        "license": "See https://pollinations.ai terms",
        "author": "Pollinations.AI",
        "rights_holder": "Pollinations.AI",
        "url": "https://pollinations.ai",
        "model": model,
        "prompt": prompt,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=manifest_path.parent,
            prefix=".LICENSES-",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(manifest, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, manifest_path)
    except OSError as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise ImageGenerationError(f"Cannot atomically update LICENSES.json: {error.__class__.__name__}") from error


def _remove_if_present(path: Any) -> None:
    if path is not None:
        Path(path).unlink(missing_ok=True)
