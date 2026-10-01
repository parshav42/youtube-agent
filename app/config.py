"""Application configuration loaded from the environment and an optional .env file."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NICHE = "Hindi English discipline and mindset"
DEFAULT_TOPIC = "Discipline and consistency"

load_dotenv(PROJECT_ROOT / ".env")


def get_niche(override: str | None = None) -> str:
    """Return the requested niche, environment value, or documented default."""
    niche = override if override is not None else os.getenv("MOTIVATIONOS_NICHE", DEFAULT_NICHE)
    niche = niche.strip()
    if not niche:
        raise ValueError("The research niche must not be empty.")
    return niche


def get_database_path() -> Path:
    """Resolve the SQLite path, keeping relative paths inside the project."""
    configured_path = os.getenv("MOTIVATIONOS_DB_PATH", "data/motivationos.sqlite3")
    path = Path(configured_path).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def get_topic(override: str | None = None) -> str:
    """Return the requested script topic, configured topic, or default."""
    topic = override if override is not None else os.getenv("MOTIVATIONOS_TOPIC", DEFAULT_TOPIC)
    topic = topic.strip()
    if not topic:
        raise ValueError("The script topic must not be empty.")
    return topic


def get_quality_threshold() -> float:
    """Return the configured heuristic quality threshold."""
    try:
        threshold = float(os.getenv("MOTIVATIONOS_QUALITY_THRESHOLD", "7.5"))
    except ValueError as error:
        raise ValueError("MOTIVATIONOS_QUALITY_THRESHOLD must be a number from 0 to 10.") from error
    if not 0 <= threshold <= 10:
        raise ValueError("MOTIVATIONOS_QUALITY_THRESHOLD must be between 0 and 10.")
    return threshold


def get_speaking_rate_wps() -> float:
    """Return the configured expected narration rate in words per second."""
    try:
        rate = float(os.getenv("MOTIVATIONOS_SPEAKING_RATE_WPS", "2.5"))
    except ValueError as error:
        raise ValueError("MOTIVATIONOS_SPEAKING_RATE_WPS must be a number from 1 to 5.") from error
    if not 1 <= rate <= 5:
        raise ValueError("MOTIVATIONOS_SPEAKING_RATE_WPS must be between 1 and 5.")
    return rate


def get_production_output_path() -> Path:
    """Resolve the production output directory."""
    configured_path = os.getenv("MOTIVATIONOS_OUTPUT_DIR", "data/productions")
    path = Path(configured_path).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def get_licensed_asset_path() -> Path:
    """Resolve the configured library of user-supplied licensed assets."""
    configured_path = os.getenv("MOTIVATIONOS_LICENSED_ASSET_DIR", "data/licensed-assets")
    path = Path(configured_path).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def get_youtube_visibility() -> str:
    """Return the configured default visibility; publishing still needs explicit approval."""
    visibility = os.getenv("MOTIVATIONOS_YOUTUBE_VISIBILITY", "private").strip().lower()
    if visibility not in {"private", "unlisted", "public"}:
        raise ValueError("MOTIVATIONOS_YOUTUBE_VISIBILITY must be private, unlisted, or public.")
    return visibility


def get_youtube_callback_uri() -> str:
    """Return the exact OAuth callback registered in the Google Cloud project."""
    callback_uri = os.getenv(
        "GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/api/youtube/auth/callback"
    ).strip()
    if not callback_uri.startswith("http://127.0.0.1:") and not callback_uri.startswith(
        "http://localhost:"
    ):
        raise ValueError("Google OAuth callback must use a localhost HTTP redirect URI.")
    return callback_uri


def get_report_output_path() -> Path:
    """Resolve the local daily-report directory."""
    configured_path = os.getenv("MOTIVATIONOS_REPORT_DIR", "data/reports")
    path = Path(configured_path).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def get_hf_settings() -> dict[str, str]:
    """Return non-secret Hugging Face model/provider settings."""
    return {
        "model": os.getenv("HF_MODEL", "").strip(),
        "provider": os.getenv("HF_PROVIDER", "auto").strip() or "auto",
    }