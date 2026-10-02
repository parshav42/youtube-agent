"""Image provider selection tests."""

from app.video.image_generator import (
    ManualImageProvider,
    PixabayImageProvider,
    PollinationsImageProvider,
    build_image_provider,
)
from app.config import get_pixabay_api_key


def test_factory_returns_pollinations_when_selected(monkeypatch):
    monkeypatch.setenv("IMAGE_PROVIDER", "pollinations")

    assert isinstance(build_image_provider(), PollinationsImageProvider)


def test_factory_returns_manual_when_selected(monkeypatch):
    monkeypatch.setenv("IMAGE_PROVIDER", "manual")

    assert isinstance(build_image_provider(), ManualImageProvider)


def test_factory_returns_pixabay_when_selected(monkeypatch):
    monkeypatch.setenv("IMAGE_PROVIDER", "pixabay")

    assert isinstance(build_image_provider(), PixabayImageProvider)


def test_factory_defaults_to_manual_when_unset(monkeypatch):
    monkeypatch.delenv("IMAGE_PROVIDER", raising=False)

    assert isinstance(build_image_provider(), ManualImageProvider)


def test_pixabay_api_key_is_read_from_environment(monkeypatch):
    monkeypatch.setenv("PIXABAY_API_KEY", "test-key")

    assert get_pixabay_api_key() == "test-key"