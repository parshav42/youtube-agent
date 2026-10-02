"""Offline tests for the Pollinations image provider."""

from io import BytesIO
from urllib.parse import quote

from PIL import Image
import pytest

from app.video.image_generator import (
    ImageGenerationError,
    POLLINATIONS_ENDPOINT,
    PollinationsImageProvider,
)


def _image_bytes(*, noisy=True):
    image = (
        Image.effect_noise((400, 400), 90).convert("RGB")
        if noisy
        else Image.new("RGB", (400, 400), "white")
    )
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class FakeResponse:
    def __init__(self, status_code, content=b"", content_type="image/png"):
        self.status_code = status_code
        self.content = content
        self.headers = {"Content-Type": content_type}

    def close(self):
        pass


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def test_pollinations_builds_encoded_url_and_expected_parameters(tmp_path):
    prompt = "Vertical 9:16, student at a desk / morning light"
    session = FakeSession([FakeResponse(200, _image_bytes())])
    provider = PollinationsImageProvider(
        model="flux-test",
        api_key="test-token",
        width=1080,
        height=1920,
        timeout_seconds=37,
        session=session,
        sleep=lambda delay: None,
    )

    provider.generate(prompt, tmp_path / "scene_01.jpg")

    url, request = session.calls[0]
    assert url == POLLINATIONS_ENDPOINT + quote(prompt, safe="")
    assert request["params"] == {
        "width": 1080,
        "height": 1920,
        "nologo": "true",
        "model": "flux-test",
        "token": "test-token",
    }
    assert request["timeout"] == 37
    assert (tmp_path / "scene_01.png").is_file()


@pytest.mark.parametrize("statuses", [[429, 500], [503, 503]])
def test_pollinations_retries_rate_limit_and_server_errors(tmp_path, statuses):
    session = FakeSession(
        [*(FakeResponse(status) for status in statuses), FakeResponse(200, _image_bytes())]
    )
    delays = []
    provider = PollinationsImageProvider(
        session=session,
        sleep=delays.append,
        max_attempts=len(statuses) + 1,
    )

    provider.generate("A student studying", tmp_path / "scene.jpg")

    assert len(session.calls) == len(statuses) + 1
    assert delays == [15 * (2**index) for index in range(len(statuses))]


def test_pollinations_stops_after_configured_rate_limit_attempts():
    session = FakeSession([FakeResponse(429) for _ in range(3)])
    delays = []
    provider = PollinationsImageProvider(
        session=session,
        sleep=delays.append,
        max_attempts=3,
    )

    with pytest.raises(ImageGenerationError, match="roughly one request every 15 seconds") as error:
        provider.generate("A student studying", __import__("pathlib").Path("unused.jpg"))

    assert error.value.status_code == 429
    assert len(session.calls) == 3
    assert delays == [15, 30]


def test_pollinations_stops_after_configured_server_error_attempts():
    session = FakeSession([FakeResponse(503), FakeResponse(503)])
    delays = []
    provider = PollinationsImageProvider(
        session=session,
        sleep=delays.append,
        max_attempts=2,
    )

    with pytest.raises(ImageGenerationError, match="HTTP 503") as error:
        provider.generate("A student studying", __import__("pathlib").Path("unused.jpg"))

    assert error.value.status_code == 503
    assert len(session.calls) == 2
    assert delays == [15]


def test_pollinations_non_retryable_4xx_exposes_status_without_secret():
    secret = "do-not-print-this-token"
    session = FakeSession([FakeResponse(403)])
    provider = PollinationsImageProvider(
        api_key=secret,
        session=session,
        sleep=lambda delay: None,
    )

    with pytest.raises(ImageGenerationError, match="Check POLLINATIONS_API_KEY") as error:
        provider.generate("A student studying", __import__("pathlib").Path("unused.jpg"))

    assert error.value.status_code == 403
    assert secret not in str(error.value)
    assert len(session.calls) == 1


def test_pollinations_402_explains_access_blocker():
    provider = PollinationsImageProvider(
        session=FakeSession([FakeResponse(402)]),
        sleep=lambda delay: None,
    )

    with pytest.raises(ImageGenerationError, match="current free-tier availability") as error:
        provider.generate("A student studying", __import__("pathlib").Path("unused.jpg"))

    assert error.value.status_code == 402


def test_pollinations_writes_image_and_removes_invalid_output(tmp_path):
    valid_session = FakeSession([FakeResponse(200, _image_bytes())])
    PollinationsImageProvider(session=valid_session, sleep=lambda delay: None).generate(
        "A student studying", tmp_path / "valid.jpg"
    )
    assert (tmp_path / "valid.png").is_file()

    invalid_session = FakeSession([FakeResponse(200, _image_bytes(noisy=False))])
    with pytest.raises(ImageGenerationError, match="smaller than"):
        PollinationsImageProvider(session=invalid_session, sleep=lambda delay: None).generate(
            "A student studying", tmp_path / "invalid.jpg"
        )
    assert not (tmp_path / "invalid.png").exists()


def test_pollinations_omits_token_when_no_api_key_is_set(tmp_path, monkeypatch):
    monkeypatch.delenv("POLLINATIONS_API_KEY", raising=False)
    session = FakeSession([FakeResponse(200, _image_bytes())])
    PollinationsImageProvider(session=session, sleep=lambda delay: None).generate(
        "A student studying", tmp_path / "scene.jpg"
    )

    assert "token" not in session.calls[0][1]["params"]