"""Offline tests for Pixabay image search and provenance handling."""

from io import BytesIO
import json
from pathlib import Path

from PIL import Image
import pytest

from app.schemas.script_schema import ScriptScene
from app.video.image_generator import (
    ImageGenerationError,
    PIXABAY_ENDPOINT,
    PixabayImageProvider,
    generate_scene_images,
)
from app.video.visual_asset_manager import LicensedLocalAssetManager, VisualAssetError


def _portrait_image_bytes():
    image = Image.effect_noise((400, 800), 90).convert("RGB")
    output = BytesIO()
    image.save(output, format="JPEG")
    return output.getvalue()


class FakeResponse:
    def __init__(self, status_code, *, payload=None, content=b""):
        self.status_code = status_code
        self._payload = payload
        self.content = content

    def json(self):
        return self._payload

    def close(self):
        pass


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def _hit():
    return {
        "largeImageURL": "https://pixabay.com/get/portrait.jpg",
        "pageURL": "https://pixabay.com/photos/study-desk-123456/",
        "user": "ExampleCreator",
        "id": 123456,
    }


def test_pixabay_search_downloads_first_portrait_result(tmp_path):
    session = FakeSession(
        [
            FakeResponse(200, payload={"hits": [_hit(), {**_hit(), "id": 2}]}),
            FakeResponse(200, content=_portrait_image_bytes()),
        ]
    )
    provider = PixabayImageProvider(api_key="test-key", session=session)
    target = tmp_path / "scene_01.jpg"

    provider.generate("student studying at a desk", target)

    search_url, request = session.calls[0]
    assert search_url == PIXABAY_ENDPOINT
    assert request["params"] == {
        "key": "test-key",
        "q": "student studying at a desk",
        "orientation": "vertical",
        "image_type": "photo",
        "per_page": 3,
    }
    assert session.calls[1][0] == _hit()["largeImageURL"]
    with Image.open(target) as image:
        assert image.height > image.width


def test_pixabay_empty_results_raise_clear_error():
    provider = PixabayImageProvider(
        api_key="test-key",
        session=FakeSession([FakeResponse(200, payload={"hits": []})]),
    )

    with pytest.raises(ImageGenerationError, match="found no images"):
        provider.generate("unusual scene", Path("unused.jpg"))


@pytest.mark.parametrize(
    ("status_code", "message"),
    [
        (401, "PIXABAY_API_KEY"),
        (429, "rate limit"),
        (500, "Retry later"),
    ],
)
def test_pixabay_http_errors_are_actionable(status_code, message):
    provider = PixabayImageProvider(
        api_key="test-key",
        session=FakeSession([FakeResponse(status_code)]),
    )

    with pytest.raises(ImageGenerationError, match=message) as error:
        provider.generate("student studying", Path("unused.jpg"))

    assert error.value.status_code == status_code


def test_pixabay_manifest_has_complete_provenance_and_passes_asset_gate(tmp_path):
    scene = ScriptScene(
        scene_number=1,
        duration_seconds=8,
        narration="Keep going.",
        visual_description="A student studying at a desk.",
        caption="Keep going",
        transition="cut",
        audio_direction="calm",
    )
    provider = PixabayImageProvider(
        api_key="test-key",
        session=FakeSession(
            [
                FakeResponse(200, payload={"hits": [_hit()]}),
                FakeResponse(200, content=_portrait_image_bytes()),
            ]
        ),
    )
    library = tmp_path / "assets"

    written = generate_scene_images([scene], library_path=library, provider=provider)

    manifest = json.loads((library / "LICENSES.json").read_text(encoding="utf-8"))
    provenance = manifest[written[0].name]
    assert provenance == {
        "source": "Pixabay",
        "url": _hit()["pageURL"],
        "user": "ExampleCreator",
        "license": "Pixabay Content License",
        "rights_holder": "ExampleCreator",
        "pixabay_id": 123456,
    }
    acquired = LicensedLocalAssetManager(library).acquire([scene], tmp_path / "output")
    assert acquired[0]["source"] == "Pixabay"
    assert acquired[0]["url"] == _hit()["pageURL"]
    assert acquired[0]["user"] == "ExampleCreator"
    assert acquired[0]["pixabay_id"] == 123456


def test_incomplete_pixabay_provenance_does_not_fall_back_to_manual_rule(tmp_path):
    library = tmp_path / "assets"
    library.mkdir()
    (library / "scene_01.jpg").write_bytes(_portrait_image_bytes())
    (library / "LICENSES.json").write_text(
        json.dumps(
            {
                "scene_01.jpg": {
                    "source": "Pixabay",
                    "url": _hit()["pageURL"],
                    "license": "Pixabay Content License",
                    "rights_holder": "ExampleCreator",
                }
            }
        ),
        encoding="utf-8",
    )
    scene = ScriptScene(
        scene_number=1,
        duration_seconds=8,
        narration="Keep going.",
        visual_description="A student studying at a desk.",
        caption="Keep going",
        transition="cut",
        audio_direction="calm",
    )

    with pytest.raises(VisualAssetError, match="needs source, license, and rights_holder"):
        LicensedLocalAssetManager(library).acquire([scene], tmp_path / "output")