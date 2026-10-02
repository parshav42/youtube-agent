"""Image provider selection tests."""

from app.video.image_generator import (
    ManualImageProvider,
    PollinationsImageProvider,
    build_image_provider,
)


def test_factory_returns_pollinations_when_selected(monkeypatch):
    monkeypatch.setenv("IMAGE_PROVIDER", "pollinations")

    assert isinstance(build_image_provider(), PollinationsImageProvider)


def test_factory_returns_manual_when_selected(monkeypatch):
    monkeypatch.setenv("IMAGE_PROVIDER", "manual")

    assert isinstance(build_image_provider(), ManualImageProvider)


def test_factory_defaults_to_manual_when_unset(monkeypatch):
    monkeypatch.delenv("IMAGE_PROVIDER", raising=False)

    assert isinstance(build_image_provider(), ManualImageProvider)